# Physical Device Demo: End-to-End Voice Automation Plan

## Goal

Automate the Case 2 conversation flow using a real iPhone picking up audio from the Mac's speakers — a true end-to-end demo with no human involvement.

## Hardware Setup

```
┌──────────────┐  audio over air   ┌──────────────────┐
│   MacBook    │ ───────────────▶  │  iPhone 15       │
│  (speaker)   │                   │  "iPhone3G"      │
│              │  USB connection   │  Device ID:      │
│              │ ◀───────────────▶ │  00008130-...    │
│              │  (build/deploy    │                  │
│              │   + mobile-mcp)   │  Running:        │
│              │                   │  VoiceAgent app  │
│  Backend     │  WiFi / localhost │                  │
│  :8000       │ ◀───────────────▶ │  WebSocket to    │
│              │                   │  backend         │
└──────────────┘                   └──────────────────┘
```

- Mac and iPhone placed ~30cm apart
- Mac speakers at moderate volume (60-70%)
- iPhone mic picks up Mac's TTS output
- Quiet room, minimal background noise

## Software Components

### 1. Backend (already running)
- FastAPI on `localhost:8000`
- Gemini Live API (moderator) + Claude agents (Ellen, etc.)
- No changes needed

### 2. iOS App on Physical Device
- Build with: `xcodebuild -project VoiceAgent.xcodeproj -scheme VoiceAgent -destination 'id=00008130-001A1D2E0E98001C' -allowProvisioningUpdates build`
- Install with: `xcrun devicectl device install app --device 00008130-001A1D2E0E98001C <app_path>`
- Launch with: `xcrun devicectl device process launch --device 00008130-001A1D2E0E98001C com.shijinglu.VoiceAgent`

### 3. Device Control (mobile-mcp)
- Used to tap the "TAP TO START" button on the iPhone
- Take screenshots to verify app state
- **Blocker**: mobile-mcp currently does not detect the physical device (returns empty device list)
- **Fallback**: Use `xcrun devicectl` commands or Xcode UI automation

### 4. Mac TTS (orchestrator)
- `say -v Samantha "utterance text"` — speaks through Mac speakers
- iPhone mic picks up the audio naturally
- Pacing controlled by sleep intervals between utterances

### 5. Log Monitoring
- Backend logs: `tail -f backend/logs/app.log`
- Watch for transcript, agent_status, and playback_state events
- Can also capture via `xcrun devicectl device process launch --console`

## Execution Steps

### Phase 1: Prepare Environment
1. Ensure backend is running on `:8000`
2. Set Mac volume to ~65% (`osascript -e 'set volume output volume 65'`)
3. Build and deploy VoiceAgent to iPhone
4. Launch the app on iPhone

### Phase 2: Start Session
5. Use mobile-mcp (or fallback) to tap the mic "TAP TO START" button on iPhone
6. Wait 2-3s for WebSocket connection + Gemini session to initialize
7. Start backend log capture in background

### Phase 3: Run Conversation
For each utterance in the script:
8. Run `say -v Samantha "<utterance>"` on Mac
9. Wait for the `say` command to complete (speech finished)
10. Wait additional N seconds for:
    - iPhone mic → backend → Gemini to process
    - Moderator/agent response to play back on iPhone speaker
11. (Optional) Take screenshot of iPhone to capture UI state
12. Proceed to next utterance

### Phase 4: Collect Results
13. Stop log capture
14. Take final screenshot
15. Parse backend logs for timing data (transcript timestamps, agent events)
16. Generate timing report

## Conversation Script (Case 2)

| Step | Speaker | Utterance | Wait After |
|------|---------|-----------|------------|
| 1 | user | "hello" | 6s |
| 2 | user | "help me pull the TODO items from work for today" | 10s |
| 3 | user | "also, help me review yesterday business metrics and brief me with a summary." | 12s |
| 4 | user | "skip the morning schedules, help me check if there are arrangements at dinner time" | 15s |
| 5 | user | "yes, cancel that for me please" | 10s |
| 6 | user | "review the metrics" | 15s |

Wait times are longer than WebSocket demo because:
- Audio travels over air (slight delay)
- iPhone's VAD needs time to detect end of speech
- Response audio plays back through iPhone speaker (can't skip playback)

## Known Blockers & Mitigations

### Blocker 1: mobile-mcp doesn't see physical device
- **Status**: `mobile_list_available_devices` returns empty
- **Root cause**: mobile-mcp may need WebDriverAgent (WDA) installed on the device, or the device needs to be in a specific state
- **Mitigations**:
  - Option A: Install WebDriverAgent on the physical device via Xcode
  - Option B: Use `xcrun simctl` / `xcrun devicectl` for basic control
  - Option C: Pre-launch the app and start the session manually, then automate only the voice part (Mac `say` commands)
  - Option D: Use Xcode's UI testing framework (`XCUITest`) to tap the button programmatically

### Blocker 2: Audio feedback loop
- iPhone speaker plays response → Mac mic could pick it up (if Mac is also listening)
- **Mitigation**: Mac is only speaking, not listening. No loop risk.
- **Concern**: iPhone mic picking up its own speaker output
- **Mitigation**: iPhone's AEC (Acoustic Echo Cancellation) in AVAudioEngine should handle this. The app already implements AEC (shared AVAudioEngine for capture + playback per `b810010`).

### Blocker 3: Timing uncertainty
- Over-air audio has variable latency depending on volume, distance, background noise
- **Mitigation**: Use generous wait times. Monitor backend logs for `FINAL` transcripts to confirm recognition before proceeding.

### Blocker 4: iPhone speaker volume
- Response audio might be too quiet to hear / verify
- **Mitigation**: Set iPhone volume to max before starting. Can be done via mobile-mcp `press_button(volume_up)` or device settings.

## Script Architecture

```python
# demos/demo_case_2_physical.py

import subprocess
import time

UTTERANCES = [
    ("hello", 6),
    ("help me pull the TODO items from work for today", 10),
    ...
]

# 1. Set Mac volume
subprocess.run(["osascript", "-e", "set volume output volume 65"])

# 2. Tap start button on iPhone (via mobile-mcp or fallback)
tap_start_button()

# 3. Wait for session init
time.sleep(3)

# 4. For each utterance
for text, wait in UTTERANCES:
    # Speak through Mac speakers
    subprocess.run(["say", "-v", "Samantha", text])
    # Wait for response
    time.sleep(wait)

# 5. Collect logs and screenshots
```

## Comparison: WebSocket Demo vs Physical Device Demo

| Aspect | WebSocket (`demo_case_2.py`) | Physical Device |
|--------|------------------------------|-----------------|
| Audio path | PCM bytes over WebSocket | Air (speaker → mic) |
| Latency | ~1-2s first response | ~2-4s (air + VAD) |
| Device needed | None (Python client) | iPhone + USB |
| Barge-in testable | No (sequential sends) | Yes (natural overlap) |
| AEC tested | No | Yes |
| Demo fidelity | Protocol-level | Real user experience |
| Reproducibility | High | Medium (ambient noise) |

## Next Steps

1. Resolve mobile-mcp physical device detection (install WDA or find alternative)
2. Write `demos/demo_case_2_physical.py` orchestration script
3. Test with a short utterance first ("hello") to validate the full loop
4. Run full Case 2 conversation
5. Extend to other cases if successful
