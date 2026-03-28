# Phase 4 — iOS App: Audio Capture, Playback, WebSocket & Session State Machine

## Goal
Build the native Swift iOS app. Audio is captured at hardware level, downsampled, and streamed to the backend. Incoming binary frames are decoded and played back through the appropriate per-agent stream. Barge-in is detected locally via RMS analysis and confirmed server-side via `gen_id`. The UI shows live transcripts, agent statuses, and meeting progress.

---

## 4.1 AudioCaptureEngine

### Responsibilities
- Configure `AVAudioSession` for voice-first operation.
- Tap the microphone via `AVAudioEngine` at 44.1 kHz.
- Downsample to 16 kHz int16 PCM in 100 ms chunks (3,200 bytes each).
- Compute per-chunk RMS and compare against an adaptive noise floor.
- Emit barge-in signal when speech is detected during AI playback.

### AVAudioSession Setup

```swift
func configureAudioSession() throws {
    let session = AVAudioSession.sharedInstance()
    // .voiceChat mode enables hardware AEC (Acoustic Echo Canceller) and AGC.
    // AEC prevents speaker playback from triggering barge-in false positives.
    try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth])
    try session.setActive(true)
}
```

### AVAudioEngine Tap & Downsampling

```swift
func startCapture() {
    let inputNode = audioEngine.inputNode
    let inputFormat = inputNode.outputFormat(forBus: 0)  // native format, e.g. 44.1 kHz float32

    let targetFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 16000,
        channels: 1,
        interleaved: true
    )!

    // AVAudioConverter handles the 44.1 kHz → 16 kHz downsampling
    let converter = AVAudioConverter(from: inputFormat, to: targetFormat)!

    inputNode.installTap(onBus: 0, bufferSize: 4410, format: inputFormat) { [weak self] buffer, _ in
        self?.processBuffer(buffer, converter: converter, targetFormat: targetFormat)
    }
    try! audioEngine.start()
}
```

### RMS Barge-In Detection

```swift
class AudioCaptureEngine {
    private var noiseFloor: Float = 0.001   // adaptive EMA of quiet-period RMS
    private let noiseFloorAlpha: Float = 0.1  // EMA smoothing factor
    private let bargeInThresholdDB: Float = 15.0   // dB above noise floor

    func computeRMS(_ samples: [Int16]) -> Float {
        let sumSquares = samples.reduce(0.0) { acc, s in acc + Float(s) * Float(s) }
        return sqrt(sumSquares / Float(samples.count)) / 32768.0
    }

    func processChunk(_ pcm16: Data, isPlaying: Bool) {
        let samples = pcm16.withUnsafeBytes { Array($0.bindMemory(to: Int16.self)) }
        let rms = computeRMS(samples)

        // Update adaptive noise floor during quiet periods (when not playing)
        if !isPlaying {
            noiseFloor = noiseFloorAlpha * rms + (1 - noiseFloorAlpha) * noiseFloor
        }

        // Barge-in: RMS exceeds noise floor by 15 dB while audio is playing
        if isPlaying {
            let rmsDB = 20 * log10(rms / max(noiseFloor, 1e-10))
            if rmsDB > bargeInThresholdDB {
                delegate?.audioCaptureDidDetectBargein()
            }
        }

        // Always send the 100ms PCM chunk (even during playback — AEC cleans it)
        delegate?.audioCaptureDidProduceChunk(pcm16)
    }
}
```

### Noise Floor Update Schedule

The noise floor EMA is updated every 500 ms of quiet-period accumulation:
- Quiet period: `!isPlaying && rms < noiseFloor * 2`
- Update: `noiseFloor = alpha * currentRMS + (1 - alpha) * noiseFloor`

---

## 4.2 AudioPlaybackEngine

### Responsibilities
- Maintain a per-stream FIFO queue keyed by `speaker_id`.
- Filter out frames with `gen_id < current_gen` (zombie audio prevention).
- In Meeting Mode, play streams strictly in arrival-completion order.
- Expose `flushAllAndStop(newGen:)` for barge-in and `cancelStream(speakerId:)` for individual cancellation.

### Stream Queue

```swift
class AudioPlaybackEngine {
    // One AVAudioPlayerNode per speaker_id (0=moderator, 1-4=agents)
    private var playerNodes: [UInt8: AVAudioPlayerNode] = [:]
    private var frameQueues: [UInt8: [Data]] = [:]  // buffered PCM per speaker
    private var currentGen: UInt8 = 0

    var meetingQueueActive = false
    private var meetingOrder: [UInt8] = []   // speaker_ids in dispatch order
    private var currentMeetingSpeaker: UInt8? = nil
```

### Receiving a Frame

```swift
func receiveAudioFrame(header: AudioFrameHeader, pcm: Data) {
    // Fix 1: Zombie audio prevention using RFC 1982 modular arithmetic.
    // A naive `header.genId >= currentGen` comparison breaks at the 0/255 wrap boundary.
    guard !isStale(frameGen: header.genId, currentGen: currentGen) else {
        return   // silently discard stale frame
    }

    // Fix 1: Helper — RFC 1982 circular sequence number staleness check
    // func isStale(frameGen: UInt8, currentGen: UInt8) -> Bool {
    //     let diff = Int(currentGen &- frameGen) & 0xFF
    //     return diff > 0 && diff < 128
    // }

    if meetingQueueActive {
        // Buffer for sequential delivery
        frameQueues[header.speakerId, default: []].append(pcm)
        maybeStartNextMeetingSpeaker()
    } else {
        // Direct playback
        enqueueForPlayback(speakerId: header.speakerId, pcm: pcm)
    }
}
```

### Barge-In Flush

```swift
func flushAllAndStop(newGen: UInt8) {
    currentGen = newGen
    // Stop all player nodes and clear all queues
    for (_, node) in playerNodes {
        node.stop()
    }
    frameQueues.removeAll()
    meetingOrder.removeAll()
    currentMeetingSpeaker = nil
    meetingQueueActive = false
}
```

### Skip Speaker (Meeting Mode)

```swift
func cancelStream(speakerId: UInt8) {
    playerNodes[speakerId]?.stop()
    frameQueues[speakerId]?.removeAll()
    // Advance to next meeting speaker
    if let idx = meetingOrder.firstIndex(of: speakerId) {
        meetingOrder.remove(at: idx)
    }
    maybeStartNextMeetingSpeaker()
}
```

### Meeting Mode Sequential Delivery

```swift
func maybeStartNextMeetingSpeaker() {
    guard meetingQueueActive,
          currentMeetingSpeaker == nil,
          let nextSpeaker = meetingOrder.first else { return }

    let queue = frameQueues[nextSpeaker] ?? []
    guard !queue.isEmpty else { return }   // wait for frames

    currentMeetingSpeaker = nextSpeaker
    // Schedule all buffered frames on the player node
    drainQueueToPlayer(speakerId: nextSpeaker)
}

func onSpeakerFinished(speakerId: UInt8) {
    // Called by AVAudioPlayerNode completion callback
    if speakerId == currentMeetingSpeaker {
        currentMeetingSpeaker = nil
        meetingOrder.removeFirst()
        maybeStartNextMeetingSpeaker()
    }
}
```

---

## 4.3 WebSocketTransport

### Responsibilities
- Manage WS connection lifecycle with exponential-backoff reconnect.
- Send audio as **binary** WS frames (4-byte header + raw PCM).
- Send control/interrupt as **JSON text** WS frames.
- Parse inbound frame type (binary vs. text) and dispatch to the appropriate handler.

### Connection

```swift
class WebSocketTransport {
    private var webSocketTask: URLSessionWebSocketTask?
    private var reconnectDelay: TimeInterval = 1.0
    private let maxReconnectDelay: TimeInterval = 30.0

    func connect(token: String, serverURL: URL) {
        var components = URLComponents(url: serverURL, resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "token", value: token)]
        let request = URLRequest(url: components.url!)
        webSocketTask = URLSession.shared.webSocketTask(with: request)
        webSocketTask?.resume()
        receive()   // start receive loop
        reconnectDelay = 1.0   // reset backoff on successful connect
    }

    func disconnect() {
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
    }
```

### Sending

```swift
    func sendAudioChunk(_ pcm: Data, frameSeq: UInt8) {
        var header = Data([MsgType.audioChunk.rawValue, 0x00, 0x00, frameSeq])
        header.append(pcm)
        let message = URLSessionWebSocketTask.Message.data(header)
        webSocketTask?.send(message) { _ in }
    }

    func sendJSON(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else { return }
        webSocketTask?.send(.string(text)) { _ in }
    }
```

### Receiving & Dispatch

```swift
    func receive() {
        webSocketTask?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                switch message {
                case .data(let data):
                    self.delegate?.transportDidReceiveBinaryFrame(data)
                case .string(let text):
                    self.delegate?.transportDidReceiveTextFrame(text)
                @unknown default: break
                }
                self.receive()   // recurse for next message

            case .failure(let error):
                // Fix 2: Distinguish 401 (token consumed) from network errors.
                // A 401 means the single-use token is dead — retry with backoff would
                // loop forever. Instead, request a fresh session from ConversationSession.
                let nsError = error as NSError
                if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorUserAuthenticationRequired {
                    self.delegate?.transportRequiresReauthentication()
                } else {
                    self.handleDisconnect(error: error)
                }
            }
        }
    }

    func handleDisconnect(error: Error) {
        // Exponential backoff reconnect (for network errors only — not 401)
        DispatchQueue.main.asyncAfter(deadline: .now() + reconnectDelay) { [weak self] in
            self?.reconnect()
        }
        reconnectDelay = min(reconnectDelay * 2, maxReconnectDelay)
    }
```

---

## 4.4 ConversationSession (State Machine)

### States

```swift
enum SessionState: String {
    case idle
    case connecting    // POST /sessions in flight
    case active        // WS open, audio streaming
    case stopped       // user tapped stop or error terminal
}
```

### State Transitions

```
idle ──── tap Start ──────────────────► connecting
connecting ── WS opened ──────────────► active
active ──── tap Stop ─────────────────► stopped
active ──── terminal error ───────────► stopped
connecting ── POST /sessions fails ───► idle (show error)
```

### gen_id Tracking

```swift
class ConversationSession {
    private(set) var currentGen: UInt8 = 0

    func handleBargein() {
        currentGen = currentGen &+ 1   // wrapping increment
        playbackEngine.flushAllAndStop(newGen: currentGen)
        transport.sendJSON(["type": "interrupt", "mode": "cancel_all"])
    }

    func handleInterruptionConfirmed(serverGenId: UInt8) {
        // Fix 1: Server is authoritative — use the server value directly.
        // Do NOT use max(currentGen, serverGenId): max() is broken at the 255→0 wrap.
        // The server increments gen_id atomically; just trust its value.
        currentGen = serverGenId
    }
}
```

### Dual-Trigger Barge-In

```swift
// Trigger 1: Local RMS detection (from AudioCaptureEngine delegate)
func audioCaptureDidDetectBargein() {
    guard state == .active else { return }
    handleBargein()
}

// Trigger 2: Server interruption confirmation (idempotent if gen already advanced)
func handleServerInterruption(genId: UInt8) {
    handleInterruptionConfirmed(serverGenId: genId)
    // No further flush needed — local flush already happened
}
```

### Inbound Frame Dispatch

```swift
func transportDidReceiveBinaryFrame(_ data: Data) {
    guard let header = AudioFrameHeader(data: data) else { return }
    let pcm = data.dropFirst(AudioFrameHeader.size)
    playbackEngine.receiveAudioFrame(header: header, pcm: Data(pcm))
}

func transportDidReceiveTextFrame(_ text: String) {
    guard let data = text.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let type = json["type"] as? String else { return }

    switch type {
    case "transcript":     handleTranscript(json)
    case "agent_status":   handleAgentStatus(json)
    case "meeting_status": handleMeetingStatus(json)
    case "interruption":   handleServerInterruption(genId: UInt8(json["gen_id"] as? Int ?? 0))
    case "error":          handleError(json)
    default:               break
    }
}
```

### Reauthentication on 401 (Fix 2)

```swift
// WebSocketTransportDelegate — ConversationSession implements this
func transportRequiresReauthentication() {
    // Token was consumed; request a completely new session (new session_id + token).
    // This is NOT a reconnect — it starts a fresh conversation.
    Task {
        do {
            let (newSessionId, newToken) = try await httpClient.createSession(serverURL: baseURL)
            self.sessionId = newSessionId
            let wsURL = baseURL.appendingPathComponent("/api/v1/conversation/live")
            transport.connect(token: newToken, serverURL: wsURL)
        } catch {
            state = .stopped
            errorMessage = "Session could not be restored: \(error.localizedDescription)"
        }
    }
}
```

---

### Session Start Flow

```swift
func start() async throws {
    state = .connecting

    // 1. POST /sessions
    let (sessionId, authToken) = try await httpClient.createSession(serverURL: baseURL)

    // 2. Open WebSocket
    let wsURL = baseURL.appendingPathComponent("/api/v1/conversation/live")
    transport.connect(token: authToken, serverURL: wsURL)

    // 3. Start audio capture
    try audioCaptureEngine.configureAudioSession()
    audioCaptureEngine.startCapture()

    state = .active
}
```

### Session Stop Flow

```swift
func stop() async {
    state = .stopped
    audioCaptureEngine.stopCapture()
    transport.sendJSON(["type": "control", "action": "stop"])
    transport.disconnect()
    try? await httpClient.deleteSession(sessionId: sessionId, serverURL: baseURL)
}
```

---

## 4.5 ConversationViewModel

SwiftUI binding layer. Decouples the session logic from the UI.

```swift
@MainActor
class ConversationViewModel: ObservableObject {
    @Published var sessionState: SessionState = .idle
    @Published var transcript: [TranscriptLine] = []
    @Published var agentStatuses: [String: AgentStatusKind] = [:]  // agent name → status
    @Published var meetingProgress: MeetingStatusEvent? = nil
    @Published var micAmplitude: Float = 0.0   // for waveform animation
    @Published var errorMessage: String? = nil

    private let session = ConversationSession()

    func tapStart() {
        Task {
            do {
                try await session.start()
                sessionState = .active
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func tapStop() {
        Task { await session.stop() }
        sessionState = .stopped
    }

    // Fix 11: Use a mutable in-progress slot for partial transcripts.
    // Without this, each partial event appends a new line — creating a flood of duplicates.
    private var inProgressTranscriptIndex: Int? = nil

    func handleTranscriptEvent(_ json: [String: Any]) {
        let text = json["text"] as? String ?? ""
        let isFinal = json["is_final"] as? Bool ?? false
        DispatchQueue.main.async {
            if isFinal {
                if let idx = self.inProgressTranscriptIndex {
                    // Replace the in-progress slot with the final text
                    self.transcript[idx] = TranscriptLine(speaker: "user", text: text, isFinal: true)
                } else {
                    self.transcript.append(TranscriptLine(speaker: "user", text: text, isFinal: true))
                }
                self.inProgressTranscriptIndex = nil
            } else {
                if let idx = self.inProgressTranscriptIndex {
                    // Update the existing in-progress slot
                    self.transcript[idx] = TranscriptLine(speaker: "user", text: text, isFinal: false)
                } else {
                    // Create a new in-progress slot
                    self.inProgressTranscriptIndex = self.transcript.count
                    self.transcript.append(TranscriptLine(speaker: "user", text: text, isFinal: false))
                }
            }
        }
    }
}
```

---

## 4.6 UI Components

### ContentView

```
┌─────────────────────────────────────────┐
│  [Conversation Transcript ScrollView]   │
│  User: "What's my risk profile?"        │
│  Moderator: "Ellen is on it!"           │
│                                         │
├─────────────────────────────────────────┤
│  [Agent Status Row]                     │
│  🟡 Ellen — thinking (12s)              │
│  ⬜ Shijing — pending                   │
│  ✅ Eva — done                          │
│  ⬜ Ming — pending                      │
├─────────────────────────────────────────┤
│  [Mic Waveform] ~~~~~~~~~~~~~~~~~~      │
│                                         │
│          [Start / Stop Button]          │
└─────────────────────────────────────────┘
```

### AgentStatusCard

Displays per-agent status with animated spinner for `thinking` state:
- `dispatched` → yellow spinner (Fix 6: same as `thinking` — no 10s grey dot gap)
- `thinking` → yellow spinner + elapsed time
- `done` → green checkmark
- `timeout` → orange warning icon
- `cancelled` → grey X

**Fix 10 — Skip Button:** When the agent is actively playing audio (`isCurrentlySpeaking == true`),
show a "Skip" button that sends `{"type":"interrupt","mode":"skip_speaker"}`.

```swift
// AgentStatusCard.swift
struct AgentStatusCard: View {
    let agentName: String
    let status: AgentStatusKind
    let isCurrentlySpeaking: Bool
    var onSkip: () -> Void

    var body: some View {
        HStack {
            StatusIcon(status: status)
            Text("\(agentName) — \(status.label)")
            Spacer()
            if isCurrentlySpeaking {
                Button("Skip", action: onSkip)
                    .buttonStyle(.bordered)
            }
        }
    }
}

// ConversationViewModel: add isCurrentlySpeaking tracking and sendSkipSpeaker
func sendSkipSpeaker() {
    session.transport.sendJSON(["type": "interrupt", "mode": "skip_speaker"])
}
```

### MeetingProgressView

Shows overall meeting progress when `meetingProgress != nil`:
```
Meeting: 2 / 4 agents done  [████████░░░░░░░░]
Remaining: shijing, ming
```

---

## 4.7 Unit Tests

All tests are Swift `XCTestCase` subclasses. No `AVAudioEngine`, no real WS connections. AVFoundation hardware components are replaced by test doubles (mock delegates, stub audio queues, fake data). Async Swift tests use `XCTestExpectation` or Swift Testing `#expect`.

**Test file locations:**
```
iOS/Tests/
├── AudioCaptureEngineTests.swift
├── AudioPlaybackEngineTests.swift
├── WebSocketTransportTests.swift
├── ConversationSessionTests.swift
└── ConversationViewModelTests.swift
```

---

### AudioCaptureEngine Tests (`AudioCaptureEngineTests.swift`)

#### ACE-01 — computeRMS returns 0 for silence
- **What:** All-zero int16 samples → RMS == 0.0.
- **Mocks:** None (pure math).
- **Input:** `[Int16](repeating: 0, count: 3200)`.
- **Verify:** `computeRMS(samples) == 0.0`.

#### ACE-02 — computeRMS returns correct value for known signal
- **What:** A full-scale sine wave of amplitude 32767 → RMS ≈ 0.7071 (1/√2 normalized).
- **Mocks:** None.
- **Input:** Pre-computed int16 samples of a 1 kHz sine at 16 kHz sample rate.
- **Verify:** `abs(computeRMS(samples) - 0.7071) < 0.01`.

#### ACE-03 — noiseFloor updates toward RMS during quiet period
- **What:** `processChunk(isPlaying: false)` with a known RMS moves `noiseFloor` toward that RMS via EMA.
- **Mocks:** Inject deterministic int16 samples with known RMS.
- **Precondition:** `noiseFloor = 0.001` (initial).
- **Verify:** After one call, `noiseFloor = 0.1 * rms + 0.9 * 0.001` (within floating-point tolerance).

#### ACE-04 — noiseFloor does NOT update while playing
- **What:** `processChunk(isPlaying: true)` does not change `noiseFloor`.
- **Verify:** `noiseFloor` is unchanged after the call.

#### ACE-05 — barge-in delegate called when RMS exceeds threshold during playback
- **What:** A loud chunk (RMS 15 dB above noiseFloor) while `isPlaying=true` fires `audioCaptureDidDetectBargein()`.
- **Mocks:** Inject a mock delegate that records calls.
- **Setup:** `noiseFloor = 0.001` (≈ -60 dBFS normalized); input RMS ≈ 0.056 (≈ 15 dB above 0.001).
- **Verify:** `mockDelegate.bargeinCallCount == 1`.

#### ACE-06 — barge-in delegate NOT called when signal is below threshold
- **What:** A quiet chunk (RMS ≤ noiseFloor + 15 dB) while playing does NOT fire barge-in.
- **Verify:** `mockDelegate.bargeinCallCount == 0`.

#### ACE-07 — audio chunk delegate always called regardless of isPlaying
- **What:** `processChunk` calls `audioCaptureDidProduceChunk` for both `isPlaying=true` and `isPlaying=false`.
- **Mocks:** Mock delegate tracking `chunkCallCount`.
- **Verify:** `chunkCallCount == 1` after each call.

#### ACE-08 — output chunk size is always 3200 bytes (100 ms at 16 kHz int16)
- **What:** The downsampled output passed to `audioCaptureDidProduceChunk` is exactly 3200 bytes.
- **Mocks:** Inject a tap buffer callback with 44.1 kHz float32 data corresponding to 100 ms.
- **Verify:** `pcm.count == 3200`.

---

### AudioPlaybackEngine Tests (`AudioPlaybackEngineTests.swift`)

#### APE-01 — receiveAudioFrame discards stale frame using RFC 1982 arithmetic (Fix 1)
- **What:** A frame with `genId=2` when `currentGen=3` is silently dropped (stale in the past half).
- **Mocks:** Mock `AVAudioPlayerNode` (do not call real audio hardware).
- **Precondition:** `currentGen = 3`.
- **Verify:** `frameQueues` is empty after the call; player node not touched.

#### APE-01b — receiveAudioFrame handles wrap-around correctly (Fix 1)
- **What:** A frame with `genId=254` when `currentGen=1` is correctly identified as stale (since 1 − 254 = −253 mod 256 = 3, which is in 1..127 → stale). A frame with `genId=255` when `currentGen=1` is also stale. A frame with `genId=128` when `currentGen=1` is NOT stale (ambiguous = accept).
- **Verify:** Frames with genId in the stale half are discarded; frames in the fresh half are accepted.

#### APE-02 — receiveAudioFrame accepts frame with genId == currentGen
- **What:** A frame with `genId=3` when `currentGen=3` is enqueued (diff=0, not stale).
- **Precondition:** `currentGen = 3`, `meetingQueueActive = false`.
- **Verify:** `frameQueues[speakerId]?.count == 1`.

#### APE-03 — receiveAudioFrame accepts frame with genId > currentGen (fresh generation)
- **What:** A frame with `genId=5` when `currentGen=3` is accepted (diff in fresh half).
- **Verify:** Frame enqueued.

#### APE-04 — flushAllAndStop updates currentGen
- **What:** `flushAllAndStop(newGen: 7)` sets `currentGen = 7`.
- **Verify:** `engine.currentGen == 7`.

#### APE-05 — flushAllAndStop clears all frame queues
- **What:** After flush, `frameQueues` is empty regardless of how many queues existed.
- **Setup:** Pre-populate `frameQueues[0x01]` and `frameQueues[0x02]` with several items.
- **Verify:** Both queues empty after `flushAllAndStop`.

#### APE-06 — flushAllAndStop resets meeting state
- **What:** `meetingQueueActive = true`, `meetingOrder = [0x01, 0x02]` → after flush, both are reset.
- **Verify:** `meetingQueueActive == false`; `meetingOrder.isEmpty`.

#### APE-07 — cancelStream removes speakerId from meetingOrder
- **What:** `cancelStream(speakerId: 0x02)` removes `0x02` from `meetingOrder`.
- **Setup:** `meetingOrder = [0x01, 0x02, 0x03]`.
- **Verify:** `meetingOrder == [0x01, 0x03]`.

#### APE-08 — cancelStream clears the frame queue for that speaker
- **What:** `cancelStream(0x02)` empties `frameQueues[0x02]`.
- **Setup:** `frameQueues[0x02] = [someData]`.
- **Verify:** `frameQueues[0x02]?.isEmpty == true`.

#### APE-09 — onSpeakerFinished advances to next meeting speaker
- **What:** When `currentMeetingSpeaker = 0x01` finishes, `currentMeetingSpeaker` becomes nil and `maybeStartNextMeetingSpeaker` is triggered.
- **Setup:** `meetingOrder = [0x01, 0x02]`; `frameQueues[0x02]` has data.
- **Verify:** After `onSpeakerFinished(0x01)`, `meetingOrder.first == 0x02` and `currentMeetingSpeaker == 0x02`.

#### APE-10 — onSpeakerFinished for non-current speaker is a no-op
- **What:** `onSpeakerFinished(0x03)` when `currentMeetingSpeaker == 0x01` does nothing.
- **Verify:** `currentMeetingSpeaker` still `0x01`; `meetingOrder` unchanged.

#### APE-11 — maybeStartNextMeetingSpeaker does nothing when meetingQueueActive is false
- **What:** Meeting mode off → `maybeStartNextMeetingSpeaker` does not set a current speaker.
- **Setup:** `meetingQueueActive = false`; `meetingOrder = [0x01]`; `frameQueues[0x01]` has data.
- **Verify:** `currentMeetingSpeaker == nil`.

#### APE-12 — receiveAudioFrame in meeting mode buffers rather than playing
- **What:** Frame arrives while `meetingQueueActive = true` → added to `frameQueues` buffer, not directly scheduled for playback.
- **Mocks:** Spy on the (mocked) player node to ensure no `scheduleBuffer` call.
- **Verify:** `frameQueues[speakerId]?.count == 1`; player node untouched.

---

### WebSocketTransport Tests (`WebSocketTransportTests.swift`)

#### WST-01 — sendAudioChunk produces correct 4-byte header
- **What:** `sendAudioChunk(pcm, frameSeq: 5)` sends a binary message whose first 4 bytes are `[0x01, 0x00, 0x00, 0x05]`.
- **Mocks:** Replace `URLSessionWebSocketTask.send` with a spy closure that captures the message.
- **Verify:** Captured data bytes 0–3 equal `[0x01, 0x00, 0x00, 0x05]`; bytes 4+ equal `pcm`.

#### WST-02 — sendAudioChunk payload follows header
- **What:** Total message length = 4 + pcm.count.
- **Verify:** Captured data.count == 4 + pcm.count.

#### WST-03 — sendJSON encodes payload as UTF-8 text frame
- **What:** `sendJSON(["type":"interrupt","mode":"cancel_all"])` sends a `.string` WS message.
- **Mocks:** Spy on `webSocketTask.send`.
- **Verify:** Message is `.string` (not `.data`); decoded JSON has `type=="interrupt"` and `mode=="cancel_all"`.

#### WST-04 — connect appends token as query parameter
- **What:** `connect(token: "abc123", serverURL: url)` constructs a URL with `?token=abc123`.
- **Mocks:** Spy on `URLSession.webSocketTask(with:)` to capture the request.
- **Verify:** Request URL contains `token=abc123` in the query string.

#### WST-05 — reconnectDelay doubles after each disconnect
- **What:** Each call to `handleDisconnect` doubles `reconnectDelay`, starting from 1.0.
- **Mocks:** Stub `DispatchQueue.asyncAfter` to a no-op so no actual timer fires.
- **Verify:** After 1st call: `reconnectDelay == 2.0`; after 2nd: `4.0`; after 3rd: `8.0`.

#### WST-06 — reconnectDelay caps at maxReconnectDelay (30 s)
- **What:** After enough disconnects, delay stays at 30.0 and does not grow further.
- **Verify:** After 10 calls to `handleDisconnect`, `reconnectDelay == 30.0`.

#### WST-07 — reconnectDelay resets to 1.0 on successful connect
- **What:** Calling `connect(...)` after a series of failures resets `reconnectDelay = 1.0`.
- **Mocks:** Suppress real WS task creation.
- **Verify:** `reconnectDelay == 1.0` after `connect`.

---

### ConversationSession Tests (`ConversationSessionTests.swift`)

#### CS-01 — initial state is idle
- **What:** Freshly created `ConversationSession` has `state == .idle` and `currentGen == 0`.
- **Mocks:** None.
- **Verify:** `state == .idle`; `currentGen == 0`.

#### CS-02 — handleBargein increments currentGen
- **What:** `handleBargein()` sets `currentGen = currentGen + 1` (wrapping).
- **Setup:** `currentGen = 4`; `state = .active`.
- **Mocks:** Mock `playbackEngine.flushAllAndStop`; mock `transport.sendJSON`.
- **Verify:** `currentGen == 5`.

#### CS-03 — handleBargein calls flushAllAndStop with new gen
- **What:** `flushAllAndStop(newGen: 5)` is called on the playback engine.
- **Verify:** Mock `flushAllAndStop` called once with argument `5`.

#### CS-04 — handleBargein sends interrupt JSON to transport
- **What:** `transport.sendJSON(["type":"interrupt","mode":"cancel_all"])` is called.
- **Verify:** `mockTransport.lastSentJSON["type"] as? String == "interrupt"`.

#### CS-05 — handleBargein is a no-op when state is not active
- **What:** In `.idle` or `.stopped` state, barge-in is ignored.
- **Setup:** `state = .idle`.
- **Verify:** `currentGen == 0` (unchanged); `flushAllAndStop` not called.

#### CS-06 — handleInterruptionConfirmed takes max of currentGen and serverGenId
- **What:** `handleInterruptionConfirmed(serverGenId: 7)` when `currentGen=5` → `currentGen=7`.
- **Verify:** `currentGen == 7`.

#### CS-07 — handleInterruptionConfirmed keeps currentGen if server is behind
- **What:** `handleInterruptionConfirmed(serverGenId: 3)` when `currentGen=5` → `currentGen=5`.
- **Verify:** `currentGen == 5`.

#### CS-08 — transportDidReceiveBinaryFrame routes to playbackEngine
- **What:** Receiving a 4-byte header + 3200-byte PCM data causes `playbackEngine.receiveAudioFrame(header:pcm:)` to be called.
- **Mocks:** Mock `playbackEngine`.
- **Input:** `Data([0x03, 0x01, 0x02, 0x00]) + Data(count: 3200)`.
- **Verify:** `mockPlaybackEngine.receiveAudioFrame` called; `header.speakerId == 0x01`; `pcm.count == 3200`.

#### CS-09 — transportDidReceiveBinaryFrame ignores frames with < 4 bytes
- **What:** A 2-byte frame does not crash and does not call `playbackEngine`.
- **Input:** `Data([0x03, 0x01])`.
- **Verify:** `mockPlaybackEngine.receiveAudioFrame` NOT called; no crash.

#### CS-10 — transportDidReceiveTextFrame routes transcript to handler
- **What:** A JSON text frame with `type="transcript"` calls `handleTranscript`.
- **Mocks:** Override `handleTranscript` to record calls.
- **Input:** `'{"type":"transcript","speaker":"user","text":"hello","is_final":true}'`.
- **Verify:** `handleTranscript` called with the parsed dict.

#### CS-11 — transportDidReceiveTextFrame routes interruption to handleServerInterruption
- **What:** JSON with `type="interruption"` and `gen_id=5` calls `handleInterruptionConfirmed(serverGenId: 5)`.
- **Mocks:** Spy on `handleInterruptionConfirmed`.
- **Verify:** Called with `5`.

#### CS-12 — transportDidReceiveTextFrame routes agent_status to handler
- **What:** JSON with `type="agent_status"` calls `handleAgentStatus`.
- **Verify:** `handleAgentStatus` called.

#### CS-13 — transportDidReceiveTextFrame ignores unknown type gracefully
- **What:** JSON with `type="future_unknown_type"` does not crash.
- **Verify:** No exception; no handler called.

#### CS-14 — transport 401 error triggers reauthentication, not backoff (Fix 2)
- **What:** When `WebSocketTransport` calls `transportRequiresReauthentication()`, `ConversationSession` calls `httpClient.createSession` to get a new token and reconnects.
- **Mocks:** `httpClient.createSession = AsyncMock(return_value=("new_session", "new_token"))`; `transport.connect = Mock()`.
- **Verify:** `httpClient.createSession` called once; `transport.connect` called with the new token, NOT the old one; `reconnectDelay` not modified (no backoff loop).

#### CS-15 — handleInterruptionConfirmed uses server value directly (Fix 1)
- **What:** `handleInterruptionConfirmed(serverGenId: 3)` when `currentGen=5` sets `currentGen = 3` (server is authoritative).
- **Verify:** `currentGen == 3` (overrides local value, unlike the old `max()` behavior).

---

### ConversationViewModel Tests (`ConversationViewModelTests.swift`)

#### VM-01 — tapStart transitions state from idle to active (happy path)
- **What:** `tapStart()` triggers `session.start()` and sets `sessionState = .active` on success.
- **Mocks:** Mock `ConversationSession.start()` to return immediately without error.
- **Verify:** After async completion, `viewModel.sessionState == .active`.

#### VM-02 — tapStart sets errorMessage on failure
- **What:** If `session.start()` throws, `errorMessage` is set and state remains `.idle`.
- **Mocks:** `session.start()` throws `URLError(.notConnectedToInternet)`.
- **Verify:** `viewModel.errorMessage != nil`; `viewModel.sessionState != .active`.

#### VM-03 — tapStop sets sessionState to stopped
- **What:** `tapStop()` immediately sets `sessionState = .stopped`.
- **Mocks:** Mock `session.stop()` as a no-op.
- **Verify:** `viewModel.sessionState == .stopped`.

#### VM-04 — agent_status thinking updates agentStatuses dict
- **What:** When `ConversationSession` fires an agent status update, `viewModel.agentStatuses["ellen"]` becomes `.thinking`.
- **Mocks:** Use a test-only callback or dependency injection to push an `AgentStatusEvent` into the ViewModel.
- **Verify:** `viewModel.agentStatuses["ellen"] == .thinking`.

#### VM-05 — meeting_status updates meetingProgress
- **What:** A `MeetingStatusEvent` updates `viewModel.meetingProgress` with the correct values.
- **Verify:** `viewModel.meetingProgress?.completed == 2`; `viewModel.meetingProgress?.totalAgents == 4`.

#### VM-06 — partial transcripts update in-progress slot, not append (Fix 11)
- **What:** Two consecutive `is_final=false` transcript events do not append two lines — they update the same slot.
- **Mocks:** Call `handleTranscriptEvent` twice with `is_final=false`.
- **Verify:** `viewModel.transcript.count == 1` (one slot); second call updates the text in place.

#### VM-07 — final transcript replaces in-progress slot then clears it (Fix 11)
- **What:** After a partial event, a `is_final=true` event replaces the slot and resets `inProgressTranscriptIndex`.
- **Mocks:** One partial event then one final event.
- **Verify:** `transcript.count == 1`; `transcript[0].isFinal == true`; subsequent partial starts a new slot.

#### VM-08 — dispatched agent status shows spinner, not grey dot (Fix 6)
- **What:** An `agent_status{status:"dispatched"}` event sets the status to the same spinner-showing kind as `thinking`.
- **Mocks:** Push `AgentStatusEvent(status: "dispatched")` into the ViewModel.
- **Verify:** `viewModel.agentStatuses["ellen"]` maps to a spinner-showing visual state (not idle/grey).

---

## Phase 4 Completion Criteria

- [ ] App compiles and runs on iOS 17+ simulator.
- [ ] `configureAudioSession()` sets `.voiceChat` mode (check in AVAudioSession logs).
- [ ] 100 ms PCM chunks (3200 bytes) sent as binary WS frames at consistent intervals.
- [ ] `audio_response` binary frames received → audio plays back through speaker.
- [ ] `agent_audio` binary frames with correct `speaker_id` route to per-agent player node.
- [ ] Frames with `gen_id < currentGen` are silently discarded (verify via frame counter in logs).
- [ ] RMS barge-in: loud audio input during playback → `interrupt` sent to backend within 100 ms.
- [ ] `interruption` from server → `currentGen` advances → old generation frames discarded.
- [ ] Meeting mode: second agent audio does not start until first agent audio completes.
- [ ] WS disconnect (network error) → exponential backoff reconnect (observed in logs: 1s, 2s, 4s, 8s…).
- [ ] WS 401 error → `transportRequiresReauthentication()` called → new session created → reconnects with fresh token (no infinite loop). (Fix 2)
- [ ] `receiveAudioFrame` uses RFC 1982 modular arithmetic for gen_id staleness — wrap-around frames are correctly discarded. (Fix 1)
- [ ] Partial transcript events update the same line slot rather than appending new lines. (Fix 11)
- [ ] Agent spinner appears immediately on dispatch (`dispatched` and `thinking` both show spinner). (Fix 6)
- [ ] Skip button appears on AgentStatusCard while an agent is audibly playing; tapping sends `interrupt{skip_speaker}`. (Fix 10)
- [ ] UI shows transcript lines, agent spinner during `thinking`, checkmark on `done`.
- [ ] `tap Stop` → audio stops, WS closes, `DELETE /sessions/{id}` called.
