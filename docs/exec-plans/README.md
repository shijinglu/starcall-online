# Execution Plan Index

Implementation and testing plan for the AI Conversation & Digital Agent System.

## Phases

| File | Phase | Description |
|------|-------|-------------|
| [phase-0-foundation.md](phase-0-foundation.md) | 0 — Foundation | Binary wire protocol, JSON schemas, REST API schemas, internal data models, audio format spec, project structure, dependencies |
| [phase-1-backend-core.md](phase-1-backend-core.md) | 1 — Backend Core | Session Manager, WebSocket handler, REST endpoints, Agent Registry, auth token lifecycle |
| [phase-2-gemini-integration.md](phase-2-gemini-integration.md) | 2 — Gemini Integration | Gemini Live proxy, audio send/receive loops, `dispatch_agent` and `resume_agent` tool call handling, system prompt construction |
| [phase-3-deep-agents.md](phase-3-deep-agents.md) | 3 — Deep Agents | Agent Task Manager state machine, Claude SDK streaming runner, sentence boundary splitter, TTS service, Meeting Mode, 30 s timeout, interrupt cancellation |
| [phase-4-ios-app.md](phase-4-ios-app.md) | 4 — iOS App | AudioCaptureEngine (AEC, RMS barge-in), AudioPlaybackEngine (gen_id filtering, meeting queue), WebSocketTransport (binary/JSON), ConversationSession state machine, SwiftUI ViewModel |
| [phase-5-testing.md](phase-5-testing.md) | 5 — Testing | 40+ detailed test cases across unit, component, integration, and full-stack levels; mock strategy; CI/CD execution plan |

## Dependency Order

```
Phase 0 (Protocol definitions)
    │
    ├── Phase 1 (Backend skeleton — no external APIs)
    │       │
    │       ├── Phase 2 (Gemini Live integration)
    │       │       │
    │       │       └── Phase 3 (Claude + TTS + Meeting Mode)
    │       │
    │       └── Phase 4 (iOS App — can start parallel to Phase 2)
    │
    └── Phase 5 (Tests written incrementally, run per phase)
```

## Key Design Decisions

- **Two separate TTS paths**: Gemini Live (moderator, zero-latency) vs. Google Cloud TTS (agents, per-voice).
- **Binary frame protocol**: 4-byte header prevents base64 overhead; `gen_id` prevents zombie audio.
- **Non-blocking dispatch**: `dispatch_agent` returns immediately; Claude runs async; Gemini voices acknowledgment without waiting.
- **Sentence-level TTS pipelining**: First agent sentence reaches the user < 2 s after dispatch; Claude need not finish.
- **Dual barge-in triggers**: Client RMS (< 100 ms) + Gemini VAD (~200 ms), idempotent via `gen_id`.
- **Deterministic busy policy**: `agent_busy` is voiced immediately; no silent queuing.
