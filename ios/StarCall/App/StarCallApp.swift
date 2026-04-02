import SwiftUI

@main
struct StarCallApp: App {
    init() {
        Log.info("App launched — log file: \(Log.currentLogFileURL.path)", tag: "StarCallApp")
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
