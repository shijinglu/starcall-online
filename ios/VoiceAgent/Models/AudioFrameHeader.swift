import Foundation

/// Message types for binary audio frames on the wire.
enum MsgType: UInt8 {
    case audioChunk    = 0x01  // Client -> Server
    case audioResponse = 0x02  // Server -> Client (Moderator)
    case agentAudio    = 0x03  // Server -> Client (Deep agent)
}

/// Speaker IDs matching the backend agent registry.
enum SpeakerId: UInt8 {
    case moderator = 0x00
    case ellen     = 0x01
    case shijing   = 0x02
    case eva       = 0x03
    case ming      = 0x04
}

/// 4-byte binary header for all audio WebSocket frames.
///
/// Layout:
/// ```
/// Byte 0: msg_type   - identifies the frame kind
/// Byte 1: speaker_id - who is speaking
/// Byte 2: gen_id     - generation counter (zombie-audio prevention)
/// Byte 3: frame_seq  - monotonic sequence within a generation (wraps 0-255)
/// Bytes 4+: raw PCM  - 16 kHz, int16, little-endian
/// ```
struct AudioFrameHeader {
    let msgType: UInt8
    let speakerId: UInt8
    let genId: UInt8
    let frameSeq: UInt8

    static let size = 4

    /// Parse header from the first 4 bytes of a binary WebSocket frame.
    /// Returns nil if data is too short.
    init?(data: Data) {
        guard data.count >= Self.size else { return nil }
        msgType   = data[data.startIndex]
        speakerId = data[data.startIndex + 1]
        genId     = data[data.startIndex + 2]
        frameSeq  = data[data.startIndex + 3]
    }

    /// Construct a header from individual fields.
    init(msgType: UInt8, speakerId: UInt8, genId: UInt8, frameSeq: UInt8) {
        self.msgType   = msgType
        self.speakerId = speakerId
        self.genId     = genId
        self.frameSeq  = frameSeq
    }

    /// Encode this header + PCM payload into a single Data suitable for a binary WS frame.
    func encode(pcm: Data) -> Data {
        var frame = Data(capacity: Self.size + pcm.count)
        frame.append(contentsOf: [msgType, speakerId, genId, frameSeq])
        frame.append(pcm)
        return frame
    }
}

// MARK: - gen_id Staleness Check (RFC 1982 Modular Arithmetic)

/// Returns true if `frameGen` is in the "past" half of the circular 0-255 space
/// relative to `currentGen`.
///
/// This uses RFC 1982 serial number arithmetic to correctly handle the 255->0 wrap.
/// A naive `frameGen < currentGen` comparison is broken at wrap boundaries.
///
/// The circular space is divided into two halves of 128 values each:
/// - Stale (past) half: diff in 1..<128
/// - Fresh (future) half: diff in 128..<256 or diff == 0
func isStale(frameGen: UInt8, currentGen: UInt8) -> Bool {
    let diff = Int(currentGen &- frameGen) & 0xFF
    return diff > 0 && diff < 128
}
