import Foundation

/// Detects main thread stalls by pinging from a background timer.
/// Logs a warning with duration whenever the main thread is unresponsive
/// for longer than `threshold` seconds.
public final class MainThreadWatchdog {

    public static let shared = MainThreadWatchdog()

    private let threshold: TimeInterval = 0.1  // 100ms
    private let interval: TimeInterval = 0.05  // check every 50ms
    private var timer: DispatchSourceTimer?
    private var pendingPingTime: CFAbsoluteTime = 0
    private let lock = NSLock()

    private init() {}

    /// Start the watchdog. Call once at app launch.
    public func start() {
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .userInteractive))
        timer.schedule(deadline: .now(), repeating: interval)
        timer.setEventHandler { [weak self] in
            self?.ping()
        }
        timer.resume()
        self.timer = timer
        Log.info("DIAG-FREEZE: MainThreadWatchdog started (threshold=\(threshold * 1000)ms)", tag: "MainThreadWatchdog")
    }

    /// Stop the watchdog.
    func stop() {
        timer?.cancel()
        timer = nil
    }

    private func ping() {
        let sendTime = CFAbsoluteTimeGetCurrent()

        lock.lock()
        // If there's already a pending ping that hasn't been answered, check if it's stale.
        let pending = pendingPingTime
        lock.unlock()

        if pending > 0 {
            let staleness = sendTime - pending
            if staleness > threshold {
                // Main thread hasn't responded — it's blocked.
                // Log from background thread (Log is thread-safe).
                Log.warning("DIAG-FREEZE: MAIN THREAD BLOCKED for \(String(format: "%.0f", staleness * 1000))ms (and counting)", tag: "MainThreadWatchdog")
            }
            // Don't stack up more pings — wait for the current one.
            return
        }

        lock.lock()
        pendingPingTime = sendTime
        lock.unlock()

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.lock.lock()
            let sentAt = self.pendingPingTime
            self.pendingPingTime = 0
            self.lock.unlock()

            let responseTime = CFAbsoluteTimeGetCurrent()
            let latencyMs = (responseTime - sentAt) * 1000
            if latencyMs > self.threshold * 1000 {
                Log.warning("DIAG-FREEZE: MAIN THREAD STALL detected latency=\(String(format: "%.0f", latencyMs))ms", tag: "MainThreadWatchdog")
            }
        }
    }
}
