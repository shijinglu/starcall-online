import SwiftUI

/// Displays per-agent status with animated spinner, elapsed time, and skip button.
///
/// Fix 6: `dispatched` shows spinner same as `thinking` (no grey dot gap).
/// Fix 10: Skip button appears when the agent is actively playing audio.
struct AgentStatusCard: View {
    let agentName: String
    let status: AgentStatusKind
    let elapsedMs: Int?
    let isCurrentlySpeaking: Bool
    var onSkip: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                statusIcon
                    .frame(width: 20, height: 20)

                Text(agentName.capitalized)
                    .font(.subheadline)
                    .fontWeight(.medium)
            }

            Text(statusLabel)
                .font(.caption)
                .foregroundColor(.secondary)

            if isCurrentlySpeaking {
                Button("Skip") {
                    onSkip()
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .tint(.orange)
            }
        }
        .padding(10)
        .background(cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(borderColor, lineWidth: 1)
        )
    }

    // MARK: - Status Icon

    @ViewBuilder
    private var statusIcon: some View {
        switch status {
        case .dispatched, .thinking:
            // Fix 6: Both dispatched and thinking show the same spinner.
            ProgressView()
                .scaleEffect(0.8)
                .tint(.orange)
        case .done:
            Image(systemName: "checkmark.circle.fill")
                .foregroundColor(.green)
        case .timeout:
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.orange)
        case .cancelled:
            Image(systemName: "xmark.circle.fill")
                .foregroundColor(.gray)
        }
    }

    // MARK: - Labels

    private var statusLabel: String {
        switch status {
        case .dispatched:
            return "dispatched"
        case .thinking:
            if let elapsed = elapsedMs {
                let seconds = elapsed / 1000
                return "thinking (\(seconds)s)"
            }
            return "thinking"
        case .done:
            return "done"
        case .timeout:
            return "timed out"
        case .cancelled:
            return "cancelled"
        }
    }

    // MARK: - Styling

    private var cardBackground: Color {
        switch status {
        case .dispatched, .thinking:
            return Color.orange.opacity(0.08)
        case .done:
            return Color.green.opacity(0.08)
        case .timeout:
            return Color.orange.opacity(0.08)
        case .cancelled:
            return Color.gray.opacity(0.08)
        }
    }

    private var borderColor: Color {
        if isCurrentlySpeaking {
            return .blue
        }
        switch status {
        case .dispatched, .thinking: return .orange.opacity(0.3)
        case .done:                  return .green.opacity(0.3)
        case .timeout:               return .orange.opacity(0.3)
        case .cancelled:             return .gray.opacity(0.3)
        }
    }
}

// MARK: - Preview

#Preview {
    HStack {
        AgentStatusCard(
            agentName: "ellen",
            status: .thinking,
            elapsedMs: 12000,
            isCurrentlySpeaking: false,
            onSkip: {}
        )
        AgentStatusCard(
            agentName: "eva",
            status: .done,
            elapsedMs: nil,
            isCurrentlySpeaking: true,
            onSkip: {}
        )
        AgentStatusCard(
            agentName: "ming",
            status: .dispatched,
            elapsedMs: nil,
            isCurrentlySpeaking: false,
            onSkip: {}
        )
    }
    .padding()
}
