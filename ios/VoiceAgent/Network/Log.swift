import Foundation
import os

/// Lightweight logger that writes to both Apple's unified console log (os.Logger)
/// and a rotating log file in the app's Documents directory.
///
/// Usage:
///     Log.info("Session started", tag: "ConversationSession")
///     Log.error("Send failed: \(error)", tag: "WebSocketTransport")
///
/// Log files are stored at:
///     <Documents>/Logs/VoiceAgent.log          (current)
///     <Documents>/Logs/VoiceAgent.1.log         (previous, after rotation)
public enum Log {

    // MARK: - Configuration

    /// Maximum size of a single log file before rotation (5 MB).
    static let maxFileSize: UInt64 = 5 * 1024 * 1024
    /// Number of rotated backup files to keep.
    static let maxBackups = 3

    // MARK: - Internals

    private static let osLog = os.Logger(subsystem: Bundle.main.bundleIdentifier ?? "VoiceAgent", category: "app")

    private static let logDirectory: URL = {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dir = docs.appendingPathComponent("Logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    private static let logFileURL: URL = logDirectory.appendingPathComponent("VoiceAgent.log")

    /// Serial queue to ensure thread-safe file writes.
    private static let queue = DispatchQueue(label: "com.voiceagent.log", qos: .utility)

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss.SSS"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    // MARK: - Public API

    public static func debug(_ message: String, tag: String = "") {
        log(level: .debug, message: message, tag: tag)
    }

    public static func info(_ message: String, tag: String = "") {
        log(level: .info, message: message, tag: tag)
    }

    public static func warning(_ message: String, tag: String = "") {
        log(level: .warning, message: message, tag: tag)
    }

    public static func error(_ message: String, tag: String = "") {
        log(level: .error, message: message, tag: tag)
    }

    // MARK: - Level

    enum Level: String {
        case debug = "DEBUG"
        case info = "INFO"
        case warning = "WARN"
        case error = "ERROR"
    }

    // MARK: - Core

    private static func log(level: Level, message: String, tag: String) {
        let timestamp = dateFormatter.string(from: Date())
        let prefix = tag.isEmpty ? "" : "[\(tag)] "
        let line = "\(timestamp) \(level.rawValue.padding(toLength: 5, withPad: " ", startingAt: 0)) \(prefix)\(message)"

        // Console via os.Logger
        switch level {
        case .debug: osLog.debug("\(line, privacy: .public)")
        case .info:  osLog.info("\(line, privacy: .public)")
        case .warning: osLog.warning("\(line, privacy: .public)")
        case .error: osLog.error("\(line, privacy: .public)")
        }

        // File
        queue.async {
            appendToFile(line)
        }
    }

    // MARK: - File I/O

    private static func appendToFile(_ line: String) {
        let entry = line + "\n"
        guard let data = entry.data(using: .utf8) else { return }

        if !FileManager.default.fileExists(atPath: logFileURL.path) {
            FileManager.default.createFile(atPath: logFileURL.path, contents: nil)
        }

        guard let handle = try? FileHandle(forWritingTo: logFileURL) else { return }
        handle.seekToEndOfFile()
        handle.write(data)
        handle.closeFile()

        rotateIfNeeded()
    }

    private static func rotateIfNeeded() {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: logFileURL.path),
              let size = attrs[.size] as? UInt64,
              size >= maxFileSize else { return }

        let fm = FileManager.default

        // Remove oldest backup.
        let oldest = logDirectory.appendingPathComponent("VoiceAgent.\(maxBackups).log")
        try? fm.removeItem(at: oldest)

        // Shift existing backups: 2 -> 3, 1 -> 2, etc.
        for i in stride(from: maxBackups - 1, through: 1, by: -1) {
            let src = logDirectory.appendingPathComponent("VoiceAgent.\(i).log")
            let dst = logDirectory.appendingPathComponent("VoiceAgent.\(i + 1).log")
            try? fm.moveItem(at: src, to: dst)
        }

        // Current -> .1
        let backup1 = logDirectory.appendingPathComponent("VoiceAgent.1.log")
        try? fm.moveItem(at: logFileURL, to: backup1)
    }

    // MARK: - Utility

    /// Returns the URL of the current log file (useful for sharing/exporting).
    public static var currentLogFileURL: URL { logFileURL }

    /// Returns URLs of all log files (current + backups).
    static var allLogFileURLs: [URL] {
        var urls = [logFileURL]
        for i in 1...maxBackups {
            let url = logDirectory.appendingPathComponent("VoiceAgent.\(i).log")
            if FileManager.default.fileExists(atPath: url.path) {
                urls.append(url)
            }
        }
        return urls
    }
}
