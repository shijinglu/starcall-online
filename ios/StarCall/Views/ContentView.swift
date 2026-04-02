import SwiftUI

/// Main StarCl conversation UI.
///
/// Layout (top to bottom):
/// - Header: App name + session tag
/// - Divider
/// - Agent strip (hidden until agents dispatched)
/// - Conversation feed (idle greeting or message list)
/// - Bottom bar: mute, mic, history
public struct ContentView: View {
    @StateObject private var viewModel = ConversationViewModel()

    public init() {}

    public var body: some View {
        VStack(spacing: 0) {
            headerView
            dividerLine
            agentStrip
            conversationFeed
            Spacer(minLength: 0)
            bottomBar
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(StarClTheme.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .accessibilityIdentifier("mainContent")
        .alert("Error", isPresented: .constant(viewModel.errorMessage != nil)) {
            Button("OK") { viewModel.errorMessage = nil }
        } message: {
            Text(viewModel.errorMessage ?? "")
        }
    }

    // MARK: - Header

    private var headerView: some View {
        HStack {
            // Star*Cl* with teal accent
            HStack(spacing: 0) {
                Text("Star")
                    .foregroundColor(StarClTheme.primaryText)
                Text("Cl")
                    .foregroundColor(StarClTheme.teal)
            }
            .font(.system(size: 23, weight: .bold))
            .tracking(-1)

            Spacer()

            Text(sessionTagText)
                .font(.system(size: 11))
                .tracking(0.8)
                .foregroundColor(StarClTheme.labelText)
                .textCase(.uppercase)
        }
        .padding(.horizontal, 26)
        .padding(.top, 4)
    }

    private var sessionTagText: String {
        switch viewModel.sessionState {
        case .idle, .stopped:
            return "NO SESSION"
        case .connecting:
            return "CONNECTING"
        case .active:
            let agentCount = viewModel.agentStatuses.count
            if agentCount > 0 {
                return "LIVE · \(agentCount) AGENTS"
            }
            return "LIVE"
        }
    }

    // MARK: - Divider

    private var dividerLine: some View {
        Rectangle()
            .fill(StarClTheme.divider)
            .frame(height: 1)
            .padding(.horizontal, 26)
            .padding(.top, 10)
    }

    // MARK: - Agent Strip

    @ViewBuilder
    private var agentStrip: some View {
        if !viewModel.agentStatuses.isEmpty {
            let activeAgents: [(definition: AgentDefinition, status: AgentStatusKind)] =
                agentNames.compactMap { name in
                    guard let status = viewModel.agentStatuses[name],
                          let def = AgentDefinition.definition(for: name) else { return nil }
                    return (def, status)
                }

            if !activeAgents.isEmpty {
                AgentStripView(
                    agents: activeAgents,
                    currentlyPlayingSpeaker: viewModel.currentlyPlayingSpeaker,
                    commTexts: viewModel.agentCommTexts.mapValues { $0.text }
                )
                .transition(.opacity.combined(with: .move(edge: .top)))
                .animation(.easeInOut(duration: 0.3), value: viewModel.agentStatuses.count)
            }
        }
    }

    // MARK: - Conversation Feed

    private var conversationFeed: some View {
        Group {
            if viewModel.sessionState == .idle && viewModel.transcript.isEmpty {
                idleContentView
            } else {
                messageListView
            }
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: - Idle Content

    private var idleContentView: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(greetingText)
                .font(.system(size: 19, weight: .medium))
                .foregroundColor(StarClTheme.primaryText)
                .tracking(-0.5)
                .padding(.bottom, 3)

            Text("Tap the mic to start a session")
                .font(.system(size: 13))
                .foregroundColor(StarClTheme.labelText)
                .padding(.bottom, 20)

            Text("RECENT SESSIONS")
                .font(.system(size: 10))
                .tracking(1.2)
                .foregroundColor(StarClTheme.labelText)
                .padding(.bottom, 8)

            // Placeholder recent sessions (no backend support yet)
            recentSessionItem(
                title: "ACH return investigation",
                time: "2h ago",
                preview: "Ming: synthetic ID cluster in EU region identified..."
            )
            recentSessionItem(
                title: "Weekly metrics brief",
                time: "Yesterday",
                preview: "Ellen: processing volume $4.2M in the last hour..."
            )
            recentSessionItem(
                title: "London time check",
                time: "Mon",
                preview: "It is 3:15 PM in London, 7:15 AM tomorrow in Tokyo."
            )

            Spacer()
        }
        .padding(.horizontal, 26)
        .padding(.top, 16)
    }

    private var greetingText: String {
        let hour = Calendar.current.component(.hour, from: Date())
        if hour < 12 { return "Good morning." }
        if hour < 17 { return "Good afternoon." }
        return "Good evening."
    }

    private func recentSessionItem(title: String, time: String, preview: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(Color(hex: 0xB8B8CC))
                Spacer()
                Text(time)
                    .font(.system(size: 11))
                    .foregroundColor(StarClTheme.subtitleText)
            }
            Text(preview)
                .font(.system(size: 11))
                .foregroundColor(StarClTheme.labelText)
                .lineLimit(1)
                .truncationMode(.tail)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(StarClTheme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(StarClTheme.cardBorder, lineWidth: 1)
        )
        .padding(.bottom, 4)
    }

    // MARK: - Message List

    private var messageListView: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(viewModel.transcript.enumerated()), id: \.element.id) { index, line in
                        let isMuted = isOlderMessage(index: index)
                        MessageRowView(line: line, isMuted: isMuted)
                            .id(line.id)
                    }
                }
                .padding(.horizontal, 26)
                .padding(.top, 14)
            }
            .onChange(of: viewModel.transcript.count) { _, _ in
                if let lastId = viewModel.transcript.last?.id {
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(lastId, anchor: .bottom)
                    }
                }
            }
        }
    }

    /// Older messages (not in the last 3) get dimmed.
    private func isOlderMessage(index: Int) -> Bool {
        let total = viewModel.transcript.count
        return total > 3 && index < total - 3
    }

    // MARK: - Bottom Bar

    private var bottomBar: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(StarClTheme.bottomBorder)
                .frame(height: 1)

            HStack(alignment: .center) {
                // Mute button
                muteButton

                Spacer()

                // Mic button (start session / toggle mute)
                micButton

                Spacer()

                // New conversation button (ends current session)
                newConversationButton
            }
            .padding(.horizontal, 26)
            .padding(.top, 14)
            .padding(.bottom, 8)
        }
    }

    // MARK: - Mute Button

    private var muteButton: some View {
        Button(action: { viewModel.toggleMute() }) {
            ZStack {
                Circle()
                    .fill(StarClTheme.sideButtonBg)
                    .frame(width: 44, height: 44)
                    .overlay(Circle().stroke(StarClTheme.sideButtonBorder, lineWidth: 1))

                Image(systemName: viewModel.isMuted ? "mic.slash" : "mic")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(viewModel.isMuted ? StarClTheme.muteRed : Color(hex: 0x666666))
            }
        }
    }

    // MARK: - Mic Button

    private var micButton: some View {
        VStack(spacing: 6) {
            Button(action: { handleMicTap() }) {
                ZStack {
                    // Pulse rings (visible when active)
                    if isSessionActive {
                        PulseRingView()
                    }

                    // Core circle
                    Circle()
                        .fill(isSessionActive ? StarClTheme.micCoreActiveBg : StarClTheme.micCoreBg)
                        .frame(width: 68, height: 68)
                        .overlay(
                            Circle()
                                .stroke(StarClTheme.teal, lineWidth: 1.5)
                        )
                        .overlay(
                            Group {
                                if isSessionActive && !viewModel.isMuted {
                                    // Wave bars (active + unmuted)
                                    WaveBarsView()
                                } else if isSessionActive && viewModel.isMuted {
                                    // Muted mic icon
                                    Image(systemName: "mic.slash.fill")
                                        .font(.system(size: 20))
                                        .foregroundColor(StarClTheme.muteRed)
                                } else {
                                    // Mic icon (idle)
                                    Image(systemName: "mic.fill")
                                        .font(.system(size: 20))
                                        .foregroundColor(StarClTheme.teal)
                                }
                            }
                        )
                }
            }
            .disabled(viewModel.sessionState == .connecting)

            Text(micStatusText)
                .font(.system(size: 10))
                .tracking(0.8)
                .foregroundColor(isSessionActive ? StarClTheme.teal : StarClTheme.labelText)
        }
    }

    private var isSessionActive: Bool {
        viewModel.sessionState == .active
    }

    private var micStatusText: String {
        switch viewModel.sessionState {
        case .idle, .stopped:
            return "TAP TO START"
        case .connecting:
            return "CONNECTING"
        case .active:
            return viewModel.isMuted ? "TAP TO UNMUTE" : "LISTENING"
        }
    }

    private func handleMicTap() {
        switch viewModel.sessionState {
        case .idle, .stopped:
            viewModel.reset()
            viewModel.tapStart()
        case .active:
            // Tap toggles mute; long-press or "New Conversation" ends the session.
            viewModel.toggleMute()
        case .connecting:
            viewModel.tapStop()
        }
    }

    // MARK: - New Conversation Button

    private var newConversationButton: some View {
        Button(action: {
            if viewModel.sessionState == .active || viewModel.sessionState == .connecting {
                viewModel.tapStop()
            }
        }) {
            ZStack {
                Circle()
                    .fill(isSessionActive ? StarClTheme.sideButtonBg : StarClTheme.sideButtonBg)
                    .frame(width: 44, height: 44)
                    .overlay(Circle().stroke(
                        isSessionActive ? StarClTheme.muteRed.opacity(0.6) : StarClTheme.sideButtonBorder,
                        lineWidth: 1
                    ))

                Image(systemName: "plus.message")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(isSessionActive ? StarClTheme.muteRed : Color(hex: 0x666666))
            }
        }
        .disabled(!isSessionActive && viewModel.sessionState != .connecting)
    }

    /// Known agent names in display order.
    private var agentNames: [String] {
        ["shijing", "eva", "ming", "ellen"]
    }

}

// MARK: - Message Row

struct MessageRowView: View {
    let line: TranscriptLine
    let isMuted: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text(StarClTheme.speakerLabel(for: line.speaker))
                .font(.system(size: 10, weight: .semibold))
                .tracking(0.6)
                .foregroundColor(StarClTheme.speakerColor(for: line.speaker))
                .frame(width: 52, alignment: .leading)
                .padding(.top, 3)

            if !line.isFinal && line.text.isEmpty {
                // Typing indicator
                TypingDotsView()
            } else {
                Text(line.text)
                    .font(.system(size: 13))
                    .foregroundColor(isMuted ? StarClTheme.mutedText : StarClTheme.messageText)
                    .lineSpacing(4)
            }
        }
    }
}

// MARK: - Typing Dots

struct TypingDotsView: View {
    @State private var animating = false

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(StarClTheme.mutedText)
                    .frame(width: 5, height: 5)
                    .scaleEffect(animating ? 1.2 : 1.0)
                    .opacity(animating ? 1.0 : 0.25)
                    .animation(
                        .easeInOut(duration: 1.3)
                        .repeatForever(autoreverses: true)
                        .delay(Double(index) * 0.2),
                        value: animating
                    )
            }
        }
        .frame(height: 18)
        .onAppear { animating = true }
    }
}

// MARK: - Pulse Rings

struct PulseRingView: View {
    @State private var animating = false

    var body: some View {
        ZStack {
            Circle()
                .stroke(StarClTheme.teal.opacity(0.19), lineWidth: 1.5)
                .frame(width: 68, height: 68)
                .scaleEffect(animating ? 1.7 : 1.0)
                .opacity(animating ? 0 : 1)

            Circle()
                .stroke(StarClTheme.teal.opacity(0.08), lineWidth: 1)
                .frame(width: 68, height: 68)
                .scaleEffect(animating ? 2.1 : 1.0)
                .opacity(animating ? 0 : 0.7)
        }
        .animation(
            .easeOut(duration: 1.6).repeatForever(autoreverses: false),
            value: animating
        )
        .onAppear { animating = true }
    }
}

// MARK: - Wave Bars

struct WaveBarsView: View {
    @State private var animating = false

    private let barCount = 5
    private let delays: [Double] = [-0.5, -0.3, -0.1, -0.4, -0.2]

    var body: some View {
        HStack(spacing: 2.5) {
            ForEach(0..<barCount, id: \.self) { index in
                RoundedRectangle(cornerRadius: 2)
                    .fill(StarClTheme.teal)
                    .frame(width: 2.5, height: animating ? 16 : 4)
                    .animation(
                        .easeInOut(duration: 0.85)
                        .repeatForever(autoreverses: true)
                        .delay(delays[index] + 0.5),
                        value: animating
                    )
            }
        }
        .onAppear { animating = true }
    }
}

// MARK: - Preview

#Preview {
    ContentView()
}
