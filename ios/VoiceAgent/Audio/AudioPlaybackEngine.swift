import AVFoundation
import Foundation

/// Callback when a speaker finishes playing all queued audio.
typealias SpeakerFinishedCallback = (UInt8) -> Void

/// Manages per-speaker audio playback with gen_id filtering, meeting mode,
/// and barge-in flush support.
final class AudioPlaybackEngine {

    // MARK: - State

    /// Current generation counter for zombie audio filtering.
    private(set) var currentGen: UInt8 = 0

    /// One AVAudioPlayerNode per speaker_id (0=moderator, 1-4=agents).
    private var playerNodes: [UInt8: AVAudioPlayerNode] = [:]
    /// Buffered PCM data per speaker.
    private(set) var frameQueues: [UInt8: [Data]] = [:]

    /// Whether meeting mode sequential delivery is active.
    var meetingQueueActive = false
    /// Speaker IDs in dispatch order for sequential meeting delivery.
    private(set) var meetingOrder: [UInt8] = []
    /// The speaker currently playing in meeting mode.
    private(set) var currentMeetingSpeaker: UInt8? = nil

    /// Callback fired when a speaker finishes all queued audio.
    var onSpeakerFinished: SpeakerFinishedCallback?

    /// The AVAudioEngine used for playback.
    private let audioEngine: AVAudioEngine

    /// The output format for playback: 16kHz int16 mono.
    private let playbackFormat: AVAudioFormat

    // MARK: - Init

    init() {
        audioEngine = AVAudioEngine()
        playbackFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16000,
            channels: 1,
            interleaved: true
        )!
    }

    /// Start the audio engine for playback.
    func start() throws {
        try audioEngine.start()
    }

    /// Stop the audio engine.
    func stop() {
        audioEngine.stop()
    }

    // MARK: - Player Node Management

    /// Get or create a player node for a given speaker.
    private func playerNode(for speakerId: UInt8) -> AVAudioPlayerNode {
        if let existing = playerNodes[speakerId] {
            return existing
        }
        let node = AVAudioPlayerNode()
        audioEngine.attach(node)
        audioEngine.connect(node, to: audioEngine.mainMixerNode, format: playbackFormat)
        playerNodes[speakerId] = node
        return node
    }

    // MARK: - Receiving Frames

    /// Receive an audio frame, filter by gen_id, and route to playback or buffer.
    func receiveAudioFrame(header: AudioFrameHeader, pcm: Data) {
        // Zombie audio prevention using RFC 1982 modular arithmetic.
        guard !isStale(frameGen: header.genId, currentGen: currentGen) else {
            return // silently discard stale frame
        }

        if meetingQueueActive {
            // Buffer for sequential meeting delivery.
            frameQueues[header.speakerId, default: []].append(pcm)

            // Add to meeting order if not already present.
            if !meetingOrder.contains(header.speakerId) {
                meetingOrder.append(header.speakerId)
            }

            maybeStartNextMeetingSpeaker()
        } else {
            // Direct playback.
            enqueueForPlayback(speakerId: header.speakerId, pcm: pcm)
        }
    }

    // MARK: - Direct Playback

    /// Schedule PCM data for immediate playback on the speaker's player node.
    private func enqueueForPlayback(speakerId: UInt8, pcm: Data) {
        let node = playerNode(for: speakerId)

        guard let buffer = pcmBuffer(from: pcm) else { return }

        if !node.isPlaying {
            node.play()
        }

        node.scheduleBuffer(buffer) { [weak self] in
            // Note: This fires per-buffer. For meeting mode we track separately.
            self?.onSpeakerFinished?(speakerId)
        }
    }

    /// Convert raw PCM Data (int16 LE) to an AVAudioPCMBuffer.
    private func pcmBuffer(from data: Data) -> AVAudioPCMBuffer? {
        let sampleCount = data.count / MemoryLayout<Int16>.size
        guard sampleCount > 0 else { return nil }

        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: playbackFormat,
            frameCapacity: AVAudioFrameCount(sampleCount)
        ) else { return nil }

        buffer.frameLength = AVAudioFrameCount(sampleCount)

        data.withUnsafeBytes { rawBuffer in
            guard let src = rawBuffer.baseAddress else { return }
            if let dst = buffer.int16ChannelData?[0] {
                memcpy(dst, src, data.count)
            }
        }

        return buffer
    }

    // MARK: - Barge-In Flush

    /// Flush all playback and reset meeting state for a new generation.
    func flushAllAndStop(newGen: UInt8) {
        currentGen = newGen

        // Stop all player nodes and clear all queues.
        for (_, node) in playerNodes {
            node.stop()
        }
        frameQueues.removeAll()
        meetingOrder.removeAll()
        currentMeetingSpeaker = nil
        meetingQueueActive = false
    }

    // MARK: - Skip Speaker (Meeting Mode)

    /// Cancel a specific speaker's stream and advance the meeting queue.
    func cancelStream(speakerId: UInt8) {
        playerNodes[speakerId]?.stop()
        frameQueues[speakerId]?.removeAll()

        // Remove from meeting order.
        if let idx = meetingOrder.firstIndex(of: speakerId) {
            meetingOrder.remove(at: idx)
        }

        // If this was the current meeting speaker, clear it and advance.
        if speakerId == currentMeetingSpeaker {
            currentMeetingSpeaker = nil
        }

        maybeStartNextMeetingSpeaker()
    }

    // MARK: - Meeting Mode Sequential Delivery

    /// Add a speaker to the meeting order (if not already present).
    func addToMeetingOrder(_ speakerId: UInt8) {
        if !meetingOrder.contains(speakerId) {
            meetingOrder.append(speakerId)
        }
    }

    /// Try to start the next meeting speaker if conditions are met.
    func maybeStartNextMeetingSpeaker() {
        guard meetingQueueActive,
              currentMeetingSpeaker == nil,
              let nextSpeaker = meetingOrder.first else { return }

        let queue = frameQueues[nextSpeaker] ?? []
        guard !queue.isEmpty else { return }

        currentMeetingSpeaker = nextSpeaker
        drainQueueToPlayer(speakerId: nextSpeaker)
    }

    /// Schedule all buffered frames for a speaker on its player node.
    private func drainQueueToPlayer(speakerId: UInt8) {
        guard let queue = frameQueues[speakerId], !queue.isEmpty else { return }

        let node = playerNode(for: speakerId)
        if !node.isPlaying {
            node.play()
        }

        // Schedule all buffered chunks.
        for (index, pcm) in queue.enumerated() {
            guard let buffer = pcmBuffer(from: pcm) else { continue }

            if index == queue.count - 1 {
                // Last buffer: attach completion handler to advance meeting.
                node.scheduleBuffer(buffer) { [weak self] in
                    DispatchQueue.main.async {
                        self?.onSpeakerFinished(speakerId: speakerId)
                    }
                }
            } else {
                node.scheduleBuffer(buffer)
            }
        }

        frameQueues[speakerId]?.removeAll()
    }

    /// Called when a speaker finishes all its queued audio in meeting mode.
    func onSpeakerFinished(speakerId: UInt8) {
        guard speakerId == currentMeetingSpeaker else { return }

        currentMeetingSpeaker = nil
        if !meetingOrder.isEmpty {
            meetingOrder.removeFirst()
        }
        maybeStartNextMeetingSpeaker()
    }

    // MARK: - Query

    /// Whether any player node is currently playing audio.
    var isAnyPlaying: Bool {
        playerNodes.values.contains { $0.isPlaying }
    }

    /// The speaker_id currently playing, if any.
    var currentlyPlayingSpeaker: UInt8? {
        if let meetingSpeaker = currentMeetingSpeaker {
            return meetingSpeaker
        }
        for (speakerId, node) in playerNodes where node.isPlaying {
            return speakerId
        }
        return nil
    }

    // MARK: - Test Helpers

    /// Set currentGen directly for testing.
    func setCurrentGen(_ gen: UInt8) {
        currentGen = gen
    }

    /// Set meeting order directly for testing.
    func setMeetingOrder(_ order: [UInt8]) {
        meetingOrder = order
    }

    /// Set current meeting speaker directly for testing.
    func setCurrentMeetingSpeaker(_ speaker: UInt8?) {
        currentMeetingSpeaker = speaker
    }

    /// Set frame queues directly for testing.
    func setFrameQueues(_ queues: [UInt8: [Data]]) {
        frameQueues = queues
    }
}
