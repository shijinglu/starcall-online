# Phase 0 — Foundation: Protocols, Schemas & Project Scaffolding

## Goal
Establish the shared contract between all system components before any feature code is written. Every subsequent phase depends on the definitions here.

---

## 0.1 Binary Wire Protocol

### Frame Layout

All audio is transported as binary WebSocket frames. Every frame carries a fixed 4-byte header followed by raw PCM bytes.

```
Byte 0: msg_type   — identifies the frame kind
Byte 1: speaker_id — who is speaking
Byte 2: gen_id     — generation counter (zombie-audio prevention)
Byte 3: frame_seq  — monotonic sequence within a generation (wraps 0–255)
Bytes 4+: raw PCM  — 16 kHz, int16, little-endian
```

### msg_type Values

| Hex  | Name           | Direction       | Sender       |
|------|----------------|-----------------|--------------|
| 0x01 | audio_chunk    | Client → Server | iOS app      |
| 0x02 | audio_response | Server → Client | Moderator    |
| 0x03 | agent_audio    | Server → Client | Deep agent   |

### speaker_id Values

| Value | Meaning          |
|-------|------------------|
| 0x00  | Moderator / User |
| 0x01  | ellen            |
| 0x02  | shijing          |
| 0x03  | eva              |
| 0x04  | ming             |

### gen_id Rules

- Server is authoritative. Starts at 0x00 at session open.
- Incremented by 1 (wrapping at 255) on every barge-in interrupt processed.
- Client adopts the value from `interruption{gen_id: N}` JSON message.
- On client → server frames (`audio_chunk`): byte 2 is always `0x00` — the server ignores it.
- Client discards any received binary frame where the frame's gen_id is **stale** relative to `current_gen`.

> **Critical — use RFC 1982 modular arithmetic, NOT plain `<`.**
> A naive `frame.gen_id < current_gen` comparison is broken at the 0/255 wrap boundary.
> After 256 barge-ins the counter wraps; all subsequent frames would be incorrectly discarded.
>
> Correct staleness check (Python, server):
> ```python
> def gen_id_is_stale(frame_gen: int, current_gen: int) -> bool:
>     """Returns True if frame_gen is in the 'past' half of the circular space."""
>     return ((current_gen - frame_gen) & 0xFF) > 0 and ((current_gen - frame_gen) & 0xFF) < 128
> ```
>
> Correct staleness check (Swift, iOS):
> ```swift
> func isStale(frameGen: UInt8, currentGen: UInt8) -> Bool {
>     let diff = Int(currentGen &- frameGen) & 0xFF
>     return diff > 0 && diff < 128
> }
> ```

### frame_seq Rules

- Per-stream sequence counter within a single generation.
- Resets to 0x00 when gen_id increments.
- Wraps from 255 → 0 without error.
- Used for jitter detection (not ordering/retransmit — UDP-like).

### Python Codec (Reference Implementation)

```python
import struct

HEADER_FMT = ">BBBB"   # 4 unsigned bytes, big-endian
HEADER_SIZE = 4

class MsgType:
    AUDIO_CHUNK    = 0x01
    AUDIO_RESPONSE = 0x02
    AGENT_AUDIO    = 0x03

class SpeakerId:
    MODERATOR = 0x00
    ELLEN     = 0x01
    SHIJING   = 0x02
    EVA       = 0x03
    MING      = 0x04

AGENT_SPEAKER_IDS = {
    "ellen": SpeakerId.ELLEN,
    "shijing": SpeakerId.SHIJING,
    "eva": SpeakerId.EVA,
    "ming": SpeakerId.MING,
}

def encode_frame(msg_type: int, speaker_id: int, gen_id: int, frame_seq: int, pcm: bytes) -> bytes:
    header = struct.pack(HEADER_FMT, msg_type, speaker_id, gen_id & 0xFF, frame_seq & 0xFF)
    return header + pcm

def decode_frame(data: bytes) -> tuple[int, int, int, int, bytes]:
    """Returns (msg_type, speaker_id, gen_id, frame_seq, pcm_bytes)."""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Frame too short: {len(data)} bytes")
    msg_type, speaker_id, gen_id, frame_seq = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
    return msg_type, speaker_id, gen_id, frame_seq, data[HEADER_SIZE:]
```

### Swift Codec (Reference)

```swift
struct AudioFrameHeader {
    let msgType: UInt8     // 0x01 / 0x02 / 0x03
    let speakerId: UInt8
    let genId: UInt8
    let frameSeq: UInt8

    static let size = 4

    init?(data: Data) {
        guard data.count >= Self.size else { return nil }
        msgType   = data[0]
        speakerId = data[1]
        genId     = data[2]
        frameSeq  = data[3]
    }

    func encode(pcm: Data) -> Data {
        var header = Data([msgType, speakerId, genId, frameSeq])
        header.append(pcm)
        return header
    }
}

enum MsgType: UInt8 {
    case audioChunk    = 0x01
    case audioResponse = 0x02
    case agentAudio    = 0x03
}
```

---

## 0.2 JSON Message Schemas

All control and status messages travel as **text** WebSocket frames carrying JSON.

### Client → Server

#### `control`
```json
{
  "type": "control",
  "action": "start" | "stop" | "pause"
}
```

#### `interrupt`
```json
{
  "type": "interrupt",
  "mode": "cancel_all" | "skip_speaker"
}
```
- `mode` defaults to `"cancel_all"` if omitted.
- `cancel_all`: cancels all running Claude tasks + clears meeting queue.
- `skip_speaker`: cancels only the current TTS stream, advances meeting queue; preserves pre-computed agent results.

#### `agent_followup`
```json
{
  "type": "agent_followup",
  "agent_session_id": "<uuid>",
  "text": "What about last month?"
}
```

---

### Server → Client

#### `transcript`
```json
{
  "type": "transcript",
  "speaker": "user" | "moderator",
  "text": "What is my risk score?",
  "is_final": true
}
```

#### `agent_status`
```json
{
  "type": "agent_status",
  "agent_name": "ellen",
  "agent_session_id": "<uuid>",
  "status": "dispatched" | "thinking" | "done" | "timeout" | "cancelled",
  "elapsed_ms": 12000,
  "gen_id": 3
}
```
- `elapsed_ms` only present on `thinking` heartbeats.
- `gen_id` present on all events for client-side correlation.

#### `meeting_status`
```json
{
  "type": "meeting_status",
  "gen_id": 3,
  "total_agents": 4,
  "completed": 2,
  "pending": ["eva", "ming"],
  "failed": []
}
```

#### `interruption`
```json
{
  "type": "interruption",
  "gen_id": 5
}
```

#### `error`
```json
{
  "type": "error",
  "code": "AUTH_FAILED" | "SESSION_NOT_FOUND" | "AGENT_BUSY" | "INTERNAL",
  "message": "Human-readable description"
}
```

---

## 0.3 REST API Schemas

### `POST /api/v1/sessions`

**Request:** No body.

**Response 200:**
```json
{
  "session_id": "s-<uuid-v4>",
  "auth_token": "<uuid-v4>",
  "expires_at": "2024-01-01T12:05:00Z"
}
```
- `auth_token` has a 5-minute TTL and is single-use (consumed on WS connect).

### `DELETE /api/v1/sessions/{session_id}`

**Response 200:**
```json
{ "status": "terminated" }
```

**Response 404:** Session not found.

### `GET /api/v1/agents`

**Response 200:**
```json
{
  "agents": [
    {
      "name": "ellen",
      "description": "Personal assistant — calendar, email, tasks",
      "voice_id": "en-US-Journey-F",
      "speaker_id": 1,
      "tool_set": ["calendar_read", "email_send", "task_list"]
    },
    {
      "name": "shijing",
      "description": "User risk analyst — user profile and journey",
      "voice_id": "en-US-Journey-D",
      "speaker_id": 2,
      "tool_set": ["user_profile_read", "user_journey_read", "risk_score_read"]
    },
    {
      "name": "eva",
      "description": "Financial analyst — transactions and bank data",
      "voice_id": "en-US-Journey-O",
      "speaker_id": 3,
      "tool_set": ["transaction_read", "bank_data_read", "chargeback_read"]
    },
    {
      "name": "ming",
      "description": "Fraud investigator — ID checks and async risk",
      "voice_id": "en-US-Neural2-D",
      "speaker_id": 4,
      "tool_set": ["id_check", "async_risk_check", "fraud_signal_read"]
    }
  ]
}
```

### `GET /api/v1/health`

**Response 200:**
```json
{
  "status": "ok",
  "version": "0.1.0",
  "active_sessions": 3
}
```

---

## 0.4 Internal Data Models

### Python (Backend)

```python
from dataclasses import dataclass, field
from typing import Literal, Optional
import asyncio, uuid, time

AgentStatus = Literal["active", "idle", "cancelled", "timeout"]

@dataclass
class AgentSession:
    agent_session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = ""
    conversation_history: list = field(default_factory=list)  # [{role, content}, ...]
    status: AgentStatus = "active"
    claude_task: Optional[asyncio.Task] = None
    parent_session_id: str = ""
    created_at: float = field(default_factory=time.time)

@dataclass
class ConversationSession:
    session_id: str = field(default_factory=lambda: "s-" + str(uuid.uuid4()))
    auth_token: str = field(default_factory=lambda: str(uuid.uuid4()))
    token_expires_at: float = field(default_factory=lambda: time.time() + 300)  # 5 min
    token_consumed: bool = False
    ws_connection: object = None          # websockets.WebSocketServerProtocol
    gemini_session: object = None
    agent_sessions: dict = field(default_factory=dict)  # agent_session_id → AgentSession
    gen_id: int = 0
    meeting_queue: list = field(default_factory=list)   # ordered agent audio results
    last_activity: float = field(default_factory=time.time)
    session_ttl: float = 7200.0          # 2 hours

@dataclass
class AgentRegistryEntry:
    name: str
    description: str
    voice_id: str
    speaker_id: int
    system_prompt: str
    tool_set: list[str]
```

### Swift (iOS)

```swift
enum SessionState { case idle, connecting, active, stopped }
enum AgentStatusKind: String { case dispatched, thinking, done, timeout, cancelled }

struct AgentStatusEvent {
    let agentName: String
    let agentSessionId: String
    let status: AgentStatusKind
    let elapsedMs: Int?
    let genId: Int
}

struct MeetingStatusEvent {
    let genId: Int
    let totalAgents: Int
    let completed: Int
    let pending: [String]
    let failed: [String]
}
```

---

## 0.5 Audio Format Specification

| Property    | Value                  |
|-------------|------------------------|
| Sample rate | 16,000 Hz              |
| Bit depth   | 16-bit signed integer  |
| Byte order  | Little-endian          |
| Channels    | 1 (mono)               |
| Frame size  | 3,200 bytes = 100 ms   |
| Encoding    | LINEAR16 (raw PCM)     |

- iOS captures at 44.1 kHz and downsamples to 16 kHz before sending.
- Google Cloud TTS is configured with `AudioEncoding.LINEAR16` and `sample_rate_hertz=16000`.
- Gemini Live native TTS output: same format.

---

## 0.6 Project Structure

### Backend

```
backend/
├── app/
│   ├── main.py                   # FastAPI app factory
│   ├── config.py                 # env vars (API keys, TTLs, ports)
│   ├── models.py                 # dataclasses: AgentSession, ConversationSession
│   ├── codec.py                  # encode_frame / decode_frame
│   ├── registry.py               # AgentRegistry (static in-memory map)
│   ├── routers/
│   │   ├── sessions.py           # POST/DELETE /api/v1/sessions
│   │   ├── agents.py             # GET /api/v1/agents
│   │   └── health.py             # GET /api/v1/health
│   ├── ws/
│   │   └── handler.py            # WebSocket endpoint + frame routing
│   ├── session_manager.py        # ConversationSession lifecycle + TTL
│   ├── gemini_proxy.py           # Gemini Live bidirectional proxy
│   ├── agent_task_manager.py     # Dispatch, resume, interrupt, meeting queue
│   ├── deep_agent_runner.py      # Claude SDK + sentence splitter
│   └── tts_service.py            # Google Cloud TTS per-sentence
├── tests/
│   ├── unit/
│   │   ├── test_codec.py
│   │   └── test_sentence_splitter.py
│   ├── component/                # L3 tests — real external APIs
│   └── integration/              # L2 tests — real backend, WS client
├── pyproject.toml
└── .env.example
```

### iOS App

```
iOS/
├── ConversationApp.xcodeproj
├── Sources/
│   ├── App/
│   │   └── ConversationApp.swift       # SwiftUI entry point
│   ├── Audio/
│   │   ├── AudioCaptureEngine.swift
│   │   └── AudioPlaybackEngine.swift
│   ├── Network/
│   │   └── WebSocketTransport.swift
│   ├── Session/
│   │   └── ConversationSession.swift   # state machine
│   ├── ViewModel/
│   │   └── ConversationViewModel.swift
│   ├── Models/
│   │   ├── AudioFrameHeader.swift
│   │   ├── JSONMessages.swift          # Codable structs for all JSON types
│   │   └── AgentState.swift
│   └── Views/
│       ├── ContentView.swift
│       ├── AgentStatusCard.swift
│       └── MeetingProgressView.swift
└── Tests/
    ├── AudioFrameHeaderTests.swift
    └── ConversationSessionTests.swift
```

---

## 0.7 Dependencies

### Backend

```toml
[tool.poetry.dependencies]
python = "^3.11"
fastapi = "^0.111"
uvicorn = {extras = ["standard"], version = "^0.29"}
websockets = "^12.0"
google-genai = "^0.8"          # Gemini Live API
anthropic = "^0.28"            # Claude SDK (claude-agent-sdk-python)
google-cloud-texttospeech = "^2.16"
pydantic = "^2.7"
python-dotenv = "^1.0"
```

### iOS

Swift Package Manager dependencies:
- No third-party audio libraries needed (AVFoundation covers everything)
- `URLSession` with `webSocketTask` for WS (built-in)

---

## 0.8 Environment Variables

```bash
# backend/.env.example
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_APPLICATION_CREDENTIALS=/path/to/tts-service-account.json

# Session config
SESSION_TTL_SECONDS=7200
AUTH_TOKEN_TTL_SECONDS=300

# Agent task config
AGENT_TASK_TIMEOUT_SECONDS=30
THINKING_HEARTBEAT_INTERVAL_SECONDS=10

# Server
HOST=0.0.0.0
PORT=8000
```

---

## Phase 0 Completion Criteria

- [ ] Binary frame encode/decode round-trips without data loss for all field value ranges
- [ ] All JSON schemas have Pydantic models (backend) and Codable structs (iOS) that parse correctly
- [ ] `GET /api/v1/agents` returns all 4 agents with correct shape
- [ ] Backend project compiles and `uvicorn app.main:app` starts cleanly
- [ ] iOS project compiles with no errors
- [ ] Unit test `test_codec.py` passes (see Phase 5 test T-U-01)
