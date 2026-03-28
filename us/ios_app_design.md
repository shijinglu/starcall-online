# iOS App Design — Native Swift Client

> **Research basis**: patterns extracted from community Whisper iOS apps
> (WhisperKit/argmaxinc, Whisper Transcription by Jordi Bruin, and open-source
> streaming dictation repos), adapted for our use case where Gemini Live API
> handles STT/VAD/TTS server-side — so several Whisper-specific layers are dropped.

---

## What We Borrowed from Whisper iOS Apps

| Pattern | Source | Why we keep it |
|---|---|---|
| Single persistent `AVAudioEngine` tap | WhisperKit | Remove/re-add causes audio glitches mid-session |
| Shared engine for capture + playback | Community pattern | Avoids format conflicts; AVAudioSession routes both automatically |
| `scheduleBuffer` playback queue | Playback apps | Buffers play back-to-back seamlessly with no gap |
| `flushAndStop()` for barge-in | Streaming apps | `AVAudioPlayerNode.stop()` discards queue instantly (<5 ms) |
| `AVAudioSession` interruption observer | All production apps | Handles phone calls, Siri, alarms gracefully |

## What We Dropped (not needed — Gemini handles it)

| Dropped | Reason |
|---|---|
| Client-side VAD (energy threshold segmentation) | Gemini Live API does VAD server-side |
| Audio snapshot accumulation | We stream continuously; no need to buffer a full utterance |
| On-device Whisper inference (Core ML) | Gemini handles STT |
| Transcript assembly from partial chunks | Server sends complete `transcript` events |

---

## Module Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      AVAudioSession                          │
│    category: .playAndRecord   mode: .voiceChat               │
│    options:  .allowBluetooth, .defaultToSpeaker              │
└──────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────▼───────────────┐
              │          AVAudioEngine         │
              │  inputNode ──► [tap]           │
              │  AVAudioPlayerNode ──► output  │
              └───────────────────────────────┘
                     │                   │
          ┌──────────▼──────┐   ┌────────▼──────────┐
          │ AudioCapture    │   │ AudioPlayback      │
          │ Engine          │   │ Engine             │
          └──────────┬──────┘   └────────┬──────────┘
                     │                   │
          ┌──────────▼───────────────────▼──────────┐
          │           ConversationSession            │
          │        (central coordinator /            │
          │     state machine + gen_id tracking)     │
          └──────────────────┬──────────────────────┘
                             │
          ┌──────────────────▼──────────────────────┐
          │           WebSocketTransport             │
          │  (URLSessionWebSocketTask)               │
          │  binary frames: audio | JSON: control   │
          └──────────────────┬──────────────────────┘
                             │
                         Backend WS (?token=...)
                    (→ Gemini Live API)
```

---

## Module 1 — AudioCaptureEngine

**Responsibility**: Configure `AVAudioSession` with `.voiceChat` mode (enables hardware AEC), tap `AVAudioEngine.inputNode`, convert to 16 kHz mono PCM, emit 100 ms chunks upstream.

```swift
// Lesson from WhisperKit: install one persistent tap; never remove/re-add during a session.
// Gemini Live expects: PCM 16-bit, 16 kHz, mono (LINEAR16 format).
// .voiceChat mode activates hardware Acoustic Echo Canceller — prevents speaker bleed
// from triggering false barge-in when user is not wearing headphones.
final class AudioCaptureEngine {
    private let engine = AVAudioEngine()
    private let converter: AVAudioConverter   // 44.1 kHz float32 → 16 kHz int16

    var onPCMChunk: ((Data) -> Void)?         // 100 ms of raw PCM, ready to send

    func start() throws {
        // Configure AVAudioSession BEFORE starting engine
        try AVAudioSession.sharedInstance().setCategory(.playAndRecord,
            mode: .voiceChat,                   // hardware AEC + AGC enabled
            options: [.allowBluetooth, .defaultToSpeaker])
        try AVAudioSession.sharedInstance().setActive(true)

        let inputNode = engine.inputNode
        let nativeFormat = inputNode.outputFormat(forBus: 0) // device native (44.1/48 kHz)
        let targetFormat = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                         sampleRate: 16000, channels: 1,
                                         interleaved: true)!
        converter = AVAudioConverter(from: nativeFormat, to: targetFormat)!

        // 100 ms buffer @ native rate
        let bufferSize = AVAudioFrameCount(nativeFormat.sampleRate * 0.1)
        inputNode.installTap(onBus: 0, bufferSize: bufferSize,
                             format: nativeFormat) { [weak self] buffer, _ in
            self?.convert(buffer)
        }
        try engine.start()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }

    private func convert(_ input: AVAudioPCMBuffer) {
        // Downsample to 16 kHz int16 and emit raw bytes
        guard let output = AVAudioPCMBuffer(
            pcmFormat: converter.outputFormat,
            frameCapacity: AVAudioFrameCount(input.frameLength / 3)) else { return }
        var error: NSError?
        converter.convert(to: output, error: &error) { _, outStatus in
            outStatus.pointee = .haveData
            return input
        }
        if let channelData = output.int16ChannelData {
            let byteCount = Int(output.frameLength) * 2
            let data = Data(bytes: channelData[0], count: byteCount)
            onPCMChunk?(data)
        }
    }
}
```

**Key design decisions:**
- Single persistent tap — never remove/re-add mid-session (learned from WhisperKit; teardown causes audio glitches)
- `.voiceChat` AVAudioSession mode — hardware AEC eliminates speaker-to-mic echo that would cause false barge-in
- Convert format inside the tap callback, not on the main thread
- `onPCMChunk` is the only output — keeps it decoupled from transport

---

## Module 2 — AudioPlaybackEngine

**Responsibility**: Queue incoming TTS PCM chunks per speaker; play sequentially in Meeting Mode (FIFO); discard stale audio after barge-in via `gen_id` guard; `flushAllAndStop(newGen:)` for barge-in.

```swift
// Playback format locked to 16 kHz int16 to match server TTS output contract.
// All Google Cloud TTS calls are configured to output LINEAR16 @ 16 kHz.
// Per-speaker AVAudioPlayerNode map supports Meeting Mode without mixing voices.
final class AudioPlaybackEngine {
    private let engine: AVAudioEngine          // shared with AudioCaptureEngine
    private var playerNodes: [UInt8: AVAudioPlayerNode] = [:]  // speakerId → node
    private let playbackFormat = AVAudioFormat( // must match server TTS output
        commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

    // Zombie audio prevention: discard frames from old generations
    private var currentGen: UInt8 = 0

    var onPlaybackStateChange: ((Bool) -> Void)?  // true = playing, false = idle

    init(engine: AVAudioEngine) {
        self.engine = engine
        setupPlayerNode(for: 0x00)  // moderator node pre-created
    }

    private func setupPlayerNode(for speakerId: UInt8) {
        guard playerNodes[speakerId] == nil else { return }
        let node = AVAudioPlayerNode()
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: playbackFormat)
        playerNodes[speakerId] = node
    }

    // Called for each incoming binary audio frame (audio_response or agent_audio).
    // speakerId 0x00 = moderator; 0x01–0x04 = agents (ellen/shijing/eva/ming).
    // Meeting Mode: agent audio is serialized server-side (FIFO meeting_queue);
    // each agent's chunks arrive only after the previous agent's stream is complete.
    func enqueue(pcmData: Data, speakerId: UInt8, genId: UInt8) {
        // Discard stale frames from before the last barge-in
        guard genId >= currentGen else { return }

        setupPlayerNode(for: speakerId)
        guard let buffer = makePCMBuffer(from: pcmData),
              let node = playerNodes[speakerId] else { return }

        node.scheduleBuffer(buffer) { [weak self] in
            self?.onPlaybackStateChange?(false)
        }
        if !node.isPlaying {
            node.play()
            onPlaybackStateChange?(true)
        }
    }

    // Called on barge-in — flush all speaker nodes and advance the generation counter.
    // Any frames arriving with genId < newGen will be silently discarded by enqueue().
    func flushAllAndStop(newGen: UInt8) {
        currentGen = newGen
        for node in playerNodes.values { node.stop() }
        onPlaybackStateChange?(false)
    }

    private func makePCMBuffer(from data: Data) -> AVAudioPCMBuffer? {
        let frameCount = AVAudioFrameCount(data.count / 2)  // 2 bytes per int16 frame
        guard let buffer = AVAudioPCMBuffer(pcmFormat: playbackFormat,
                                            frameCapacity: frameCount) else { return nil }
        buffer.frameLength = frameCount
        data.withUnsafeBytes { ptr in
            buffer.int16ChannelData![0].assign(
                from: ptr.bindMemory(to: Int16.self).baseAddress!, count: Int(frameCount))
        }
        return buffer
    }
}
```

---

## Module 3 — WebSocketTransport

**Responsibility**: Own the WebSocket connection with token auth; send audio as **binary frames**; dispatch binary audio frames and JSON control messages to separate typed handlers.

```swift
// Binary frame layout (4-byte header + raw PCM):
//   [1B: msg_type][1B: speaker_id][1B: gen_id][1B: frame_seq] | PCM bytes
//
// msg_type values:
//   0x01 = audio_chunk  (client → server)
//   0x02 = audio_response (server → client, moderator)
//   0x03 = agent_audio    (server → client, agent)
//
// All control/status messages remain JSON text frames.
final class WebSocketTransport {
    private var task: URLSessionWebSocketTask?
    private let session = URLSession(configuration: .default)
    private var reconnectDelay: TimeInterval = 1.0
    private var outboundSeq: UInt8 = 0

    // Audio frames: carry speakerId and genId extracted from binary header
    var onAudioFrame: ((Data, UInt8, UInt8) -> Void)?       // (pcmData, speakerId, genId)
    // JSON control/status handlers
    var onTranscript: ((String, String) -> Void)?            // (speaker, text)
    var onAgentStatus: ((String, String, Int) -> Void)?      // (agentName, status, elapsedMs)
    var onMeetingStatus: ((Int, Int, [String]) -> Void)?     // (total, completed, pending[])
    var onInterruption: ((UInt8) -> Void)?                   // server-confirmed gen_id
    var onError: ((String) -> Void)?

    // Token is appended as ?token=<authToken> query parameter
    func connect(to url: URL, token: String) {
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "token", value: token)]
        task = session.webSocketTask(with: components.url!)
        task?.resume()
        receive()
    }

    func disconnect() {
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    // Send audio as binary frame — no base64, no JSON wrapping
    func sendAudioChunk(_ pcm: Data) {
        let header = Data([0x01, 0x00, 0x00, outboundSeq])
        outboundSeq &+= 1
        task?.send(.data(header + pcm)) { _ in }
    }

    // cancel_all (default): cancels all running agent tasks and clears the meeting queue.
    // skip_speaker: cancels only the currently-playing agent's audio; preserves pre-computed
    //               results for remaining agents in the Meeting Mode queue.
    func sendInterrupt(mode: String = "cancel_all") {
        sendJSON(["type": "interrupt", "mode": mode])
    }
    func sendSkipSpeaker() { sendInterrupt(mode: "skip_speaker") }
    func sendControl(_ action: String) { sendJSON(["type": "control", "action": action]) }

    private func receive() {
        task?.receive { [weak self] result in
            switch result {
            case .success(let message):
                self?.dispatch(message)
                self?.receive()          // re-arm for next message
            case .failure:
                self?.scheduleReconnect()
            }
        }
    }

    private func dispatch(_ message: URLSessionWebSocketTask.Message) {
        switch message {
        case .data(let data):
            // Binary audio frame: [msgType][speakerId][genId][frameSeq] | PCM
            guard data.count >= 4 else { return }
            let msgType   = data[0]
            let speakerId = data[1]
            let genId     = data[2]
            // data[3] is frame_seq — available for jitter buffer use
            guard msgType == 0x02 || msgType == 0x03 else { return }
            onAudioFrame?(data.dropFirst(4), speakerId, genId)

        case .string(let text):
            // JSON control/status message
            guard let data = text.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let type = json["type"] as? String else { return }
            switch type {
            case "transcript":
                let speaker = json["speaker"] as? String ?? "unknown"
                let text    = json["text"]    as? String ?? ""
                onTranscript?(speaker, text)
            case "agent_status":
                let name    = json["agent_name"] as? String ?? ""
                let status  = json["status"]     as? String ?? ""
                let elapsed = json["elapsed_ms"] as? Int ?? 0
                onAgentStatus?(name, status, elapsed)
            case "meeting_status":
                let total     = json["total_agents"] as? Int ?? 0
                let completed = json["completed"]    as? Int ?? 0
                let pending   = json["pending"]      as? [String] ?? []
                onMeetingStatus?(total, completed, pending)
            case "interruption":
                let serverGen = UInt8(json["gen_id"] as? Int ?? 0)
                onInterruption?(serverGen)
            case "error":
                onError?(json["message"] as? String ?? "unknown error")
            default: break
            }
        @unknown default: break
        }
    }

    private func sendJSON(_ dict: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: dict),
              let text = String(data: data, encoding: .utf8) else { return }
        task?.send(.string(text)) { _ in }
    }

    private func scheduleReconnect() {
        // exponential backoff reconnect
    }
}
```

---

## Module 4 — ConversationSession (central coordinator)

**Responsibility**: Wire the three engines together; own the barge-in state machine with `gen_id` tracking; maintain adaptive RMS noise floor; handle `AVAudioSession` interruptions.

```swift
// Two barge-in triggers — whichever fires first wins:
//   Trigger A (client, ~0 ms):  adaptive RMS > noise_floor + 15 dB while AI is playing
//   Trigger B (server, ~200 ms): Gemini detects speech → backend sends "interruption"
// Both call handleBargein(newGen:) which is idempotent.
final class ConversationSession {

    enum State { case idle, connecting, active, stopped }
    private(set) var state: State = .idle

    private let capture   = AudioCaptureEngine()
    private let engine    = AVAudioEngine()           // shared engine
    private lazy var playback = AudioPlaybackEngine(engine: engine)
    private let transport = WebSocketTransport()

    // Adaptive noise floor (EMA over quiet periods, updated every 100ms chunk)
    private var noiseFloor: Float = 0.005              // initial conservative estimate
    private let noiseDecay: Float = 0.95               // EMA coefficient (~2s window)
    private let bargeInMarginFactor: Float = 5.623     // +15 dB = 10^(15/20)
    private var isAIPlaying = false

    // Generation counter — incremented on every barge-in to invalidate in-flight audio
    private var currentGen: UInt8 = 0

    var onStateChange:    ((State) -> Void)?
    var onTranscript:     ((String, String) -> Void)?
    var onAgentStatus:    ((String, String, Int) -> Void)?   // (name, status, elapsedMs)
    var onMeetingStatus:  ((Int, Int, [String]) -> Void)?    // (total, completed, pending)

    // MARK: - Lifecycle

    func start(wsURL: URL, authToken: String) throws {
        guard state == .idle else { return }
        transition(to: .connecting)

        // Wire: mic → transport (binary frame)
        capture.onPCMChunk = { [weak self] pcm in
            self?.transport.sendAudioChunk(pcm)
            self?.checkBargein(pcm: pcm)
        }

        // Wire: transport audio → playback (with speakerId + genId from binary header)
        transport.onAudioFrame = { [weak self] pcm, speakerId, genId in
            self?.playback.enqueue(pcmData: pcm, speakerId: speakerId, genId: genId)
        }

        // Server barge-in confirmation — take the higher gen of client and server
        transport.onInterruption = { [weak self] serverGen in
            guard let self else { return }
            let newGen = max(serverGen, self.currentGen)
            self.handleBargein(newGen: newGen)
        }

        transport.onTranscript    = { [weak self] s, t       in self?.onTranscript?(s, t) }
        transport.onAgentStatus   = { [weak self] n, s, e    in self?.onAgentStatus?(n, s, e) }
        transport.onMeetingStatus = { [weak self] tot, d, p  in self?.onMeetingStatus?(tot, d, p) }

        playback.onPlaybackStateChange = { [weak self] playing in
            self?.isAIPlaying = playing
        }

        transport.connect(to: wsURL, token: authToken)
        try capture.start()
        transition(to: .active)

        // Register for system audio interruptions (phone calls, Siri)
        NotificationCenter.default.addObserver(self,
            selector: #selector(handleAudioSessionInterruption),
            name: AVAudioSession.interruptionNotification, object: nil)
    }

    func stop() {
        capture.stop()
        transport.sendControl("stop")
        transport.disconnect()
        currentGen &+= 1
        playback.flushAllAndStop(newGen: currentGen)
        transition(to: .stopped)
    }

    // MARK: - Barge-in

    private func checkBargein(pcm: Data) {
        let rms = computeRMS(pcm)
        if isAIPlaying {
            // Barge-in: threshold is noise_floor * 10^(15dB/20) ≈ noise_floor * 5.6
            // Voice barge-in always uses cancel_all — the user is speaking, implying a new topic.
            // Use skipSpeaker() (UI button) to advance the meeting queue without cancelling agents.
            if rms > noiseFloor * bargeInMarginFactor {
                transport.sendInterrupt(mode: "cancel_all")
                currentGen &+= 1
                handleBargein(newGen: currentGen)
            }
        } else {
            // Quiet period: update adaptive noise floor with exponential moving average
            noiseFloor = noiseFloor * noiseDecay + rms * (1 - noiseDecay)
        }
    }

    // Called from a UI "Skip" button during Meeting Mode — advances to the next agent's audio
    // without cancelling agents that have already finished computing their response.
    func skipSpeaker() {
        transport.sendSkipSpeaker()
        currentGen &+= 1
        handleBargein(newGen: currentGen)
    }

    private func handleBargein(newGen: UInt8) {
        currentGen = max(currentGen, newGen)
        playback.flushAllAndStop(newGen: currentGen)
        isAIPlaying = false
    }

    // MARK: - System interruption (phone call, Siri, alarm)

    @objc private func handleAudioSessionInterruption(_ n: Notification) {
        guard let type = n.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let interruptionType = AVAudioSession.InterruptionType(rawValue: type)
        else { return }

        switch interruptionType {
        case .began:
            currentGen &+= 1
            playback.flushAllAndStop(newGen: currentGen)
        case .ended:
            try? engine.start()
        @unknown default: break
        }
    }

    private func transition(to newState: State) {
        state = newState
        onStateChange?(newState)
    }

    private func computeRMS(_ pcm: Data) -> Float {
        let samples = pcm.withUnsafeBytes { Array($0.bindMemory(to: Int16.self)) }
        let sumSq = samples.reduce(Float(0)) { $0 + Float($1) * Float($1) }
        return sqrt(sumSq / Float(max(samples.count, 1))) / Float(Int16.max)
    }
}
```

---

## Module 5 — ConversationViewModel

**Responsibility**: SwiftUI binding layer only — converts session events into `@Published` state. No business logic here.

```swift
@MainActor
final class ConversationViewModel: ObservableObject {
    @Published var sessionState: ConversationSession.State = .idle
    @Published var transcript: [TranscriptEntry] = []
    @Published var agentStatuses: [String: AgentStatus] = [:]   // agentName → status
    @Published var meetingProgress: MeetingProgress?             // nil when not in meeting mode
    @Published var micAmplitude: Float = 0                       // 0..1, for waveform animation

    struct TranscriptEntry: Identifiable {
        let id = UUID()
        let speaker: String
        var text: String
    }

    // Reflects all agent_status values: "dispatched" | "thinking" | "done" |
    //                                   "timeout" | "cancelled" | "agent_busy"
    struct AgentStatus {
        var status: String
        var elapsedMs: Int   // populated during "thinking" heartbeat events
    }

    struct MeetingProgress {
        var total: Int
        var completed: Int
        var pending: [String]
    }

    private let session = ConversationSession()

    func startConversation(wsURL: URL, authToken: String) {
        session.onStateChange   = { [weak self] s in self?.sessionState = s }
        session.onTranscript    = { [weak self] speaker, text in
            self?.transcript.append(.init(speaker: speaker, text: text))
        }
        session.onAgentStatus   = { [weak self] name, status, elapsed in
            self?.agentStatuses[name] = .init(status: status, elapsedMs: elapsed)
        }
        session.onMeetingStatus = { [weak self] total, completed, pending in
            self?.meetingProgress = .init(total: total, completed: completed, pending: pending)
        }
        try? session.start(wsURL: wsURL, authToken: authToken)
    }

    func stopConversation() { session.stop() }
}
```

---

## Module Summary

| Module | Est. lines | Core Apple API |
|---|---|---|
| `AudioCaptureEngine` | ~80 | `AVAudioEngine`, `AVAudioConverter` |
| `AudioPlaybackEngine` | ~70 | `AVAudioPlayerNode` (multi-speaker) |
| `WebSocketTransport` | ~110 | `URLSessionWebSocketTask` |
| `ConversationSession` | ~130 | wires the above three |
| `ConversationViewModel` | ~50 | SwiftUI `@ObservableObject` |
| **Total** | **~440 lines** | **Zero third-party dependencies** |

---

## Core Data Flows

```
FLOW 1 — Mic → Server (continuous streaming, every 100 ms)
──────────────────────────────────────────────────────────
AVAudioEngine inputNode tap
  → PCM buffer (44.1 kHz float32, 4410 frames)
  → AVAudioConverter → 16 kHz int16 mono (1600 frames = 100 ms)
  → Data (3200 bytes raw PCM)
  → binary WebSocket frame: [0x01][0x00][0x00][seq] + PCM bytes
      (no base64 — ~33% less bandwidth vs JSON encoding)
  → Backend → Gemini Live API
      (STT + VAD handled entirely server-side)


FLOW 2 — Server → Ears (TTS playback, sentence-by-sentence)
─────────────────────────────────────────────────────────────
Binary WebSocket frame: [msgType][speakerId][genId][frameSeq] + PCM bytes
  → WebSocketTransport.dispatch(.data)
  → extract speakerId and genId from 4-byte header
  → onAudioFrame(pcmData, speakerId, genId)
  → AudioPlaybackEngine.enqueue(pcmData, speakerId, genId)
      → guard genId >= currentGen else { discard }  ← zombie audio prevention
  → AVAudioPCMBuffer (16 kHz int16)                 ← matches TTS output contract
  → AVAudioPlayerNode[speakerId].scheduleBuffer()   ← queued, plays back-to-back
  → AVAudioEngine mainMixerNode → hardware output
  → Speaker / AirPods / earpiece

  Note: agent audio arrives sentence-by-sentence (streaming TTS),
  so first audio plays ~1-2 s after dispatch — not after full response.


FLOW 3 — Barge-in (two-trigger, whichever fires first)
────────────────────────────────────────────────────────
Trigger A — client side (~0 ms):
  mic RMS > noiseFloor * 5.623 (+15 dB) while isAIPlaying == true
  (noiseFloor is an EMA updated during quiet periods — adapts to environment)
  (hardware AEC via .voiceChat mode prevents speaker bleed from triggering this)
    → currentGen &+= 1
    → transport.sendInterrupt()      ← JSON text frame {"type":"interrupt"}
    → handleBargein(newGen: currentGen)
    → playback.flushAllAndStop(newGen: currentGen)  ← all nodes stop instantly
    → future frames with genId < currentGen are discarded by enqueue()

Trigger B — server side (~200 ms):
  Gemini detects user speech
    → backend cancels Claude tasks + increments session gen_id
    → backend sends JSON {"type":"interruption", "gen_id": N}
    → ConversationSession.handleBargein(newGen: max(serverGen, currentGen))
    → playback.flushAllAndStop(newGen: currentGen)  ← idempotent, safe to call twice


FLOW 4 — System interruption (phone call / Siri / alarm)
──────────────────────────────────────────────────────────
AVAudioSession.interruptionNotification (.began)
  → currentGen &+= 1
  → playback.flushAllAndStop(newGen: currentGen)
  → WebSocket stays open (session survives the interruption)
AVAudioSession.interruptionNotification (.ended)
  → engine.start() → capture resumes automatically
```
