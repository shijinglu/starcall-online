# AI Conversation & Digital Agent System

Voice-first AI assistant: users speak with a fast Gemini moderator that delegates complex tasks to deep-thinking Claude agents running in parallel.

## Architecture

- **Fast path**: iOS app <-> FastAPI backend <-> Gemini Live API (real-time voice, VAD, barge-in, TTS)
- **Slow path**: Backend <-> Claude SDK agents (async, results injected back into voice session)

## Repository Layout

```
backend/          Python FastAPI backend
  app/            Application code (main.py is the entry point)
  prompts/        Agent persona prompts (ellen, eva, ming, shijing)
  tests/          Unit, component, and integration tests
  Makefile        make install | run | test | lint | clean
  pyproject.toml  Dependencies and project config (uses uv + hatchling)

ios/              Native iOS app (Swift, iOS 17+)
  VoiceAgent/     App source code
    App/          SwiftUI entry point + Info.plist
    Audio/        AVAudioEngine mic capture and playback
    Models/       Data models
    Network/      WebSocket transport
    Session/      Session management
    ViewModel/    View models
    Views/        SwiftUI views
  VoiceAgent.xcodeproj/  Xcode project (use this to build, not the SPM workspace)
  Package.swift   SPM package (library + tests only)
  project.yml     xcodegen spec (regenerate .xcodeproj with `xcodegen generate`)
  Makefile        make build | test | clean (SPM-based, for library/tests)

docs/             Design documents and execution plans
  overview.md           Product overview, use cases, tech stack
  high_level_design.md  Architecture, endpoints, wire format
  ios_app_design.md     iOS app design
  exec-plans/           Phased implementation plans (phases 0-5)
```

## Quick Start

### Backend
```sh
cd backend
make install    # creates .venv, installs deps
make run        # starts on :8000
make test       # runs all tests
```
**LOGS** backend logs are in `backend/logs/app.log`

### iOS
Open `ios/VoiceAgent.xcodeproj` in Xcode, select an iOS 17+ simulator, and run.

## Key Tech

- **Backend**: Python 3.11+, FastAPI, `google-genai` (Gemini Live API), `anthropic` (Claude agents), Google Cloud TTS, uv
- **iOS**: Swift 5.9, SwiftUI, AVFoundation, WebSocket
- **Protocol**: Single WebSocket (`/api/v1/conversation/live`) carrying binary audio frames + JSON control messages
