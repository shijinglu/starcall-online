import AVFoundation
import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// Delegate for session events that the ViewModel observes.
protocol ConversationSessionDelegate: AnyObject {
    func sessionDidChangeState(_ state: SessionState)
    func sessionDidReceiveTranscript(_ json: [String: Any])
    func sessionDidReceiveAgentStatus(_ event: AgentStatusEvent)
    func sessionDidReceiveMeetingStatus(_ event: MeetingStatusEvent)
    func sessionDidReceiveAgentComm(_ event: AgentCommEvent)
    func sessionDidReceiveBargeIn(currentGenId: Int)
    func sessionDidReceiveError(_ message: String)
    func sessionDidUpdateMicAmplitude(_ amplitude: Float)
    func sessionDidUpdatePlayingSpeaker(_ speakerId: UInt8?)
}

/// Central coordinator and state machine for a conversation session.
///
/// States: idle -> connecting -> active -> stopped
///
/// Manages gen_id tracking, barge-in (dual trigger: local RMS + server interruption),
/// audio routing between capture/playback engines and WebSocket transport.
final class ConversationSession: NSObject {

    // MARK: - Public State

    /// Current session state.
    private(set) var state: SessionState = .idle {
        didSet { delegate?.sessionDidChangeState(state) }
    }

    /// Current generation counter for zombie audio prevention.
    private(set) var currentGen: UInt8 = 0

    /// The session ID from the backend.
    private(set) var sessionId: String?

    // MARK: - Dependencies

    weak var delegate: ConversationSessionDelegate?

    let httpClient: HTTPClient
    let transport: WebSocketTransport
    let audioCaptureEngine: AudioCaptureEngine
    let playbackEngine: AudioPlaybackEngine

    /// Single shared AVAudioEngine for both capture and playback.
    /// Sharing one engine enables hardware AEC (Acoustic Echo Cancellation)
    /// so the mic input is cleaned of speaker bleed.
    private let sharedAudioEngine: AVAudioEngine

    /// Base URL for the backend server.
    var baseURL: URL

    /// Whether microphone input is muted (audio capture continues but chunks are not sent).
    private(set) var isMuted: Bool = false

    /// Frame sequence counter for outbound audio chunks.
    private var frameSeq: UInt8 = 0

    /// Error message from the last failure.
    private(set) var errorMessage: String?

    // MARK: - Time-Based Audio Gate
    /// Absolute time when the playback gate should reopen.
    /// Audio chunks are dropped while CFAbsoluteTimeGetCurrent() < gateEndTime.
    /// This replaces the isPlaying-based gate which suffered from AVAudioPlayerNode
    /// keeping isPlaying=true after buffers finish.
    private var gateEndTime: CFAbsoluteTime = 0
    /// Extra margin after last audio chunk before gate reopens (seconds).
    private let gateMargin: Double = 0.3

    // MARK: - Echo Diagnostics
    /// Track chunks sent to backend during playback vs silence.
    private var diagChunksSentDuringPlayback: Int = 0
    private var diagChunksSentDuringSilence: Int = 0
    /// DIAG: track gate transitions
    private var diagLastGateState: Bool = false
    private var diagBinaryFramesReceived: Int = 0
    /// DIAG: timestamp of last binary frame received
    private var diagLastBinaryFrameTime: CFAbsoluteTime = 0
    /// Tracks the last speaker reported to the delegate to avoid redundant UI updates.
    private var lastReportedPlayingSpeaker: UInt8? = nil

    // MARK: - Init

    init(
        httpClient: HTTPClient = HTTPClient(),
        transport: WebSocketTransport = WebSocketTransport(),
        audioCaptureEngine: AudioCaptureEngine? = nil,
        playbackEngine: AudioPlaybackEngine? = nil,
        baseURL: URL = HTTPClient.defaultServerURL
    ) {
        self.httpClient = httpClient
        self.transport = transport

        // Create a single shared AVAudioEngine for both capture and playback.
        // This allows hardware AEC to correlate speaker output with mic input.
        let shared = AVAudioEngine()
        self.sharedAudioEngine = shared
        self.audioCaptureEngine = audioCaptureEngine ?? AudioCaptureEngine(sharedEngine: shared)
        self.playbackEngine = playbackEngine ?? AudioPlaybackEngine(sharedEngine: shared)

        self.baseURL = baseURL
        super.init()

        self.transport.delegate = self
        self.audioCaptureEngine.delegate = self
    }

    // MARK: - Session Lifecycle

    /// Start a new conversation session.
    ///
    /// 1. POST /sessions to get session_id + auth_token
    /// 2. Open WebSocket with the token
    /// 3. Start audio capture
    /// Reset the session to idle so it can be started again.
    func resetToIdle() {
        state = .idle
    }

    func start() async throws {
        guard state == .idle else { return }
        state = .connecting
        Log.info("Starting session, baseURL=\(baseURL)", tag: "ConversationSession")

        do {
            // 1. Create session via REST.
            let (newSessionId, authToken) = try await httpClient.createSession(serverURL: baseURL)
            self.sessionId = newSessionId
            Log.info("Session created: \(newSessionId)", tag: "ConversationSession")

            // 2. Open WebSocket.
            let wsURL = buildWebSocketURL()
            Log.info("Connecting WebSocket: \(wsURL)", tag: "ConversationSession")
            transport.connect(token: authToken, serverURL: wsURL)

            // 3. Configure audio session and start shared engine.
            do {
                try audioCaptureEngine.configureAudioSession()
                Log.info("Audio session configured (.voiceChat AEC)", tag: "ConversationSession")
            } catch {
                Log.error("configureAudioSession failed: \(error)", tag: "ConversationSession")
                throw error
            }

            // Enable hardware voice processing (AEC) on the input node.
            // This is critical — without it, the mic picks up TTS playback,
            // causing false barge-in (design doc section 1A).
            do {
                try sharedAudioEngine.inputNode.setVoiceProcessingEnabled(true)
                Log.info("Voice processing (AEC) enabled on input node", tag: "ConversationSession")
            } catch {
                Log.error("Failed to enable voice processing: \(error)", tag: "ConversationSession")
            }

            // Start the shared AVAudioEngine once — both capture and playback
            // use this single engine so hardware AEC can cancel speaker bleed.
            do {
                try playbackEngine.start()  // attaches player nodes to shared engine
                audioCaptureEngine.startCapture()  // installs input tap on shared engine
                try sharedAudioEngine.start()
                Log.info("Shared AVAudioEngine started (capture + playback on single engine for AEC)", tag: "ConversationSession")
            } catch {
                Log.error("Shared audio engine start failed: \(error)", tag: "ConversationSession")
                throw error
            }

            state = .active
            currentGen = 0
            frameSeq = 0
            Log.info("Session active", tag: "ConversationSession")
        } catch {
            state = .idle
            errorMessage = error.localizedDescription
            Log.error("Session start failed: \(error)", tag: "ConversationSession")
            throw error
        }
    }

    /// Stop the current conversation session.
    func stop() async {
        let isMain = Thread.current.isMainThread
        let callerThread = isMain ? "MAIN" : (Thread.current.name ?? "bg-\(Unmanaged.passUnretained(Thread.current).toOpaque())")
        Log.info("DIAG-STOP: stop() ENTERED state=\(state) callerThread=\(callerThread) isMainThread=\(isMain)", tag: "ConversationSession")
        state = .stopped

        // Stop audio capture (removes input tap).
        audioCaptureEngine.stopCapture()
        Log.info("DIAG-STOP: capture stopped", tag: "ConversationSession")

        // Send stop control message.
        transport.sendJSON(["type": "control", "action": "stop"])

        // Close WebSocket.
        transport.disconnect()
        Log.info("DIAG-STOP: WebSocket disconnected", tag: "ConversationSession")

        // Stop playback (clears queues and player nodes).
        Log.info("DIAG-STOP: BEFORE flushAllAndStop callerThread=\(callerThread)", tag: "ConversationSession")
        playbackEngine.flushAllAndStop(newGen: currentGen)
        Log.info("DIAG-STOP: AFTER flushAllAndStop", tag: "ConversationSession")
        playbackEngine.stop()

        // Stop the shared audio engine last.
        sharedAudioEngine.stop()
        Log.info("DIAG-STOP: all engines stopped", tag: "ConversationSession")

        // Delete session on the backend.
        if let sid = sessionId {
            try? await httpClient.deleteSession(sessionId: sid, serverURL: baseURL)
        }

        sessionId = nil
    }

    // MARK: - Haptic Feedback

    #if canImport(UIKit)
    private let hapticGenerator = UIImpactFeedbackGenerator(style: .light)
    #endif
    private var lastHapticTime: CFAbsoluteTime = 0
    private let hapticDedupeInterval: Double = 0.5

    /// Fire a subtle haptic tap for barge-in acknowledgment.
    /// Deduplicated so dual triggers (local + server) don't double-tap.
    private func fireHaptic() {
        let now = CFAbsoluteTimeGetCurrent()
        guard now - lastHapticTime >= hapticDedupeInterval else { return }
        lastHapticTime = now
        #if canImport(UIKit)
        hapticGenerator.impactOccurred()
        #endif
    }

    // MARK: - Barge-In

    /// Handle a barge-in event (dual trigger: local RMS or server interruption).
    ///
    /// Increments currentGen, flushes playback, and sends interrupt to the server.
    func handleBargein() {
        guard state == .active else {
            Log.warning("DIAG: handleBargein SKIPPED, state=\(state)", tag: "ConversationSession")
            return
        }

        let bargeinStart = CFAbsoluteTimeGetCurrent()
        let isMain = Thread.current.isMainThread
        let callerThread = isMain ? "MAIN" : (Thread.current.name ?? "bg-\(Unmanaged.passUnretained(Thread.current).toOpaque())")
        Log.info("DIAG-BARGEIN: START currentGen=\(currentGen) callerThread=\(callerThread) isMainThread=\(isMain)", tag: "ConversationSession")

        // Don't bump currentGen locally — the server is authoritative on gen_id.
        Log.info("DIAG-BARGEIN: BEFORE flushAllAndStop callerThread=\(callerThread)", tag: "ConversationSession")
        playbackEngine.flushAllAndStop(newGen: currentGen)
        let afterFlush = CFAbsoluteTimeGetCurrent()
        let flushMs = (afterFlush - bargeinStart) * 1000
        Log.info("DIAG-BARGEIN: AFTER flushAllAndStop took=\(String(format: "%.1f", flushMs))ms callerThread=\(callerThread)", tag: "ConversationSession")

        audioCaptureEngine.notifyPlaybackFlushed()
        gateEndTime = 0  // Immediately reopen the time-based gate on barge-in
        transport.sendJSON(["type": "interrupt", "mode": "cancel_all"])
        fireHaptic()
        delegate?.sessionDidReceiveBargeIn(currentGenId: Int(currentGen))

        let totalMs = (CFAbsoluteTimeGetCurrent() - bargeinStart) * 1000
        Log.info("DIAG-BARGEIN: DONE total=\(String(format: "%.1f", totalMs))ms flush=\(String(format: "%.1f", flushMs))ms callerThread=\(callerThread)", tag: "ConversationSession")
    }

    /// Handle server-side interruption confirmation.
    ///
    /// Fix 1: Server is authoritative. Use the server value directly.
    /// Do NOT use max(currentGen, serverGenId): max() is broken at the 255->0 wrap.
    func handleInterruptionConfirmed(serverGenId: UInt8) {
        currentGen = serverGenId
    }

    /// Handle a server interruption JSON message.
    func handleServerInterruption(genId: UInt8) {
        let start = CFAbsoluteTimeGetCurrent()
        let isMain = Thread.current.isMainThread
        let callerThread = isMain ? "MAIN" : (Thread.current.name ?? "bg-\(Unmanaged.passUnretained(Thread.current).toOpaque())")
        Log.info("DIAG-INTERRUPT: handleServerInterruption START genId=\(genId) currentGen=\(currentGen) callerThread=\(callerThread)", tag: "ConversationSession")
        handleInterruptionConfirmed(serverGenId: genId)
        Log.info("DIAG-INTERRUPT: BEFORE flushAllAndStop callerThread=\(callerThread)", tag: "ConversationSession")
        playbackEngine.flushAllAndStop(newGen: currentGen)
        let flushMs = (CFAbsoluteTimeGetCurrent() - start) * 1000
        Log.info("DIAG-INTERRUPT: AFTER flushAllAndStop took=\(String(format: "%.1f", flushMs))ms", tag: "ConversationSession")
        audioCaptureEngine.notifyPlaybackFlushed()
        gateEndTime = 0  // Immediately reopen the time-based gate
        fireHaptic()
        delegate?.sessionDidReceiveBargeIn(currentGenId: Int(currentGen))
        let totalMs = (CFAbsoluteTimeGetCurrent() - start) * 1000
        Log.info("DIAG-INTERRUPT: handleServerInterruption DONE took=\(String(format: "%.1f", totalMs))ms callerThread=\(callerThread)", tag: "ConversationSession")
    }

    // MARK: - Mute

    /// Set muted state. When muted, audio chunks are not sent to the server.
    func setMuted(_ muted: Bool) {
        isMuted = muted
    }

    // MARK: - Skip Speaker

    /// Send a skip_speaker interrupt for the currently playing agent.
    func sendSkipSpeaker() {
        transport.sendJSON(["type": "interrupt", "mode": "skip_speaker"])
        if let speakerId = playbackEngine.currentMeetingSpeaker {
            playbackEngine.cancelStream(speakerId: speakerId)
        }
    }

    // MARK: - Message Handlers

    /// Parse and route a transcript JSON message.
    func handleTranscript(_ json: [String: Any]) {
        delegate?.sessionDidReceiveTranscript(json)
    }

    /// Parse and route an agent_status JSON message.
    func handleAgentStatus(_ json: [String: Any]) {
        guard let agentName = json["agent_name"] as? String,
              let agentSessionId = json["agent_session_id"] as? String,
              let statusStr = json["status"] as? String,
              let status = AgentStatusKind(rawValue: statusStr) else { return }

        let elapsedMs = json["elapsed_ms"] as? Int
        let genId = json["gen_id"] as? Int ?? 0

        let event = AgentStatusEvent(
            agentName: agentName,
            agentSessionId: agentSessionId,
            status: status,
            elapsedMs: elapsedMs,
            genId: genId
        )
        delegate?.sessionDidReceiveAgentStatus(event)
    }

    /// Parse and route a meeting_status JSON message.
    func handleMeetingStatus(_ json: [String: Any]) {
        guard let genId = json["gen_id"] as? Int,
              let totalAgents = json["total_agents"] as? Int,
              let completed = json["completed"] as? Int,
              let pending = json["pending"] as? [String],
              let failed = json["failed"] as? [String] else { return }

        let event = MeetingStatusEvent(
            genId: genId,
            totalAgents: totalAgents,
            completed: completed,
            pending: pending,
            failed: failed
        )

        // Activate meeting mode if we have multiple agents.
        if totalAgents > 1 {
            playbackEngine.meetingQueueActive = true
        }

        delegate?.sessionDidReceiveMeetingStatus(event)
    }

    /// Handle a server error JSON message.
    func handleError(_ json: [String: Any]) {
        let message = json["message"] as? String ?? "Unknown error"
        let code = json["code"] as? String ?? "UNKNOWN"
        errorMessage = "[\(code)] \(message)"
        delegate?.sessionDidReceiveError(errorMessage!)
    }

    /// Parse and route an agent_comm JSON message.
    func handleAgentComm(_ json: [String: Any]) {
        guard let fromAgent = json["from_agent"] as? String,
              let text = json["text"] as? String else { return }

        let toAgent = json["to_agent"] as? String
        let genId = json["gen_id"] as? Int ?? 0

        let event = AgentCommEvent(
            fromAgent: fromAgent,
            toAgent: toAgent,
            text: text,
            genId: genId
        )
        delegate?.sessionDidReceiveAgentComm(event)
    }

    // MARK: - Helpers

    /// Build the WebSocket URL from the base URL.
    private func buildWebSocketURL() -> URL {
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!

        // Switch scheme to ws/wss.
        if components.scheme == "https" {
            components.scheme = "wss"
        } else {
            components.scheme = "ws"
        }

        components.path = "/api/v1/conversation/live"
        return components.url!
    }
}

// MARK: - WebSocketTransportDelegate

extension ConversationSession: WebSocketTransportDelegate {

    func transportDidReceiveBinaryFrame(_ data: Data) {
        let frameStart = CFAbsoluteTimeGetCurrent()
        guard let header = AudioFrameHeader(data: data) else {
            Log.warning("DIAG: transportDidReceiveBinaryFrame invalid header, dataSize=\(data.count)", tag: "ConversationSession")
            return
        }
        let pcm = Data(data.dropFirst(AudioFrameHeader.size))
        diagBinaryFramesReceived += 1
        diagLastBinaryFrameTime = frameStart

        // Extend the time-based audio gate: each incoming audio chunk adds its
        // duration to the gate end time so the gate stays closed until all
        // enqueued audio finishes playing. Margin is applied only in the gate
        // check, not per-chunk, to avoid accumulating excess gate time.
        let chunkDuration = Double(pcm.count) / (16000.0 * 2.0)  // 16kHz int16 mono
        let now = CFAbsoluteTimeGetCurrent()
        gateEndTime = max(gateEndTime, now) + chunkDuration

        // Notify capture engine about playback for barge-in gating (time-based).
        audioCaptureEngine.notifyPlaybackChunk(durationSeconds: chunkDuration)
        playbackEngine.receiveAudioFrame(header: header, pcm: pcm)

        if diagBinaryFramesReceived == 1 || diagBinaryFramesReceived % 50 == 0 {
            Log.info("DIAG-GATE: receiveBinaryFrame #\(diagBinaryFramesReceived) speaker=\(header.speakerId) pcmBytes=\(pcm.count)", tag: "ConversationSession")
        }
        // Track speaker from frame header (no node.isPlaying polling).
        let newSpeaker = header.speakerId
        if newSpeaker != lastReportedPlayingSpeaker {
            lastReportedPlayingSpeaker = newSpeaker
            delegate?.sessionDidUpdatePlayingSpeaker(newSpeaker)
        }
        let frameMs = (CFAbsoluteTimeGetCurrent() - frameStart) * 1000
        if frameMs > 5.0 {
            Log.warning("DIAG-FREEZE: transportDidReceiveBinaryFrame SLOW frame#\(diagBinaryFramesReceived) took=\(String(format: "%.1f", frameMs))ms speaker=\(header.speakerId)", tag: "ConversationSession")
        }
    }

    func transportDidReceiveTextFrame(_ text: String) {
        Log.info("DIAG: textFrame received: \(text.prefix(200))", tag: "ConversationSession")
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else { return }

        switch type {
        case "transcript":
            handleTranscript(json)
        case "agent_status":
            handleAgentStatus(json)
        case "meeting_status":
            handleMeetingStatus(json)
        case "interruption":
            let genId = UInt8(json["gen_id"] as? Int ?? 0)
            handleServerInterruption(genId: genId)
        case "agent_comm":
            handleAgentComm(json)
        case "error":
            handleError(json)
        default:
            Log.info("DIAG: unknown message type '\(type)'", tag: "ConversationSession")
            break
        }
    }

    func transportRequiresReauthentication() {
        // Token was consumed or backend restarted. Create a fresh session.
        guard state == .active else { return }
        Task {
            do {
                let (newSessionId, newToken) = try await httpClient.createSession(serverURL: baseURL)
                self.sessionId = newSessionId
                let wsURL = buildWebSocketURL()
                transport.connect(token: newToken, serverURL: wsURL)
                Log.info("Reauthenticated with new session \(newSessionId)", tag: "ConversationSession")
            } catch {
                // Backend may still be starting up. Schedule another attempt
                // using the transport's exponential backoff.
                Log.warning("Reauthentication failed (backend down?): \(error.localizedDescription)", tag: "ConversationSession")
                transport.scheduleReauthentication()
            }
        }
    }

    func transportDidDisconnect(error: Error?) {
        // Transport handles reconnection internally with exponential backoff.
        Log.warning("DIAG: Transport disconnected, state=\(state), error=\(error?.localizedDescription ?? "none")", tag: "ConversationSession")
    }
}

// MARK: - AudioCaptureEngineDelegate

extension ConversationSession: AudioCaptureEngineDelegate {

    func audioCaptureDidDetectBargein() {
        // When muted, ignore barge-in — the user muted their mic,
        // so any RMS spike is speaker bleed, not intentional speech.
        // TTS playback should continue uninterrupted.
        guard !isMuted else { return }
        let isMain = Thread.current.isMainThread
        let callerThread = isMain ? "MAIN" : (Thread.current.name ?? "bg-\(Unmanaged.passUnretained(Thread.current).toOpaque())")
        Log.info("DIAG-BARGEIN: audioCaptureDidDetectBargein ENTERED gen=\(currentGen) callerThread=\(callerThread) isMainThread=\(isMain)", tag: "ConversationSession")
        handleBargein()
        Log.info("DIAG-BARGEIN: audioCaptureDidDetectBargein RETURNED callerThread=\(callerThread)", tag: "ConversationSession")
    }

    func audioCaptureDidProduceChunk(_ data: Data) {
        guard state == .active else { return }

        // When muted, still compute amplitude for UI but don't send audio.
        guard !isMuted else {
            delegate?.sessionDidUpdateMicAmplitude(0.0)
            return
        }

        // Time-based audio gate: drop audio while TTS is expected to be playing.
        // This replaces the isPlaying-based gate which suffered from
        // AVAudioPlayerNode.isPlaying staying true after buffers finish.
        let now = CFAbsoluteTimeGetCurrent()
        let isGateClosed = now < gateEndTime + gateMargin

        // DIAG: log gate state transitions
        if isGateClosed != diagLastGateState {
            Log.info("DIAG-GATE: audio gate \(isGateClosed ? "CLOSED (dropping chunks)" : "OPEN (sending chunks)") skipped=\(diagChunksSentDuringPlayback) sent=\(diagChunksSentDuringSilence) gateRemaining=\(String(format: "%.2f", max(0, gateEndTime - now)))s", tag: "ConversationSession")
            diagLastGateState = isGateClosed
        }

        if isGateClosed {
            diagChunksSentDuringPlayback += 1
            if diagChunksSentDuringPlayback == 1 || diagChunksSentDuringPlayback % 100 == 0 {
                Log.info("DIAG-GATE: chunk DROPPED #\(diagChunksSentDuringPlayback) gateRemaining=\(String(format: "%.2f", gateEndTime - now))s", tag: "ConversationSession")
            }
            // Still update UI amplitude but don't send to backend.
            let samples = data.withUnsafeBytes { rawBuffer -> [Int16] in
                guard let base = rawBuffer.baseAddress else { return [] }
                let bound = base.bindMemory(to: Int16.self, capacity: rawBuffer.count / MemoryLayout<Int16>.size)
                return Array(UnsafeBufferPointer(start: bound, count: rawBuffer.count / MemoryLayout<Int16>.size))
            }
            let rms = audioCaptureEngine.computeRMS(samples)
            delegate?.sessionDidUpdateMicAmplitude(rms)
            return
        }

        diagChunksSentDuringSilence += 1

        // Send the 100ms PCM chunk as a binary WS frame.
        transport.sendAudioChunk(data, frameSeq: frameSeq)
        frameSeq = frameSeq &+ 1

        // Update mic amplitude for UI waveform visualization.
        let samples = data.withUnsafeBytes { rawBuffer -> [Int16] in
            guard let base = rawBuffer.baseAddress else { return [] }
            let bound = base.bindMemory(to: Int16.self, capacity: rawBuffer.count / MemoryLayout<Int16>.size)
            return Array(UnsafeBufferPointer(start: bound, count: rawBuffer.count / MemoryLayout<Int16>.size))
        }
        let rms = audioCaptureEngine.computeRMS(samples)
        if frameSeq == 1 || frameSeq % 100 == 0 {
            Log.info("DIAG-ECHO: SENT_TO_GEMINI chunk#\(frameSeq) rms=\(rms) totalSkippedDuringPlayback=\(diagChunksSentDuringPlayback) totalSent=\(diagChunksSentDuringSilence)", tag: "ConversationSession")
        }
        delegate?.sessionDidUpdateMicAmplitude(rms)
    }
}
