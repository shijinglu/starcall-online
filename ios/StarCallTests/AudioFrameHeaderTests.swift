import XCTest
@testable import StarCallLib

final class AudioFrameHeaderTests: XCTestCase {

    // MARK: - Parsing

    func testParseValidHeader() {
        let data = Data([0x03, 0x01, 0x02, 0x05]) + Data(count: 100)
        let header = AudioFrameHeader(data: data)

        XCTAssertNotNil(header)
        XCTAssertEqual(header?.msgType, MsgType.agentAudio.rawValue)
        XCTAssertEqual(header?.speakerId, SpeakerId.ellen.rawValue)
        XCTAssertEqual(header?.genId, 0x02)
        XCTAssertEqual(header?.frameSeq, 0x05)
    }

    func testParseExactly4Bytes() {
        let data = Data([0x01, 0x00, 0x00, 0x00])
        let header = AudioFrameHeader(data: data)
        XCTAssertNotNil(header)
        XCTAssertEqual(header?.msgType, MsgType.audioChunk.rawValue)
    }

    func testParseReturnsNilForTooShortData() {
        let data = Data([0x01, 0x02])
        XCTAssertNil(AudioFrameHeader(data: data))
    }

    func testParseReturnsNilForEmptyData() {
        XCTAssertNil(AudioFrameHeader(data: Data()))
    }

    // MARK: - Encoding

    func testEncodeProducesCorrectHeader() {
        let header = AudioFrameHeader(msgType: 0x01, speakerId: 0x00, genId: 0x00, frameSeq: 0x05)
        let pcm = Data(repeating: 0xAB, count: 3200)
        let encoded = header.encode(pcm: pcm)

        XCTAssertEqual(encoded.count, 4 + 3200)
        XCTAssertEqual(encoded[0], 0x01)
        XCTAssertEqual(encoded[1], 0x00)
        XCTAssertEqual(encoded[2], 0x00)
        XCTAssertEqual(encoded[3], 0x05)
        XCTAssertEqual(encoded[4], 0xAB)
    }

    func testEncodeDecodeRoundTrip() {
        let original = AudioFrameHeader(msgType: 0x03, speakerId: 0x04, genId: 0xFF, frameSeq: 0x80)
        let pcm = Data(repeating: 0x42, count: 100)
        let encoded = original.encode(pcm: pcm)

        let decoded = AudioFrameHeader(data: encoded)
        XCTAssertNotNil(decoded)
        XCTAssertEqual(decoded?.msgType, original.msgType)
        XCTAssertEqual(decoded?.speakerId, original.speakerId)
        XCTAssertEqual(decoded?.genId, original.genId)
        XCTAssertEqual(decoded?.frameSeq, original.frameSeq)

        let decodedPcm = encoded.dropFirst(AudioFrameHeader.size)
        XCTAssertEqual(Data(decodedPcm), pcm)
    }

    func testEncodeWithEmptyPcm() {
        let header = AudioFrameHeader(msgType: 0x02, speakerId: 0x00, genId: 0x01, frameSeq: 0x00)
        let encoded = header.encode(pcm: Data())
        XCTAssertEqual(encoded.count, 4)
    }

    // MARK: - All MsgType values

    func testAllMsgTypes() {
        XCTAssertEqual(MsgType.audioChunk.rawValue, 0x01)
        XCTAssertEqual(MsgType.audioResponse.rawValue, 0x02)
        XCTAssertEqual(MsgType.agentAudio.rawValue, 0x03)
    }

    // MARK: - All SpeakerId values

    func testAllSpeakerIds() {
        XCTAssertEqual(SpeakerId.moderator.rawValue, 0x00)
        XCTAssertEqual(SpeakerId.ellen.rawValue, 0x01)
        XCTAssertEqual(SpeakerId.shijing.rawValue, 0x02)
        XCTAssertEqual(SpeakerId.eva.rawValue, 0x03)
        XCTAssertEqual(SpeakerId.ming.rawValue, 0x04)
    }

    // MARK: - Header Size

    func testHeaderSizeIs4() {
        XCTAssertEqual(AudioFrameHeader.size, 4)
    }

    // MARK: - Data slice safety

    func testParseFromDataSlice() {
        // Ensure parsing works from a Data slice (e.g., data.dropFirst(n))
        let fullData = Data([0xFF, 0xFF, 0x03, 0x02, 0x01, 0x00]) + Data(count: 100)
        let slice = fullData.dropFirst(2)  // startIndex != 0
        let header = AudioFrameHeader(data: Data(slice))
        XCTAssertNotNil(header)
        XCTAssertEqual(header?.msgType, 0x03)
        XCTAssertEqual(header?.speakerId, 0x02)
        XCTAssertEqual(header?.genId, 0x01)
        XCTAssertEqual(header?.frameSeq, 0x00)
    }
}
