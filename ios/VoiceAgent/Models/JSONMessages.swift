import Foundation

// MARK: - Client -> Server Messages

/// `control` message: session lifecycle commands.
struct ControlMessage: Codable {
    let type: String
    let action: String

    init(action: String) {
        self.type = "control"
        self.action = action
    }

    static func start() -> ControlMessage { ControlMessage(action: "start") }
    static func stop() -> ControlMessage { ControlMessage(action: "stop") }
    static func pause() -> ControlMessage { ControlMessage(action: "pause") }
}

/// `interrupt` message: barge-in signal to server.
struct InterruptMessage: Codable {
    let type: String
    let mode: String

    init(mode: String = "cancel_all") {
        self.type = "interrupt"
        self.mode = mode
    }

    static func cancelAll() -> InterruptMessage { InterruptMessage(mode: "cancel_all") }
    static func skipSpeaker() -> InterruptMessage { InterruptMessage(mode: "skip_speaker") }
}

/// `agent_followup` message: route follow-up to an existing agent session.
struct AgentFollowupMessage: Codable {
    let type: String
    let agentSessionId: String
    let text: String

    enum CodingKeys: String, CodingKey {
        case type
        case agentSessionId = "agent_session_id"
        case text
    }

    init(agentSessionId: String, text: String) {
        self.type = "agent_followup"
        self.agentSessionId = agentSessionId
        self.text = text
    }
}

// MARK: - Server -> Client Messages

/// `transcript` message: recognized speech text.
struct TranscriptMessage: Codable {
    let type: String
    let speaker: String
    let text: String
    let isFinal: Bool

    enum CodingKeys: String, CodingKey {
        case type, speaker, text
        case isFinal = "is_final"
    }
}

/// `agent_status` message: agent lifecycle events.
struct AgentStatusMessage: Codable {
    let type: String
    let agentName: String
    let agentSessionId: String
    let status: String
    let elapsedMs: Int?
    let genId: Int?

    enum CodingKeys: String, CodingKey {
        case type
        case agentName = "agent_name"
        case agentSessionId = "agent_session_id"
        case status
        case elapsedMs = "elapsed_ms"
        case genId = "gen_id"
    }
}

/// `meeting_status` message: meeting progress tracking.
struct MeetingStatusMessage: Codable {
    let type: String
    let genId: Int
    let totalAgents: Int
    let completed: Int
    let pending: [String]
    let failed: [String]

    enum CodingKeys: String, CodingKey {
        case type
        case genId = "gen_id"
        case totalAgents = "total_agents"
        case completed, pending, failed
    }
}

/// `interruption` message: server-side barge-in confirmation.
struct InterruptionMessage: Codable {
    let type: String
    let genId: Int

    enum CodingKeys: String, CodingKey {
        case type
        case genId = "gen_id"
    }
}

/// `error` message: server error notification.
struct ErrorMessage: Codable {
    let type: String
    let code: String
    let message: String
}

// MARK: - REST API Response Models

/// Response from POST /api/v1/sessions
struct CreateSessionResponse: Codable {
    let sessionId: String
    let authToken: String
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case authToken = "auth_token"
        case expiresAt = "expires_at"
    }
}

/// Response from DELETE /api/v1/sessions/{session_id}
struct DeleteSessionResponse: Codable {
    let status: String
}

/// Response from GET /api/v1/health
struct HealthResponse: Codable {
    let status: String
    let version: String
    let activeSessions: Int

    enum CodingKeys: String, CodingKey {
        case status, version
        case activeSessions = "active_sessions"
    }
}

/// Agent info from GET /api/v1/agents
struct AgentInfo: Codable {
    let name: String
    let description: String
    let voiceId: String
    let speakerId: Int
    let toolSet: [String]

    enum CodingKeys: String, CodingKey {
        case name, description
        case voiceId = "voice_id"
        case speakerId = "speaker_id"
        case toolSet = "tool_set"
    }
}

/// Response from GET /api/v1/agents
struct AgentsResponse: Codable {
    let agents: [AgentInfo]
}

// MARK: - Generic JSON Type Discriminator

/// Minimal envelope to determine the `type` field of any server JSON message.
struct MessageEnvelope: Codable {
    let type: String
}
