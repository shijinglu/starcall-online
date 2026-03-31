# Full Data Flow: Voice Input → Agent → TTS Playback

## 1. iOS: Mic Capture → WebSocket

**`AudioCaptureEngine.swift`** captures mic at 44.1kHz, downsamples to **16kHz int16 mono**, and produces **100ms chunks** (3200 bytes). Hardware AEC is enabled via `.voiceChat` audio session mode.

**`ConversationSession.swift`** gates outbound audio: if TTS is playing (`isPlaying=true`), chunks are **not sent** (echo suppression). Otherwise:

**`WebSocketTransport.swift`** wraps each chunk in a **4-byte header** and sends as a binary WebSocket frame:

```
[0x01=AUDIO_CHUNK] [0x00=user] [gen_id] [frame_seq] [PCM payload...]
```

## 2. Backend: WebSocket → Gemini Live API

**`ws/handler.py`** receives the binary frame at `/api/v1/conversation/live`, decodes the 4-byte header via `codec.py`, and enqueues the PCM into `session.audio_queue`.

**`gemini_proxy.py`** runs two async loops:
- **Send loop** (`_audio_send_loop`): dequeues from `audio_queue`, forwards to Gemini via `send_realtime_input(audio=Blob(pcm, "audio/pcm;rate=16000"))`
- **Receive loop** (`_response_receive_loop`): processes Gemini events

## 3. Gemini Responds (3 possible outputs)

### 3a. Direct voice reply (fast path)
Gemini generates **audio TTS** directly (`response.data` = 16kHz PCM). Backend sends it to iOS immediately:
```
[0x02=AUDIO_RESPONSE] [0x00=moderator] [gen_id] [seq] [PCM...]
```

### 3b. Transcriptions
User/moderator speech text arrives as `input_transcription` / `output_transcription`. Backend accumulates in `TranscriptBuffer` and emits JSON:
```json
{"type": "transcript", "speaker": "user", "text": "...", "is_final": true}
```

### 3c. Tool call → Agent dispatch (slow path)
Gemini decides a domain expert is needed and calls:
```
dispatch_agent(name="ellen", task="Check my calendar for Thursday")
```

## 4. Agent Dispatch & Execution

**`gemini_proxy.py`** `_handle_tool_call()` routes to **`agent_task_manager.py`** `dispatch()`:

1. Creates an `AgentSession` (UUID, status="active")
2. Emits `{"type": "agent_status", "status": "thinking"}` to iOS (shows spinner)
3. Starts **heartbeat** (every 2s: `{"type": "agent_status", "elapsed_ms": ...}`)
4. Spawns `_run_agent()` with **30s timeout**, limited by semaphore (max 8 concurrent)

**`sdk_agent_runner.py`** `run()` calls the **Claude Agent SDK**:
```python
options = ClaudeAgentOptions(
    model="claude-opus-4.1",
    system_prompt=agent_persona,   # from prompts/ellen.md etc.
    mcp_servers={agent_tools},      # agent-specific tool set
    max_turns=5,
    max_budget_usd=0.50,
)
async for message in query(prompt=task, options=options):
    # processes tool calls, thinking, final result
```

Claude runs its tools (e.g., calendar lookup, fraud check), reasons through the task, and returns a text result.

## 5. TTS Synthesis (Agent Response)

**`tts_service.py`** sends the agent's text to **Google Cloud TTS**:
- Voice: per-agent voice ID from registry
- Format: 16kHz LINEAR16 PCM
- Retry: 2 attempts, 0.5s between failures

## 6. Audio Delivery to iOS

**`agent_task_manager.py`** `_deliver_or_queue()`:

- **Single agent**: sends immediately via `send_agent_audio()`:
  ```
  [0x03=AGENT_AUDIO] [agent_speaker_id] [gen_id] [seq] [PCM...]
  ```
- **Meeting mode** (multiple agents): buffers in `audio_buffer`, a background `_meeting_mode_audio_sender()` drains the queue sequentially (one agent at a time)

Emits `{"type": "agent_status", "status": "done"}` when complete.

## 7. iOS: Receive → Playback

**`ConversationSession.swift`** `transportDidReceiveBinaryFrame()` parses the 4-byte header.

**`AudioPlaybackEngine.swift`** `receiveAudioFrame()`:
1. **Zombie filter**: compares `frame.genId` vs `currentGen` using RFC 1982 modular arithmetic — stale frames are silently discarded
2. Converts PCM to `AVAudioPCMBuffer` (16kHz, int16, mono)
3. Schedules on the speaker's `AVAudioPlayerNode` (one per speaker_id)
4. Meeting mode: buffers frames per speaker, plays sequentially

When playback starts, `isPlaying → true` → **capture engine gates outbound chunks** (echo loop prevention).

## 8. Barge-in (User Interrupts)

```
iOS: RMS spike detected (noiseFloor + 25dB threshold)
  → audioCaptureDidDetectBargein()
  → handleBargein(): increment currentGen, flush playback, isPlaying=false
  → Send {"type": "interrupt", "mode": "cancel_all"}

Backend: increment session.gen_id, clear agent audio buffers
  → Send {"type": "interruption", "gen_id": new_gen}
  → All subsequent frames with old gen_id are discarded as zombies
```

## Visual Summary

```
┌────────────────────────────────────────────────────────────────────┐
│  iOS App                                                           │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────────┐          │
│  │ Mic 44kHz├──►│CaptureEngine ├──►│ WebSocketTransport │──── WS ──┼──┐
│  │          │   │ ↓16kHz 100ms │   │ [01][00][gen][seq] │          │  │
│  └──────────┘   │ RMS→barge-in │   └────────────────────┘          │  │
│                 └──────────────┘                                   │  │
│  ┌──────────┐    ┌──────────────┐                                  │  │
│  │ Speaker  │◄──│PlaybackEngine│◄─── binary frames ────────────────┼──┤
│  │          │   │ zombie filter │                                  │  │
│  └──────────┘   │ meeting queue │                                  │  │
│                  └──────────────┘                                  │  │
└────────────────────────────────────────────────────────────────────┘  │
                                                                        │
┌───────────────────────────────────────────────────────────────────────┤
│  Backend (FastAPI)                                                    │
│  ┌──────────┐   ┌────────────────┐    ┌─────────────────┐             │
│  │ws/handler├──►│ GeminiLiveProxy├──►│  Gemini Live API │             │
│  │decode    │   │ send_loop      │   │  (voice + tools) │             │
│  │frames    │   │ receive_loop   │   └────────┬─────────┘             │
│  └──────────┘   └───────┬────────┘            │                       │
│                         │ tool_call           │ audio/transcript      │
│                         ▼                     ▼                       │
│  ┌──────────────────┐  ┌──────────────┐  [0x02] frames → iOS          │
│  │AgentTaskManager  │  │              │                               │
│  │ dispatch/resume  ├─►│SDKAgentRunner│                               │
│  │ meeting_queue    │  │ Claude SDK   │                               │
│  │ heartbeat        │  │ MCP tools    │                               │
│  └────────┬─────────┘  └──────┬───────┘                               │
│           │                   │ text result                           │
│           ▼                   ▼                                       │
│  ┌──────────────┐    ┌──────────────┐                                 │
│  │ TTSService   │◄───│ agent result │                                 │
│  │ Google Cloud │    └──────────────┘                                 │
│  │ TTS → PCM   │                                                      │
│  └──────┬───────┘                                                     │
│         │ [0x03] frames → iOS                                         │
└─────────┴─────────────────────────────────────────────────────────────┘
```

## Key Files

| Layer | File | Role |
|-------|------|------|
| iOS capture | `AudioCaptureEngine.swift` | Mic → 16kHz PCM chunks, barge-in detection |
| iOS transport | `WebSocketTransport.swift` | Binary frame encode/send, reconnection |
| iOS session | `ConversationSession.swift` | Orchestrates capture/playback, echo gating |
| iOS playback | `AudioPlaybackEngine.swift` | Zombie filter, meeting mode, speaker routing |
| Backend WS | `ws/handler.py` | Frame decode, routing, outbound encoding |
| Gemini | `gemini_proxy.py` | Audio send/receive loops, tool call routing |
| Agents | `agent_task_manager.py` | Dispatch, timeout, meeting mode, delivery |
| Claude SDK | `sdk_agent_runner.py` | Wraps Claude Agent SDK with MCP tools |
| TTS | `tts_service.py` | Google Cloud TTS for agent responses |
| Protocol | `codec.py` / `AudioFrameHeader.swift` | 4-byte frame header encode/decode |
