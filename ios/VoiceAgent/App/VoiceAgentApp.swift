import SwiftUI

@main
struct VoiceAgentApp: App {
    init() {
        Log.info("App launched — log file: \(Log.currentLogFileURL.path)", tag: "VoiceAgentApp")
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
