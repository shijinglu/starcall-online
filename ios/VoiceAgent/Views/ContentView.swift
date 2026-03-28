import SwiftUI

/// Main conversation UI.
///
/// Layout:
/// - Transcript scroll view (top)
/// - Agent status row (middle)
/// - Meeting progress bar (when active)
/// - Mic waveform + start/stop button (bottom)
public struct ContentView: View {
    @StateObject private var viewModel = ConversationViewModel()

    public init() {}

    public var body: some View {
        VStack(spacing: 0) {
            // MARK: - Transcript
            transcriptSection

            Divider()

            // MARK: - Agent Status
            agentStatusSection

            // MARK: - Meeting Progress
            if let progress = viewModel.meetingProgress {
                MeetingProgressView(
                    completed: progress.completed,
                    total: progress.totalAgents,
                    pending: progress.pending
                )
                .padding(.horizontal)
                .padding(.vertical, 8)
            }

            Divider()

            // MARK: - Mic Waveform & Controls
            controlSection
        }
        .alert("Error", isPresented: .constant(viewModel.errorMessage != nil)) {
            Button("OK") {
                viewModel.errorMessage = nil
            }
        } message: {
            Text(viewModel.errorMessage ?? "")
        }
    }

    // MARK: - Transcript Section

    private var transcriptSection: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(viewModel.transcript) { line in
                        TranscriptLineView(line: line)
                            .id(line.id)
                    }
                }
                .padding()
            }
            .onChange(of: viewModel.transcript.count) { _, _ in
                // Auto-scroll to the latest transcript line.
                if let lastId = viewModel.transcript.last?.id {
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(lastId, anchor: .bottom)
                    }
                }
            }
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: - Agent Status Section

    private var agentStatusSection: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                ForEach(agentNames, id: \.self) { name in
                    let status = viewModel.agentStatuses[name] ?? .cancelled
                    let speakerId = speakerIdForAgent(name)
                    let isSpeaking = viewModel.currentlyPlayingSpeaker == speakerId

                    AgentStatusCard(
                        agentName: name,
                        status: status,
                        elapsedMs: viewModel.agentElapsedMs[name],
                        isCurrentlySpeaking: isSpeaking,
                        onSkip: {
                            viewModel.sendSkipSpeaker()
                        }
                    )
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 8)
        }
        .opacity(viewModel.agentStatuses.isEmpty ? 0 : 1)
    }

    // MARK: - Control Section

    private var controlSection: some View {
        VStack(spacing: 16) {
            // Mic waveform visualization.
            MicWaveformView(amplitude: viewModel.micAmplitude)
                .frame(height: 40)
                .padding(.horizontal)

            // Start / Stop button.
            Button(action: {
                switch viewModel.sessionState {
                case .idle, .stopped:
                    viewModel.reset()
                    viewModel.tapStart()
                case .active, .connecting:
                    viewModel.tapStop()
                }
            }) {
                Text(buttonLabel)
                    .font(.headline)
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(buttonColor)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(viewModel.sessionState == .connecting)
            .padding(.horizontal)
            .padding(.bottom, 16)
        }
    }

    // MARK: - Helpers

    private var buttonLabel: String {
        switch viewModel.sessionState {
        case .idle, .stopped: return "Start Conversation"
        case .connecting:     return "Connecting..."
        case .active:         return "Stop"
        }
    }

    private var buttonColor: Color {
        switch viewModel.sessionState {
        case .idle, .stopped: return .blue
        case .connecting:     return .gray
        case .active:         return .red
        }
    }

    /// Known agent names in display order.
    private var agentNames: [String] {
        ["ellen", "shijing", "eva", "ming"]
    }

    /// Map agent name to speaker_id.
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

// MARK: - Transcript Line View

struct TranscriptLineView: View {
    let line: TranscriptLine

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(line.speaker.capitalized + ":")
                .font(.subheadline)
                .fontWeight(.semibold)
                .foregroundColor(speakerColor)
                .frame(width: 80, alignment: .trailing)

            Text(line.text)
                .font(.body)
                .foregroundColor(line.isFinal ? .primary : .secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var speakerColor: Color {
        switch line.speaker.lowercased() {
        case "user":      return .blue
        case "moderator": return .purple
        default:          return .orange
        }
    }
}

// MARK: - Mic Waveform View

struct MicWaveformView: View {
    let amplitude: Float

    /// Number of bars in the waveform visualization.
    private let barCount = 20

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<barCount, id: \.self) { index in
                let normalizedIndex = Float(index) / Float(barCount)
                let barHeight = max(0.05, amplitude * waveShape(at: normalizedIndex))

                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.blue.opacity(0.6))
                    .frame(width: 4, height: CGFloat(barHeight) * 40)
                    .animation(.easeInOut(duration: 0.1), value: amplitude)
            }
        }
    }

    /// Generate a wave shape factor for the given normalized position.
    private func waveShape(at position: Float) -> Float {
        let center = Float(0.5)
        let distance = abs(position - center)
        return max(0.2, 1.0 - distance * 2)
    }
}

// MARK: - Preview

#Preview {
    ContentView()
}
