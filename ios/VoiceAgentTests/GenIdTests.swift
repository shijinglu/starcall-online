import XCTest
@testable import VoiceAgent

/// Tests for RFC 1982 modular arithmetic gen_id staleness check.
///
/// The circular space 0-255 is divided into two halves:
/// - Stale (past) half: diff = (currentGen - frameGen) & 0xFF in range 1..<128
/// - Fresh (future or current) half: diff = 0 or diff in range 128..<256
final class GenIdTests: XCTestCase {

    // MARK: - Basic Cases

    func testSameGenIdIsNotStale() {
        // diff = 0 -> not stale
        XCTAssertFalse(isStale(frameGen: 5, currentGen: 5))
    }

    func testFrameOneBeforeCurrentIsStale() {
        // diff = 1 -> stale
        XCTAssertTrue(isStale(frameGen: 4, currentGen: 5))
    }

    func testFrameOneBehindIsStale() {
        // diff = 3 -> stale (in 1..<128)
        XCTAssertTrue(isStale(frameGen: 2, currentGen: 5))
    }

    func testFrameAheadIsNotStale() {
        // diff = (3 - 5) & 0xFF = 254, which is >= 128 -> not stale
        XCTAssertFalse(isStale(frameGen: 5, currentGen: 3))
    }

    func testFrameFarAheadIsNotStale() {
        // diff = (0 - 200) & 0xFF = 56, but that's frame=200, current=0
        // Actually: (0 - 200) & 0xFF = 56 which is in 1..<128 -> stale!
        // This is correct: 200 is "behind" 0 in the circular space (200 was 56 increments ago)
        XCTAssertTrue(isStale(frameGen: 200, currentGen: 0))
    }

    // MARK: - Wrap-Around Cases (Fix 1)

    func testWrapAroundGenId254VsCurrent1() {
        // diff = (1 - 254) & 0xFF = (-253) & 0xFF = 3 -> stale
        // 254 is 3 increments behind 1 (after wrap).
        XCTAssertTrue(isStale(frameGen: 254, currentGen: 1))
    }

    func testWrapAroundGenId255VsCurrent1() {
        // diff = (1 - 255) & 0xFF = (-254) & 0xFF = 2 -> stale
        XCTAssertTrue(isStale(frameGen: 255, currentGen: 1))
    }

    func testWrapAroundGenId0VsCurrent255() {
        // diff = (255 - 0) & 0xFF = 255 -> NOT stale (255 >= 128)
        // 0 is "ahead" of 255 in the circular space.
        XCTAssertFalse(isStale(frameGen: 0, currentGen: 255))
    }

    func testWrapAroundGenId128VsCurrent1() {
        // diff = (1 - 128) & 0xFF = (-127) & 0xFF = 129 -> NOT stale (129 >= 128)
        // 128 is in the "future" half relative to 1 (ambiguous edge = accept).
        XCTAssertFalse(isStale(frameGen: 128, currentGen: 1))
    }

    func testWrapAroundGenId127VsCurrent0() {
        // diff = (0 - 127) & 0xFF = 129 -> NOT stale (129 >= 128)
        XCTAssertFalse(isStale(frameGen: 127, currentGen: 0))
    }

    func testWrapAroundGenId129VsCurrent1() {
        // diff = (1 - 129) & 0xFF = (-128) & 0xFF = 128 -> NOT stale (128 >= 128)
        XCTAssertFalse(isStale(frameGen: 129, currentGen: 1))
    }

    // MARK: - Boundary Cases

    func testBothZero() {
        // diff = 0 -> not stale
        XCTAssertFalse(isStale(frameGen: 0, currentGen: 0))
    }

    func testBoth255() {
        // diff = 0 -> not stale
        XCTAssertFalse(isStale(frameGen: 255, currentGen: 255))
    }

    func testDiffExactly127() {
        // diff = 127 -> stale (127 is in 1..<128)
        XCTAssertTrue(isStale(frameGen: 0, currentGen: 127))
    }

    func testDiffExactly128() {
        // diff = 128 -> NOT stale (128 is NOT in 1..<128)
        XCTAssertFalse(isStale(frameGen: 0, currentGen: 128))
    }

    // MARK: - Exhaustive Sanity Check

    func testIdentityIsNeverStale() {
        // For every possible gen_id value, same frameGen == currentGen is never stale.
        for i: UInt8 in 0...255 {
            XCTAssertFalse(isStale(frameGen: i, currentGen: i),
                           "frameGen=\(i) should not be stale when currentGen=\(i)")
        }
    }

    func testImmediatelyPriorIsAlwaysStale() {
        // For every possible gen_id value, one behind is always stale.
        for current: UInt8 in 0...255 {
            let prior = current &- 1
            XCTAssertTrue(isStale(frameGen: prior, currentGen: current),
                          "frameGen=\(prior) should be stale when currentGen=\(current)")
        }
    }

    // MARK: - Playback Engine Integration

    func testPlaybackEngineDiscardsStaleFrame() {
        let engine = AudioPlaybackEngine()
        engine.setCurrentGen(3)

        let staleHeader = AudioFrameHeader(msgType: MsgType.agentAudio.rawValue,
                                           speakerId: SpeakerId.ellen.rawValue,
                                           genId: 2, frameSeq: 0)
        let pcm = Data(repeating: 0, count: 3200)
        engine.receiveAudioFrame(header: staleHeader, pcm: pcm)

        // Frame should have been discarded.
        XCTAssertTrue(engine.frameQueues.isEmpty)
    }

    func testPlaybackEngineAcceptsCurrentGenFrame() {
        let engine = AudioPlaybackEngine()
        engine.setCurrentGen(3)
        engine.meetingQueueActive = true  // Use meeting mode to buffer instead of playing

        let header = AudioFrameHeader(msgType: MsgType.agentAudio.rawValue,
                                      speakerId: SpeakerId.ellen.rawValue,
                                      genId: 3, frameSeq: 0)
        let pcm = Data(repeating: 0, count: 3200)
        engine.receiveAudioFrame(header: header, pcm: pcm)

        // Frame should have been buffered.
        XCTAssertEqual(engine.frameQueues[SpeakerId.ellen.rawValue]?.count, 1)
    }

    func testPlaybackEngineAcceptsFutureGenFrame() {
        let engine = AudioPlaybackEngine()
        engine.setCurrentGen(3)
        engine.meetingQueueActive = true

        let header = AudioFrameHeader(msgType: MsgType.agentAudio.rawValue,
                                      speakerId: SpeakerId.ellen.rawValue,
                                      genId: 5, frameSeq: 0)
        let pcm = Data(repeating: 0, count: 3200)
        engine.receiveAudioFrame(header: header, pcm: pcm)

        XCTAssertEqual(engine.frameQueues[SpeakerId.ellen.rawValue]?.count, 1)
    }

    func testPlaybackEngineWrapAroundStaleFrame() {
        let engine = AudioPlaybackEngine()
        engine.setCurrentGen(1)

        // genId=254 when currentGen=1: diff = (1-254)&0xFF = 3 -> stale
        let header = AudioFrameHeader(msgType: MsgType.agentAudio.rawValue,
                                      speakerId: SpeakerId.ellen.rawValue,
                                      genId: 254, frameSeq: 0)
        let pcm = Data(repeating: 0, count: 3200)
        engine.receiveAudioFrame(header: header, pcm: pcm)

        XCTAssertTrue(engine.frameQueues.isEmpty)
    }

    // MARK: - Flush

    func testFlushUpdatesCurrentGen() {
        let engine = AudioPlaybackEngine()
        engine.flushAllAndStop(newGen: 7)
        XCTAssertEqual(engine.currentGen, 7)
    }

    func testFlushClearsAllQueues() {
        let engine = AudioPlaybackEngine()
        engine.setFrameQueues([
            0x01: [Data(repeating: 0, count: 100)],
            0x02: [Data(repeating: 0, count: 100), Data(repeating: 0, count: 100)]
        ])

        engine.flushAllAndStop(newGen: 5)

        XCTAssertTrue(engine.frameQueues.isEmpty)
    }

    func testFlushResetsMeetingState() {
        let engine = AudioPlaybackEngine()
        engine.meetingQueueActive = true
        engine.setMeetingOrder([0x01, 0x02])
        engine.setCurrentMeetingSpeaker(0x01)

        engine.flushAllAndStop(newGen: 5)

        XCTAssertFalse(engine.meetingQueueActive)
        XCTAssertTrue(engine.meetingOrder.isEmpty)
        XCTAssertNil(engine.currentMeetingSpeaker)
    }

    // MARK: - Meeting Mode

    func testCancelStreamRemovesFromMeetingOrder() {
        let engine = AudioPlaybackEngine()
        engine.meetingQueueActive = true
        engine.setMeetingOrder([0x01, 0x02, 0x03])

        engine.cancelStream(speakerId: 0x02)

        XCTAssertEqual(engine.meetingOrder, [0x01, 0x03])
    }

    func testCancelStreamClearsFrameQueue() {
        let engine = AudioPlaybackEngine()
        engine.setFrameQueues([0x02: [Data(repeating: 0, count: 100)]])

        engine.cancelStream(speakerId: 0x02)

        XCTAssertTrue(engine.frameQueues[0x02]?.isEmpty ?? true)
    }

    func testOnSpeakerFinishedAdvancesMeeting() {
        let engine = AudioPlaybackEngine()
        engine.meetingQueueActive = true
        engine.setMeetingOrder([0x01, 0x02])
        engine.setCurrentMeetingSpeaker(0x01)

        engine.onSpeakerFinished(speakerId: 0x01)

        XCTAssertNil(engine.currentMeetingSpeaker)
        XCTAssertEqual(engine.meetingOrder, [0x02])
    }

    func testOnSpeakerFinishedNonCurrentIsNoop() {
        let engine = AudioPlaybackEngine()
        engine.meetingQueueActive = true
        engine.setMeetingOrder([0x01, 0x02])
        engine.setCurrentMeetingSpeaker(0x01)

        engine.onSpeakerFinished(speakerId: 0x03)

        XCTAssertEqual(engine.currentMeetingSpeaker, 0x01)
        XCTAssertEqual(engine.meetingOrder, [0x01, 0x02])
    }

    func testMaybeStartNextDoesNothingWhenMeetingInactive() {
        let engine = AudioPlaybackEngine()
        engine.meetingQueueActive = false
        engine.setMeetingOrder([0x01])
        engine.setFrameQueues([0x01: [Data(repeating: 0, count: 100)]])

        engine.maybeStartNextMeetingSpeaker()

        XCTAssertNil(engine.currentMeetingSpeaker)
    }
}
