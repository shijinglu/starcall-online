import SwiftUI

/// Circular agent avatar with colored ring border, initials, and status dot.
///
/// Used in the agent strip when agents are active. Matches the StarCl design:
/// - Dark circle background with colored ring border
/// - Two-letter initials in the agent's color
/// - Status dot: amber (thinking/dispatched), teal (done/speaking)
/// - Pulsing animation when thinking, scale animation when speaking
struct AgentAvatarView: View {
    let definition: AgentDefinition
    let status: AgentStatusKind
    let isSpeaking: Bool
    let commText: String?

    private let avatarSize: CGFloat = 52

    var body: some View {
        VStack(spacing: 7) {
            ZStack(alignment: .bottomTrailing) {
                // Ring + initials
                Text(definition.initials)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(definition.ringColor)
                    .frame(width: avatarSize, height: avatarSize)
                    .background(Color(hex: 0x111118))
                    .clipShape(Circle())
                    .overlay(
                        Circle()
                            .stroke(definition.ringColor, lineWidth: 2)
                    )
                    .opacity(status.showsSpinner ? thinkingOpacity : 1.0)
                    .scaleEffect(isSpeaking ? speakingScale : 1.0)
                    .animation(
                        status.showsSpinner
                            ? .easeInOut(duration: 1.8).repeatForever(autoreverses: true)
                            : .easeInOut(duration: 0.9).repeatForever(autoreverses: true),
                        value: status.showsSpinner || isSpeaking
                    )

                // Status dot
                statusDot
                    .offset(x: 1, y: 1)
            }

            Text(definition.name)
                .font(.system(size: 11))
                .foregroundColor(StarClTheme.agentLabel)

            // Agent comm text (intermediate reasoning, no TTS)
            if let commText = commText, !commText.isEmpty {
                Text(commText)
                    .font(.system(size: 10).italic())
                    .foregroundColor(StarClTheme.mutedText)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 100)
                    .transition(.opacity)
                    .animation(.easeInOut(duration: 0.3), value: commText)
            }
        }
    }

    @ViewBuilder
    private var statusDot: some View {
        Circle()
            .fill(statusDotColor)
            .frame(width: 10, height: 10)
            .overlay(
                Circle()
                    .stroke(StarClTheme.background, lineWidth: 2)
            )
            .opacity(isSpeaking ? speakingDotOpacity : 1.0)
            .animation(
                isSpeaking
                    ? .easeInOut(duration: 0.8).repeatForever(autoreverses: true)
                    : .default,
                value: isSpeaking
            )
    }

    private var statusDotColor: Color {
        switch status {
        case .dispatched, .thinking:
            return StarClTheme.amber
        case .done:
            return StarClTheme.teal
        case .timeout:
            return StarClTheme.amber
        case .cancelled:
            return StarClTheme.mutedText
        }
    }

    // Animation state values — SwiftUI animates between these
    @State private var thinkingOpacity: Double = 0.5
    @State private var speakingScale: CGFloat = 1.08
    @State private var speakingDotOpacity: Double = 0.2
}

// MARK: - Agent Strip

/// Horizontal strip of active agent avatars, shown below the header divider.
struct AgentStripView: View {
    let agents: [(definition: AgentDefinition, status: AgentStatusKind)]
    let currentlyPlayingSpeaker: UInt8?
    let commTexts: [String: String]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("ACTIVE AGENTS")
                .font(.system(size: 10))
                .tracking(1.2)
                .foregroundColor(StarClTheme.labelText)

            HStack(spacing: 20) {
                ForEach(agents, id: \.definition.key) { agent in
                    let speakerId = speakerIdForAgent(agent.definition.key)
                    let isSpeaking = currentlyPlayingSpeaker == speakerId

                    AgentAvatarView(
                        definition: agent.definition,
                        status: agent.status,
                        isSpeaking: isSpeaking,
                        commText: commTexts[agent.definition.key]
                    )
                }
            }
        }
        .padding(.horizontal, 26)
        .padding(.top, 14)
    }

    private func speakerIdForAgent(_ name: String) -> UInt8 {
        switch name {
        case "ellen":   return SpeakerId.ellen.rawValue
        case "shijing": return SpeakerId.shijing.rawValue
        case "eva":     return SpeakerId.eva.rawValue
        case "ming":    return SpeakerId.ming.rawValue
        default:        return 0xFF
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        StarClTheme.background.ignoresSafeArea()

        AgentStripView(
            agents: [
                (AgentDefinition.all[0], .thinking),
                (AgentDefinition.all[1], .done),
                (AgentDefinition.all[2], .thinking),
            ],
            currentlyPlayingSpeaker: nil,
            commTexts: ["ellen": "Checking calendar for conflicts..."]
        )
    }
}
