import SwiftUI

/// Shows overall meeting progress when multiple agents are dispatched.
///
/// Displays a progress bar with completed/total count and lists remaining agents.
struct MeetingProgressView: View {
    let completed: Int
    let total: Int
    let pending: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "person.3.fill")
                    .foregroundColor(.blue)

                Text("Meeting: \(completed) / \(total) agents done")
                    .font(.subheadline)
                    .fontWeight(.medium)

                Spacer()
            }

            ProgressView(value: Double(completed), total: Double(max(total, 1)))
                .tint(.blue)

            if !pending.isEmpty {
                Text("Remaining: \(pending.joined(separator: ", "))")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(12)
        .background(Color.blue.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Preview

#Preview {
    MeetingProgressView(
        completed: 2,
        total: 4,
        pending: ["shijing", "ming"]
    )
    .padding()
}
