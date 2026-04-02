import AVFoundation
import Foundation

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

    // MARK: - Echo Diagnostics
    /// Track chunks sent to backend during playback vs silence.
    private var diagChunksSentDuringPlayback: Int = 0
    private var diagChunksSentDuringSilence: Int = 0
    /// DIAG: track gate transitions and playback frame counts
    private var diagLastIsPlayingState: Bool = false
    private var diagBinaryFramesReceived: Int = 0
    private var diagSpeakerFinishedCount: Int = 0
    /// DIAG: timestamp when gate last closed (isPlaying became true)
    private var diagGateClosedTime: CFAbsoluteTime = 0
    /// DIAG: timestamp of last binary frame received
    private var diagLastBinaryFrameTime: CFAbsoluteTime = 0
    /// DIAG: consecutive chunks dropped while gate stuck
    private var diagConsecutiveDrops: Int = 0
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

            // When a speaker finishes playing, update isPlaying so the audio
            // gate re-opens and mic audio flows to Gemini again.
            playbackEngine.onSpeakerFinished = { [weak self] speakerId in
                guard let self = self else { return }
                let wasPlaying = self.audioCaptureEngine.isPlaying
                let isNowPlaying = self.playbackEngine.isAnyPlaying
                self.audioCaptureEngine.isPlaying = isNowPlaying
                self.diagSpeakerFinishedCount += 1
                let threadName = Thread.current.isMainThread ? "main" : (Thread.current.name ?? "bg-\(Thread.current)")
                if wasPlaying != isNowPlaying {
                    let gateDuration = self.diagGateClosedTime > 0 ? CFAbsoluteTimeGetCurrent() - self.diagGateClosedTime : 0
                    Log.info("DIAG-GATE: onSpeakerFinished speaker=\(speakerId) isPlaying \(wasPlaying)->\(isNowPlaying) callbacks=\(self.diagSpeakerFinishedCount) skipped=\(self.diagChunksSentDuringPlayback) sent=\(self.diagChunksSentDuringSilence) gateDuration=\(String(format: "%.2f", gateDuration))s thread=\(threadName)", tag: "ConversationSession")
                    if !isNowPlaying {
                        self.diagConsecutiveDrops = 0
                    }
                } else if wasPlaying && isNowPlaying {
                    // Gate still closed after callback — log periodically to track stuck state
                    if self.diagSpeakerFinishedCount % 10 == 0 {
                        Log.info("DIAG-GATE: onSpeakerFinished speaker=\(speakerId) STILL PLAYING callbacks=\(self.diagSpeakerFinishedCount) thread=\(threadName)", tag: "ConversationSession")
                    }
                }
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

        // Stop audio capture (removes input tap).
        audioCaptureEngine.stopCapture()

        // Send stop control message.
        transport.sendJSON(["type": "control", "action": "stop"])

        // Close WebSocket.
        transport.disconnect()

        // Stop playback (clears queues and player nodes).
        playbackEngine.flushAllAndStop(newGen: currentGen)
        playbackEngine.stop()

        // Stop the shared audio engine last.
        sharedAudioEngine.stop()
        Log.info("Shared AVAudioEngine stopped", tag: "ConversationSession")

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
        audioCaptureEngine.isPlaying = false
        transport.sendJSON(["type": "interrupt", "mode": "cancel_all"])
        delegate?.sessionDidReceiveBargeIn(currentGenId: Int(currentGen))
        Log.info("DIAG: handleBargein complete, isPlaying reset to false", tag: "ConversationSession")
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
        audioCaptureEngine.isPlaying = false
        delegate?.sessionDidReceiveBargeIn(currentGenId: Int(currentGen))
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
        guard let header = AudioFrameHeader(data: data) else {
            Log.warning("DIAG: transportDidReceiveBinaryFrame invalid header, dataSize=\(data.count)", tag: "ConversationSession")
            return
        }
        let pcm = Data(data.dropFirst(AudioFrameHeader.size))
        diagBinaryFramesReceived += 1
        diagLastBinaryFrameTime = CFAbsoluteTimeGetCurrent()
        let wasPlaying = audioCaptureEngine.isPlaying
        playbackEngine.receiveAudioFrame(header: header, pcm: pcm)

        // Update playing state for the capture engine's barge-in detection.
        let isNowPlaying = playbackEngine.isAnyPlaying
        audioCaptureEngine.isPlaying = isNowPlaying
        if wasPlaying != isNowPlaying {
            Log.info("DIAG-GATE: receiveBinaryFrame isPlaying \(wasPlaying)->\(isNowPlaying) binaryFrames=\(diagBinaryFramesReceived) speaker=\(header.speakerId) pcmBytes=\(pcm.count)", tag: "ConversationSession")
        }
        if diagBinaryFramesReceived == 1 || diagBinaryFramesReceived % 50 == 0 {
            Log.info("DIAG-GATE: receiveBinaryFrame #\(diagBinaryFramesReceived) speaker=\(header.speakerId) isPlaying=\(isNowPlaying) pcmBytes=\(pcm.count)", tag: "ConversationSession")
        }
        let newSpeaker = playbackEngine.currentlyPlayingSpeaker
        if newSpeaker != lastReportedPlayingSpeaker {
            lastReportedPlayingSpeaker = newSpeaker
            delegate?.sessionDidUpdatePlayingSpeaker(newSpeaker)
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
        Log.info("DIAG-ECHO: LOCAL_BARGEIN fired gen=\(currentGen)", tag: "ConversationSession")
        handleBargein()
    }

    func audioCaptureDidProduceChunk(_ data: Data) {
        guard state == .active else { return }

        // When muted, still compute amplitude for UI but don't send audio.
        guard !isMuted else {
            delegate?.sessionDidUpdateMicAmplitude(0.0)
            return
        }

        let isCurrentlyPlaying = audioCaptureEngine.isPlaying

        // DIAG: log gate state transitions
        if isCurrentlyPlaying != diagLastIsPlayingState {
            let now = CFAbsoluteTimeGetCurrent()
            if isCurrentlyPlaying {
                diagGateClosedTime = now
                diagConsecutiveDrops = 0
            }
            let gateDuration = (!isCurrentlyPlaying && diagGateClosedTime > 0) ? now - diagGateClosedTime : 0
            Log.info("DIAG-GATE: audio gate \(isCurrentlyPlaying ? "CLOSED (dropping chunks)" : "OPEN (sending chunks)") skipped=\(diagChunksSentDuringPlayback) sent=\(diagChunksSentDuringSilence) isAnyPlaying=\(playbackEngine.isAnyPlaying) speakerFinishedCallbacks=\(diagSpeakerFinishedCount) gateDuration=\(String(format: "%.2f", gateDuration))s", tag: "ConversationSession")
            diagLastIsPlayingState = isCurrentlyPlaying
        }

        // Gate: Do NOT send audio to Gemini while TTS is playing.
        // Even with hardware AEC, residual echo (20-40% of chunks at 15-20 dB)
        // reaches Gemini's server-side VAD which transcribes it as user speech.
        // Barge-in still works: local RMS detector fires → handleBargein() →
        // flushes playback → isPlaying becomes false → audio flows again.
        if isCurrentlyPlaying {
            diagChunksSentDuringPlayback += 1
            diagConsecutiveDrops += 1
            if diagChunksSentDuringPlayback == 1 || diagChunksSentDuringPlayback % 100 == 0 {
                Log.info("DIAG-GATE: chunk DROPPED #\(diagChunksSentDuringPlayback) (isPlaying=true, isAnyPlaying=\(playbackEngine.isAnyPlaying))", tag: "ConversationSession")
            }
            // DIAG: Stuck gate detector — if gate closed for >3s with no new audio frames, dump state
            let now = CFAbsoluteTimeGetCurrent()
            let gateAge = diagGateClosedTime > 0 ? now - diagGateClosedTime : 0
            let silenceSinceLastFrame = diagLastBinaryFrameTime > 0 ? now - diagLastBinaryFrameTime : 0
            if diagConsecutiveDrops == 30 || (diagConsecutiveDrops > 0 && diagConsecutiveDrops % 50 == 0) {
                Log.warning("DIAG-STUCK: gate closed for \(String(format: "%.1f", gateAge))s, \(diagConsecutiveDrops) chunks dropped, no audio frame for \(String(format: "%.1f", silenceSinceLastFrame))s, isAnyPlaying=\(playbackEngine.isAnyPlaying) currentSpeaker=\(playbackEngine.currentlyPlayingSpeaker.map(String.init) ?? "nil") speakerFinishedCallbacks=\(diagSpeakerFinishedCount)", tag: "ConversationSession")
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
