import Foundation

/// Delegate for session events that the ViewModel observes.
protocol ConversationSessionDelegate: AnyObject {
    func sessionDidChangeState(_ state: SessionState)
    func sessionDidReceiveTranscript(_ json: [String: Any])
    func sessionDidReceiveAgentStatus(_ event: AgentStatusEvent)
    func sessionDidReceiveMeetingStatus(_ event: MeetingStatusEvent)
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

    /// Base URL for the backend server.
    var baseURL: URL

    /// Whether microphone input is muted (audio capture continues but chunks are not sent).
    private(set) var isMuted: Bool = false

    /// Frame sequence counter for outbound audio chunks.
    private var frameSeq: UInt8 = 0

    /// Error message from the last failure.
    private(set) var errorMessage: String?

    // MARK: - Init

    init(
        httpClient: HTTPClient = HTTPClient(),
        transport: WebSocketTransport = WebSocketTransport(),
        audioCaptureEngine: AudioCaptureEngine = AudioCaptureEngine(),
        playbackEngine: AudioPlaybackEngine = AudioPlaybackEngine(),
        baseURL: URL = HTTPClient.defaultServerURL
    ) {
        self.httpClient = httpClient
        self.transport = transport
        self.audioCaptureEngine = audioCaptureEngine
        self.playbackEngine = playbackEngine
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

            // 3. Start audio capture.
            do {
                try audioCaptureEngine.configureAudioSession()
                Log.info("Audio session configured", tag: "ConversationSession")
            } catch {
                Log.error("configureAudioSession failed: \(error)", tag: "ConversationSession")
                throw error
            }
            audioCaptureEngine.startCapture()
            Log.info("Audio capture started", tag: "ConversationSession")

            // 4. Start playback engine.
            do {
                try playbackEngine.start()
                Log.info("Playback engine started", tag: "ConversationSession")
            } catch {
                Log.error("Playback engine start failed: \(error)", tag: "ConversationSession")
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
        Log.info("DIAG: stop() called, current state=\(state) thread=\(Thread.current)", tag: "ConversationSession")
        state = .stopped

        // Stop audio capture.
        audioCaptureEngine.stopCapture()

        // Send stop control message.
        transport.sendJSON(["type": "control", "action": "stop"])

        // Close WebSocket.
        transport.disconnect()

        // Stop playback.
        playbackEngine.flushAllAndStop(newGen: currentGen)
        playbackEngine.stop()

        // Delete session on the backend.
        if let sid = sessionId {
            try? await httpClient.deleteSession(sessionId: sid, serverURL: baseURL)
        }

        sessionId = nil
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

        currentGen = currentGen &+ 1
        Log.info("DIAG: handleBargein firing, newGen=\(currentGen) thread=\(Thread.current)", tag: "ConversationSession")
        playbackEngine.flushAllAndStop(newGen: currentGen)
        transport.sendJSON(["type": "interrupt", "mode": "cancel_all"])
        Log.info("DIAG: handleBargein complete", tag: "ConversationSession")
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
        Log.info("DIAG: handleServerInterruption genId=\(genId) currentGen=\(currentGen)", tag: "ConversationSession")
        handleInterruptionConfirmed(serverGenId: genId)
        // Flush playback with server's gen_id to discard stale audio.
        playbackEngine.flushAllAndStop(newGen: currentGen)
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
        if let speakerId = playbackEngine.currentlyPlayingSpeaker {
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
        guard let header = AudioFrameHeader(data: data) else {
            Log.warning("DIAG: transportDidReceiveBinaryFrame invalid header, dataSize=\(data.count)", tag: "ConversationSession")
            return
        }
        let pcm = Data(data.dropFirst(AudioFrameHeader.size))
        playbackEngine.receiveAudioFrame(header: header, pcm: pcm)

        // Update playing state for the capture engine's barge-in detection.
        audioCaptureEngine.isPlaying = playbackEngine.isAnyPlaying
        delegate?.sessionDidUpdatePlayingSpeaker(playbackEngine.currentlyPlayingSpeaker)
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
        case "error":
            handleError(json)
        default:
            Log.info("DIAG: unknown message type '\(type)'", tag: "ConversationSession")
            break
        }
    }

    func transportRequiresReauthentication() {
        // Fix 2: Token was consumed. Request a completely new session (new session_id + token).
        // This is NOT a reconnect -- it starts a fresh conversation.
        Task {
            do {
                let (newSessionId, newToken) = try await httpClient.createSession(serverURL: baseURL)
                self.sessionId = newSessionId
                let wsURL = buildWebSocketURL()
                transport.connect(token: newToken, serverURL: wsURL)
            } catch {
                state = .stopped
                errorMessage = "Session could not be restored: \(error.localizedDescription)"
                delegate?.sessionDidReceiveError(errorMessage!)
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
        Log.info("DIAG: audioCaptureDidDetectBargein fired", tag: "ConversationSession")
        // Trigger 1: Local RMS detection during playback.
        handleBargein()
    }

    func audioCaptureDidProduceChunk(_ data: Data) {
        guard state == .active else { return }

        // When muted, still compute amplitude for UI but don't send audio.
        guard !isMuted else {
            delegate?.sessionDidUpdateMicAmplitude(0.0)
            return
        }

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
        if frameSeq == 1 || frameSeq % 50 == 0 {
            Log.info("DIAG: Audio chunk #\(frameSeq): \(data.count) bytes, rms=\(rms) state=\(state) isPlaying=\(audioCaptureEngine.isPlaying) gen=\(currentGen)", tag: "ConversationSession")
        }
        delegate?.sessionDidUpdateMicAmplitude(rms)
    }
}
