import Foundation
import SwiftUI

/// SwiftUI binding layer for the conversation UI.
///
/// Exposes @Published state driven by ConversationSession events.
/// Handles partial transcript slot management (Fix 11) and agent status mapping (Fix 6).
@MainActor
final class ConversationViewModel: ObservableObject {

    // MARK: - Published State

    @Published var sessionState: SessionState = .idle
    @Published var transcript: [TranscriptLine] = []
    @Published var agentStatuses: [String: AgentStatusKind] = [:]
    @Published var agentElapsedMs: [String: Int] = [:]
    @Published var meetingProgress: MeetingStatusEvent? = nil
    @Published var micAmplitude: Float = 0.0
    @Published var errorMessage: String? = nil
    @Published var currentlyPlayingSpeaker: UInt8? = nil

    // MARK: - Session

    let session: ConversationSession

    /// Fix 11: Mutable in-progress slot index for partial transcripts.
    /// Without this, each partial event would append a new line, creating duplicates.
    private var inProgressTranscriptIndex: Int? = nil

    // MARK: - Init

    init(session: ConversationSession = ConversationSession()) {
        self.session = session
        self.session.delegate = self
    }

    // MARK: - User Actions

    /// Called when the user taps the Start button.
    func tapStart() {
        guard sessionState == .idle else { return }
        sessionState = .connecting

        Task {
            do {
                try await session.start()
                sessionState = .active
                errorMessage = nil
            } catch {
                sessionState = .idle
                errorMessage = error.localizedDescription
            }
        }
    }

    /// Called when the user taps the Stop button.
    func tapStop() {
        sessionState = .stopped
        Task {
            await session.stop()
        }
    }

    /// Called when the user taps Skip on an agent.
    func sendSkipSpeaker() {
        session.sendSkipSpeaker()
    }

    // MARK: - Transcript Handling (Fix 11)

    /// Handle a transcript event from the server.
    ///
    /// Fix 11: Partial transcripts update an in-progress slot rather than appending new lines.
    func handleTranscriptEvent(_ json: [String: Any]) {
        let text = json["text"] as? String ?? ""
        let speaker = json["speaker"] as? String ?? "user"
        let isFinal = json["is_final"] as? Bool ?? false

        if isFinal {
            if let idx = inProgressTranscriptIndex, idx < transcript.count {
                // Replace the in-progress slot with the final text.
                transcript[idx] = TranscriptLine(speaker: speaker, text: text, isFinal: true)
            } else {
                transcript.append(TranscriptLine(speaker: speaker, text: text, isFinal: true))
            }
            inProgressTranscriptIndex = nil
        } else {
            if let idx = inProgressTranscriptIndex, idx < transcript.count {
                // Update the existing in-progress slot.
                transcript[idx] = TranscriptLine(speaker: speaker, text: text, isFinal: false)
            } else {
                // Create a new in-progress slot.
                inProgressTranscriptIndex = transcript.count
                transcript.append(TranscriptLine(speaker: speaker, text: text, isFinal: false))
            }
        }
    }

    // MARK: - Agent Status Handling (Fix 6)

    /// Handle an agent status event.
    ///
    /// Fix 6: dispatched status shows spinner same as thinking.
    func handleAgentStatusEvent(_ event: AgentStatusEvent) {
        agentStatuses[event.agentName] = event.status
        if let elapsed = event.elapsedMs {
            agentElapsedMs[event.agentName] = elapsed
        }
    }

    // MARK: - Meeting Status

    /// Handle a meeting status event.
    func handleMeetingStatusEvent(_ event: MeetingStatusEvent) {
        meetingProgress = event
    }

    // MARK: - Reset

    /// Reset all state for a new session.
    func reset() {
        sessionState = .idle
        transcript.removeAll()
        agentStatuses.removeAll()
        agentElapsedMs.removeAll()
        meetingProgress = nil
        micAmplitude = 0.0
        errorMessage = nil
        currentlyPlayingSpeaker = nil
        inProgressTranscriptIndex = nil
    }
}

// MARK: - ConversationSessionDelegate

extension ConversationViewModel: ConversationSessionDelegate {

    nonisolated func sessionDidChangeState(_ state: SessionState) {
        Task { @MainActor in
            self.sessionState = state
        }
    }

    nonisolated func sessionDidReceiveTranscript(_ json: [String: Any]) {
        Task { @MainActor in
            self.handleTranscriptEvent(json)
        }
    }

    nonisolated func sessionDidReceiveAgentStatus(_ event: AgentStatusEvent) {
        Task { @MainActor in
            self.handleAgentStatusEvent(event)
        }
    }

    nonisolated func sessionDidReceiveMeetingStatus(_ event: MeetingStatusEvent) {
        Task { @MainActor in
            self.handleMeetingStatusEvent(event)
        }
    }

    nonisolated func sessionDidReceiveError(_ message: String) {
        Task { @MainActor in
            self.errorMessage = message
        }
    }

    nonisolated func sessionDidUpdateMicAmplitude(_ amplitude: Float) {
        Task { @MainActor in
            self.micAmplitude = amplitude
        }
    }

    nonisolated func sessionDidUpdatePlayingSpeaker(_ speakerId: UInt8?) {
        Task { @MainActor in
            self.currentlyPlayingSpeaker = speakerId
        }
    }
}
