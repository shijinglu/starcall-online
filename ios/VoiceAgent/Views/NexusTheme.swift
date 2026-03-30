import SwiftUI

/// Design tokens for the NEXUS dark UI theme.
enum NexusTheme {

    // MARK: - Background Colors

    static let background = Color(hex: 0x0A0A10)
    static let cardBackground = Color(hex: 0x0F0F18)
    static let cardBorder = Color(hex: 0x181824)
    static let divider = Color(hex: 0x14141E)
    static let bottomBorder = Color(hex: 0x12121C)
    static let sideButtonBg = Color(hex: 0x111118)
    static let sideButtonBorder = Color(hex: 0x1E1E2C)
    static let micCoreBg = Color(hex: 0x111820)
    static let micCoreActiveBg = Color(hex: 0x0F1C18)

    // MARK: - Text Colors

    static let primaryText = Color(hex: 0xDDDDE8)
    static let messageText = Color(hex: 0xA8A8BE)
    static let mutedText = Color(hex: 0x3E3E54)
    static let labelText = Color(hex: 0x3A3A50)
    static let agentLabel = Color(hex: 0x55556A)
    static let subtitleText = Color(hex: 0x30304A)

    // MARK: - Accent Colors

    static let teal = Color(hex: 0x2CC8A4)
    static let amber = Color(hex: 0xF5A623)
    static let muteRed = Color(hex: 0xE84A4A)

    // MARK: - Speaker Colors

    static let speakerYou = Color(hex: 0x5580FF)
    static let speakerNexus = Color(hex: 0x2CC8A4)
    static let speakerEllen = Color(hex: 0xE879A0)
    static let speakerShijing = Color(hex: 0xF5A623)
    static let speakerEva = Color(hex: 0xA78BFA)
    static let speakerMing = Color(hex: 0x17B8D4)

    /// Returns the speaker color for a given speaker name.
    static func speakerColor(for speaker: String) -> Color {
        switch speaker.lowercased() {
        case "user", "you":
            return speakerYou
        case "moderator", "nexus":
            return speakerNexus
        case "ellen":
            return speakerEllen
        case "shijing":
            return speakerShijing
        case "eva":
            return speakerEva
        case "ming":
            return speakerMing
        default:
            return messageText
        }
    }

    /// Returns the display name for a speaker (uppercased label).
    static func speakerLabel(for speaker: String) -> String {
        switch speaker.lowercased() {
        case "user":
            return "YOU"
        case "moderator":
            return "NEXUS"
        default:
            return speaker.uppercased()
        }
    }
}

// MARK: - Agent Definition

/// Visual definition for an agent avatar in the agent strip.
struct AgentDefinition {
    let key: String
    let initials: String
    let ringColor: Color
    let name: String

    static let all: [AgentDefinition] = [
        AgentDefinition(key: "shijing", initials: "SJ", ringColor: NexusTheme.speakerShijing, name: "Shijing"),
        AgentDefinition(key: "eva", initials: "EV", ringColor: NexusTheme.speakerEva, name: "Eva"),
        AgentDefinition(key: "ming", initials: "MG", ringColor: NexusTheme.speakerMing, name: "Ming"),
        AgentDefinition(key: "ellen", initials: "EL", ringColor: NexusTheme.speakerEllen, name: "Ellen"),
    ]

    static func definition(for key: String) -> AgentDefinition? {
        all.first { $0.key == key.lowercased() }
    }
}

// MARK: - Color Extension

extension Color {
    init(hex: UInt32, opacity: Double = 1.0) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            opacity: opacity
        )
    }
}
