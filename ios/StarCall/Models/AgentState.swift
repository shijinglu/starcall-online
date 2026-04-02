import Foundation

/// Conversation mode: talk (full voice) or listen (silent meeting assistant).
enum SessionMode: String {
    case talk
    case listen
}

/// Session lifecycle states.
enum SessionState: String {
    case idle
    case connecting   // POST /sessions in flight
    case active       // WS open, audio streaming
    case stopped      // user tapped stop or terminal error
}

/// Agent lifecycle status kinds, matching the server's `agent_status` JSON `status` field.
enum AgentStatusKind: String, Codable {
    case dispatched
    case thinking
    case done
    case timeout
    case cancelled

    /// Human-readable label for UI display.
    var label: String {
        switch self {
        case .dispatched: return "dispatched"
        case .thinking:   return "thinking"
        case .done:       return "done"
        case .timeout:    return "timed out"
        case .cancelled:  return "cancelled"
        }
    }

    /// Whether this status should display a spinner in the UI.
    /// Fix 6: dispatched shows spinner same as thinking (no grey dot gap).
    var showsSpinner: Bool {
        switch self {
        case .dispatched, .thinking: return true
        default: return false
        }
    }
}

/// Parsed agent_status server event.
struct AgentStatusEvent {
    let agentName: String
    let agentSessionId: String
    let status: AgentStatusKind
    let elapsedMs: Int?
    let genId: Int
}

/// Parsed meeting_status server event.
struct MeetingStatusEvent {
    let genId: Int
    let totalAgents: Int
    let completed: Int
    let pending: [String]
    let failed: [String]
}

/// Parsed agent_comm server event — intermediate agent reasoning text (no TTS).
struct AgentCommEvent {
    let fromAgent: String
    let toAgent: String?
    let text: String
    let genId: Int
}

/// A single line in the conversation transcript.
struct TranscriptLine: Identifiable {
    let id = UUID()
    let speaker: String
    var text: String
    var isFinal: Bool
}
