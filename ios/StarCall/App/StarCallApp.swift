#if SWIFT_PACKAGE
import StarCallLib
#endif
import SwiftUI

@main
struct StarCallApp: App {
    init() {
        Log.clearAll()
        Log.info("App launched — log file: \(Log.currentLogFileURL.path)", tag: "StarCallApp")
        MainThreadWatchdog.shared.start()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
