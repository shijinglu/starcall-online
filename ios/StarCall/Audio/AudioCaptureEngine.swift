import AVFoundation
import Foundation

// MARK: - Delegate Protocol

/// Delegate protocol for AudioCaptureEngine events.
protocol AudioCaptureEngineDelegate: AnyObject {
    /// Called when barge-in is detected (RMS exceeds noise floor + threshold during playback).
    func audioCaptureDidDetectBargein()
    /// Called for every 100ms PCM chunk produced (3200 bytes, 16kHz int16 LE mono).
    func audioCaptureDidProduceChunk(_ data: Data)
}

// MARK: - AudioCaptureEngine

/// Captures microphone audio at 44.1kHz, downsamples to 16kHz int16 mono,
/// produces 100ms chunks (3200 bytes), and detects barge-in via RMS analysis.
final class AudioCaptureEngine {

    weak var delegate: AudioCaptureEngineDelegate?

    /// - Parameter sharedEngine: If provided, this engine is used for capture
    ///   and the caller is responsible for starting/stopping it. Pass `nil` to
    ///   create an internal engine (used by tests and standalone operation).
    init(sharedEngine: AVAudioEngine? = nil) {
        if let shared = sharedEngine {
            self.audioEngine = shared
            self.ownsEngine = false
        } else {
            self.audioEngine = AVAudioEngine()
            self.ownsEngine = true
        }
    }

    /// Whether the system is currently playing back audio (used for barge-in detection and noise floor updates).
    /// Thread-safe: accessed from audio render, WebSocket, and main threads.
    var isPlaying: Bool {
        get { isPlayingLock.withLock { _isPlaying } }
        set {
            let oldValue = isPlayingLock.withLock {
                let old = _isPlaying
                _isPlaying = newValue
                return old
            }
            if oldValue != newValue {
                let threadName = Thread.current.isMainThread ? "main" : (Thread.current.name ?? "bg-\(Thread.current)")
                Log.info("DIAG-ISPLAYING: \(oldValue)->\(newValue) thread=\(threadName)", tag: "AudioCaptureEngine")
            }
        }
    }
    private var _isPlaying: Bool = false
    private let isPlayingLock = NSLock()

    // MARK: - Noise Floor / Barge-In Configuration

    /// Adaptive noise floor (EMA of quiet-period RMS). Starts at a low baseline.
    private(set) var noiseFloor: Float = 0.001
    /// EMA smoothing factor for noise floor updates.
    private let noiseFloorAlpha: Float = 0.1
    /// dB above the noise floor required to trigger a barge-in.
    /// Raised from 15 to 25: AEC residual peaks at ~20 dB, real speech at 28-35 dB.
    private let bargeInThresholdDB: Float = 25.0

    /// Minimum interval between barge-in triggers (seconds).
    /// Prevents the ~10/s spam observed when speaker bleed keeps RMS above threshold.
    private let bargeInCooldown: TimeInterval = 1.0
    /// Timestamp of the last barge-in event.
    private var lastBargeInTime: TimeInterval = 0

    // MARK: - Diagnostics
    /// Total barge-in fire count for this session.
    private var bargeinFireCount: Int = 0
    /// Periodic summary counters (reset every summary interval).
    private var diagPlaybackChunks: Int = 0
    private var diagPlaybackMaxRMS: Float = 0
    private var diagPlaybackExceedCount: Int = 0
    private var diagSilenceChunks: Int = 0
    private var diagLastSummaryTime: TimeInterval = 0
    /// Summary interval in seconds.
    private let diagSummaryInterval: TimeInterval = 10.0

    // MARK: - Audio Engine Components

    /// The AVAudioEngine used for mic capture.
    /// When a shared engine is provided (ownsEngine=false), this engine is also
    /// used by AudioPlaybackEngine so that hardware AEC can correlate output/input.
    let audioEngine: AVAudioEngine
    /// Whether this instance owns (and should start/stop) the engine.
    private let ownsEngine: Bool
    private var converter: AVAudioConverter?

    /// Accumulation buffer for building 100ms output chunks from variable-size tap callbacks.
    private var accumulationBuffer = Data()
    /// Target output chunk size: 100ms at 16kHz int16 mono = 1600 samples * 2 bytes = 3200 bytes.
    static let chunkSize = 3200

    // MARK: - Audio Session Configuration

    /// Configure AVAudioSession for voice-first operation.
    /// Uses .voiceChat mode which enables hardware AEC (Acoustic Echo Canceller) and AGC.
    func configureAudioSession() throws {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetooth])
        try session.setActive(true)
        logAudioRoute()
        #endif
    }

    /// Log current audio route for AEC diagnostics.
    func logAudioRoute() {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        let route = session.currentRoute
        let inputs = route.inputs.map { "\($0.portName)(\($0.portType.rawValue))" }.joined(separator: ", ")
        let outputs = route.outputs.map { "\($0.portName)(\($0.portType.rawValue))" }.joined(separator: ", ")
        #if targetEnvironment(simulator)
        let envType = "SIMULATOR"
        #else
        let envType = "DEVICE"
        #endif
        Log.info("DIAG-ECHO: audioRoute env=\(envType) mode=\(session.mode.rawValue) category=\(session.category.rawValue) inputs=[\(inputs)] outputs=[\(outputs)] sampleRate=\(session.sampleRate) ioBufferDuration=\(session.ioBufferDuration)", tag: "AudioCaptureEngine")
        #endif
    }

    // MARK: - Capture Control

    /// Begin capturing audio from the microphone.
    /// Installs a tap on the input node, downsamples to 16kHz int16 mono,
    /// and emits 100ms chunks via the delegate.
    func startCapture() {
        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        Log.info("Input format: \(inputFormat)", tag: "AudioCaptureEngine")

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16000,
            channels: 1,
            interleaved: true
        ) else {
            Log.error("Failed to create target format", tag: "AudioCaptureEngine")
            return
        }

        guard let audioConverter = AVAudioConverter(from: inputFormat, to: targetFormat) else {
            Log.error("Failed to create converter from \(inputFormat) to \(targetFormat)", tag: "AudioCaptureEngine")
            return
        }
        self.converter = audioConverter

        // Buffer size of 4410 samples at 44.1kHz is approximately 100ms.
        inputNode.installTap(onBus: 0, bufferSize: 4410, format: inputFormat) { [weak self] buffer, _ in
            self?.processBuffer(buffer, converter: audioConverter, targetFormat: targetFormat)
        }

        if ownsEngine {
            do {
                try audioEngine.start()
                Log.info("Audio engine started successfully", tag: "AudioCaptureEngine")
            } catch {
                Log.error("Failed to start audio engine: \(error)", tag: "AudioCaptureEngine")
            }
        } else {
            Log.info("Using shared audio engine (AEC-enabled), not starting independently", tag: "AudioCaptureEngine")
        }
    }

    /// Stop capturing audio.
    func stopCapture() {
        audioEngine.inputNode.removeTap(onBus: 0)
        if ownsEngine {
            audioEngine.stop()
        }
        accumulationBuffer.removeAll()
    }

    // MARK: - Buffer Processing

    /// Process a tap buffer: downsample to 16kHz int16, accumulate, and emit 100ms chunks.
    private func processBuffer(_ buffer: AVAudioPCMBuffer, converter: AVAudioConverter, targetFormat: AVAudioFormat) {
        // Calculate output frame capacity based on input-to-output sample rate ratio.
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let outputFrameCapacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1

        guard let outputBuffer = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outputFrameCapacity) else {
            return
        }

        var error: NSError?
        let status = converter.convert(to: outputBuffer, error: &error) { _, outStatus in
            outStatus.pointee = .haveData
            return buffer
        }

        guard status != .error, error == nil else {
            return
        }

        // Extract int16 samples from the output buffer.
        guard let int16Data = outputBuffer.int16ChannelData else { return }
        let sampleCount = Int(outputBuffer.frameLength)
        let byteCount = sampleCount * MemoryLayout<Int16>.size
        let data = Data(bytes: int16Data[0], count: byteCount)

        accumulationBuffer.append(data)

        // Emit complete 100ms chunks.
        while accumulationBuffer.count >= Self.chunkSize {
            let chunk = accumulationBuffer.prefix(Self.chunkSize)
            accumulationBuffer.removeFirst(Self.chunkSize)
            processChunk(Data(chunk), isPlaying: isPlaying)
        }
    }

    // MARK: - RMS & Barge-In

    /// Compute RMS of int16 PCM samples, normalized to 0.0-1.0 range.
    func computeRMS(_ samples: [Int16]) -> Float {
        guard !samples.isEmpty else { return 0.0 }
        let sumSquares = samples.reduce(Float(0.0)) { acc, s in acc + Float(s) * Float(s) }
        return sqrt(sumSquares / Float(samples.count)) / 32768.0
    }

    /// Process a 100ms PCM chunk: update noise floor, detect barge-in, emit to delegate.
    func processChunk(_ pcm16: Data, isPlaying: Bool) {
        let samples = pcm16.withUnsafeBytes { rawBuffer -> [Int16] in
            guard let baseAddress = rawBuffer.baseAddress else { return [] }
            let bound = baseAddress.bindMemory(to: Int16.self, capacity: rawBuffer.count / MemoryLayout<Int16>.size)
            return Array(UnsafeBufferPointer(start: bound, count: rawBuffer.count / MemoryLayout<Int16>.size))
        }
        let rms = computeRMS(samples)

        // Update adaptive noise floor during quiet periods (when not playing).
        if !isPlaying {
            noiseFloor = noiseFloorAlpha * rms + (1 - noiseFloorAlpha) * noiseFloor
            diagSilenceChunks += 1
        }

        // Track playback-period stats for periodic summary.
        if isPlaying {
            diagPlaybackChunks += 1
            diagPlaybackMaxRMS = max(diagPlaybackMaxRMS, rms)
            let rmsDB = 20 * log10(rms / max(noiseFloor, 1e-10))
            if rmsDB > bargeInThresholdDB {
                diagPlaybackExceedCount += 1
            }
        }

        // DIAG-ECHO: Periodic summary every 10s instead of per-chunk spam.
        let now = ProcessInfo.processInfo.systemUptime
        if now - diagLastSummaryTime >= diagSummaryInterval {
            Log.info("DIAG-ECHO: SUMMARY playbackChunks=\(diagPlaybackChunks) silenceChunks=\(diagSilenceChunks) maxRMS_during_playback=\(diagPlaybackMaxRMS) exceedThresholdCount=\(diagPlaybackExceedCount) noiseFloor=\(noiseFloor) bargeinFires=\(bargeinFireCount)", tag: "AudioCaptureEngine")
            diagPlaybackChunks = 0
            diagPlaybackMaxRMS = 0
            diagPlaybackExceedCount = 0
            diagSilenceChunks = 0
            diagLastSummaryTime = now
        }

        // Barge-in detection: RMS exceeds noise floor by threshold while audio is playing.
        // Dispatch off the audio render thread — calling AVAudioPlayerNode.stop()
        // from within a tap callback deadlocks the audio I/O thread.
        // Debounce: only fire once per bargeInCooldown to avoid spamming cancel_all.
        if isPlaying {
            let rmsDB = 20 * log10(rms / max(noiseFloor, 1e-10))
            if rmsDB > bargeInThresholdDB && (now - lastBargeInTime) >= bargeInCooldown {
                bargeinFireCount += 1
                Log.info("DIAG-ECHO: BARGEIN_FIRED #\(bargeinFireCount) rms=\(rms) rmsDB=\(rmsDB)", tag: "AudioCaptureEngine")
                lastBargeInTime = now
                DispatchQueue.main.async { [weak self] in
                    self?.delegate?.audioCaptureDidDetectBargein()
                }
            }
        }

        // Always emit the chunk — audio is sent to Gemini even during playback.
        // TODO(echo): This is the likely cause of Gemini hearing its own TTS.
        // AEC reduces bleed but residual still reaches Gemini's server-side VAD.
        delegate?.audioCaptureDidProduceChunk(pcm16)
    }

    // MARK: - Test Helpers

    /// Expose noise floor setter for testing.
    func setNoiseFloor(_ value: Float) {
        noiseFloor = value
    }
}
