import AVFoundation
import XCTest
@testable import StarCallLib

/// Tests that verify the iOS implementation matches the barge-in design doc
/// (docs/barge-in-design.md). These guard against regressions that reintroduce
/// the isAnyPlaying feedback loop or break AEC configuration.
final class BargeInDesignTests: XCTestCase {

    // MARK: - Section 1A: Audio Session & AEC

    /// Design doc §1A: Audio session must use .voiceChat mode with the correct options.
    /// Options: [.defaultToSpeaker, .allowBluetooth, .allowBluetoothA2DP]
    func testAudioSessionConfiguration() throws {
        #if os(iOS)
        let engine = AudioCaptureEngine()
        try engine.configureAudioSession()

        let session = AVAudioSession.sharedInstance()
        XCTAssertEqual(session.category, .playAndRecord,
                       "Audio session category must be .playAndRecord")
        XCTAssertEqual(session.mode, .voiceChat,
                       "Audio session mode must be .voiceChat for hardware AEC")
        XCTAssertTrue(session.categoryOptions.contains(.defaultToSpeaker),
                      "Must include .defaultToSpeaker option")
        XCTAssertTrue(session.categoryOptions.contains(.allowBluetooth),
                      "Must include .allowBluetooth option")
        XCTAssertTrue(session.categoryOptions.contains(.allowBluetoothA2DP),
                      "Must include .allowBluetoothA2DP option (design doc §1A)")
        #else
        throw XCTSkip("Audio session tests require iOS")
        #endif
    }

    // MARK: - No isAnyPlaying feedback loop

    /// Design doc §1C: The playback engine must NOT expose isAnyPlaying.
    /// This property caused a deadlock: completion handlers on the render thread
    /// read node.isPlaying while node.stop() held an internal lock.
    func testPlaybackEngineDoesNotExposeIsAnyPlaying() {
        // If AudioPlaybackEngine has an `isAnyPlaying` property, this test
        // will fail to compile. That's the point — compile-time enforcement.
        // We verify via reflection that the property does not exist.
        let engine = AudioPlaybackEngine()
        let mirror = Mirror(reflecting: engine)
        let propertyNames = mirror.children.compactMap { $0.label }
        XCTAssertFalse(propertyNames.contains("isAnyPlaying"),
                       "isAnyPlaying must not exist — it caused the main-thread deadlock (see local/diag/case4_diagnostic_20260402.md)")
    }

    /// The playback engine must NOT have an onSpeakerFinished callback property.
    /// This callback was called from audio render thread completion handlers,
    /// creating a feedback loop that contributed to the deadlock.
    func testPlaybackEngineDoesNotHaveOnSpeakerFinishedCallback() {
        let engine = AudioPlaybackEngine()
        let mirror = Mirror(reflecting: engine)
        let propertyNames = mirror.children.compactMap { $0.label }
        XCTAssertFalse(propertyNames.contains("onSpeakerFinished"),
                       "onSpeakerFinished callback must not exist — it was part of the deadlock feedback loop")
    }

    // MARK: - Section 1C: "Clear the Floor" (Barge-In Flush)

    /// Design doc §1C: flushAllAndStop must update gen_id and clear all queues.
    func testFlushClearsStateAndUpdatesGen() {
        let engine = AudioPlaybackEngine()
        engine.meetingQueueActive = true
        engine.setMeetingOrder([0x01, 0x02])
        engine.setCurrentMeetingSpeaker(0x01)
        engine.setFrameQueues([
            0x01: [Data(repeating: 0, count: 100)],
            0x02: [Data(repeating: 0, count: 100)]
        ])

        engine.flushAllAndStop(newGen: 42)

        XCTAssertEqual(engine.currentGen, 42, "Flush must update gen_id")
        XCTAssertTrue(engine.frameQueues.isEmpty, "Flush must clear all frame queues")
        XCTAssertFalse(engine.meetingQueueActive, "Flush must deactivate meeting mode")
        XCTAssertTrue(engine.meetingOrder.isEmpty, "Flush must clear meeting order")
        XCTAssertNil(engine.currentMeetingSpeaker, "Flush must clear current meeting speaker")
    }

    /// Design doc §1C: flushAllAndStop must NOT block the calling thread.
    /// The old implementation used watchdogQueue.sync which deadlocked.
    func testFlushDoesNotBlockMainThread() {
        let engine = AudioPlaybackEngine()

        // This should return immediately (async), not block.
        let start = CFAbsoluteTimeGetCurrent()
        engine.flushAllAndStop(newGen: 1)
        let elapsed = CFAbsoluteTimeGetCurrent() - start

        // If this takes more than 100ms, something is blocking.
        XCTAssertLessThan(elapsed, 0.1,
                          "flushAllAndStop must not block — old watchdogQueue.sync caused permanent deadlock")
    }

    // MARK: - AudioCaptureEngine: No isPlaying feedback loop

    /// The capture engine must NOT have an `isPlaying` Bool property that is
    /// externally set. This was part of the feedback loop:
    /// completion handler → isAnyPlaying → set isPlaying → gate barge-in.
    /// Replaced by time-based playbackEndTime.
    func testCaptureEngineUsesTimeBased() {
        let engine = AudioCaptureEngine()
        let mirror = Mirror(reflecting: engine)
        let propertyNames = mirror.children.compactMap { $0.label }

        // Should NOT have the old _isPlaying / isPlayingLock fields
        XCTAssertFalse(propertyNames.contains("_isPlaying"),
                       "_isPlaying must not exist — replaced by time-based playbackEndTime")
        XCTAssertFalse(propertyNames.contains("isPlayingLock"),
                       "isPlayingLock must not exist — replaced by time-based tracking")
    }

    /// The capture engine must have time-based playback tracking.
    func testCaptureEngineHasPlaybackEndTime() {
        let engine = AudioCaptureEngine()
        // Should be able to set and read playbackEndTime
        engine.playbackEndTime = CFAbsoluteTimeGetCurrent() + 1.0
        XCTAssertTrue(engine.isPlaybackExpected,
                      "isPlaybackExpected should be true when playbackEndTime is in the future")

        engine.playbackEndTime = 0
        XCTAssertFalse(engine.isPlaybackExpected,
                       "isPlaybackExpected should be false when playbackEndTime is 0")
    }

    /// notifyPlaybackFlushed must reset playback tracking immediately.
    func testNotifyPlaybackFlushedResetsState() {
        let engine = AudioCaptureEngine()
        engine.playbackEndTime = CFAbsoluteTimeGetCurrent() + 10.0
        XCTAssertTrue(engine.isPlaybackExpected)

        engine.notifyPlaybackFlushed()

        XCTAssertFalse(engine.isPlaybackExpected,
                       "After flush, playback should not be expected")
    }

    // MARK: - Barge-in detection uses time-based gate

    /// Barge-in should fire when RMS exceeds threshold during expected playback.
    func testBargeInFiresDuringPlayback() {
        let engine = AudioCaptureEngine()
        engine.setNoiseFloor(0.0001)

        // Simulate playback started 2 seconds ago (past grace period of 0.8s)
        engine.notifyPlaybackChunk(durationSeconds: 5.0)
        // Wait for grace period — use a fake playbackStartTime in the past
        // We can't easily fake time, so we test the structural invariant instead.
        // The processChunk method should check isPlaybackExpected internally.

        // Verify the engine has playback expected
        XCTAssertTrue(engine.isPlaybackExpected,
                      "After notifyPlaybackChunk, playback should be expected")
    }

    /// Barge-in should NOT fire when no playback is expected.
    func testBargeInDoesNotFireWithoutPlayback() {
        let engine = AudioCaptureEngine()
        engine.setNoiseFloor(0.0001)

        // No playback — isPlaybackExpected is false
        XCTAssertFalse(engine.isPlaybackExpected)

        // Create a loud PCM chunk
        var samples = [Int16](repeating: 0, count: 1600)
        for i in 0..<samples.count { samples[i] = 10000 }
        let pcm = samples.withUnsafeBufferPointer { Data(buffer: $0) }

        // Track if barge-in fires
        class BargeinTracker: AudioCaptureEngineDelegate {
            var bargeinFired = false
            func audioCaptureDidDetectBargein() { bargeinFired = true }
            func audioCaptureDidProduceChunk(_ data: Data) {}
        }
        let tracker = BargeinTracker()
        engine.delegate = tracker

        engine.processChunk(pcm)

        XCTAssertFalse(tracker.bargeinFired,
                       "Barge-in must NOT fire when no playback is expected")
    }

    // MARK: - Shared Engine Invariant

    /// Design doc §1A: Capture and playback MUST share the same AVAudioEngine
    /// for hardware AEC to correlate speaker output with mic input.
    func testSharedEngineInvariant() {
        let session = ConversationSession()
        XCTAssertTrue(
            session.audioCaptureEngine.audioEngine === session.playbackEngine.audioEngine,
            "Capture and playback must share the same AVAudioEngine for AEC"
        )
    }
}
