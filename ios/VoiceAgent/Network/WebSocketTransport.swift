import Foundation

// MARK: - Delegate Protocol

/// Delegate for WebSocket transport events.
protocol WebSocketTransportDelegate: AnyObject {
    /// A binary WebSocket frame was received (audio data with 4-byte header).
    func transportDidReceiveBinaryFrame(_ data: Data)
    /// A text WebSocket frame was received (JSON control/status message).
    func transportDidReceiveTextFrame(_ text: String)
    /// The server rejected the connection with 401 (token consumed/expired).
    /// The delegate should request a fresh session rather than retrying with backoff.
    func transportRequiresReauthentication()
    /// The transport disconnected due to a network error and is attempting reconnection.
    func transportDidDisconnect(error: Error?)
}

// MARK: - WebSocketTransport

/// Manages the WebSocket connection to the backend, handling binary and JSON frames,
/// exponential backoff reconnection, and 401 reauthentication.
final class WebSocketTransport: NSObject {

    weak var delegate: WebSocketTransportDelegate?

    /// The current WebSocket task.
    private var webSocketTask: URLSessionWebSocketTask?

    /// Exponential backoff reconnect delay, starting at 1 second.
    private(set) var reconnectDelay: TimeInterval = 1.0
    /// Maximum reconnect delay cap.
    private let maxReconnectDelay: TimeInterval = 30.0

    /// The URL session used for WebSocket connections.
    private let urlSession: URLSession

    /// The last server URL used for connection (for reconnection).
    private var lastServerURL: URL?
    /// The last auth token used (consumed after first connect; reauthentication gets a new one).
    private var lastToken: String?

    /// Whether the transport is intentionally disconnected (user-initiated stop).
    private var intentionalDisconnect = false

    // MARK: - Init

    override init() {
        urlSession = URLSession(configuration: .default)
        super.init()
    }

    // MARK: - Connection

    /// Open a WebSocket connection to the server with the given auth token.
    ///
    /// The token is appended as `?token=<token>` to the URL.
    func connect(token: String, serverURL: URL) {
        intentionalDisconnect = false
        lastServerURL = serverURL
        lastToken = token

        var components = URLComponents(url: serverURL, resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "token", value: token)]

        guard let url = components.url else { return }

        let request = URLRequest(url: url)
        webSocketTask = urlSession.webSocketTask(with: request)
        webSocketTask?.resume()
        receiveLoop()

        // Reset backoff on successful connect.
        reconnectDelay = 1.0
    }

    /// Gracefully disconnect the WebSocket.
    func disconnect() {
        intentionalDisconnect = true
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        webSocketTask = nil
    }

    // MARK: - Sending

    /// Send a raw PCM audio chunk as a binary WebSocket frame with a 4-byte header.
    ///
    /// Header: [0x01 (audio_chunk), 0x00 (user), 0x00 (gen_id unused), frameSeq]
    func sendAudioChunk(_ pcm: Data, frameSeq: UInt8) {
        var frame = Data(capacity: AudioFrameHeader.size + pcm.count)
        frame.append(contentsOf: [MsgType.audioChunk.rawValue, 0x00, 0x00, frameSeq])
        frame.append(pcm)
        let message = URLSessionWebSocketTask.Message.data(frame)
        webSocketTask?.send(message) { error in
            if let error {
                Log.error("Send binary error: \(error)", tag: "WebSocketTransport")
            }
        }
    }

    /// Send a JSON payload as a text WebSocket frame.
    func sendJSON(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else { return }
        webSocketTask?.send(.string(text)) { error in
            if let error {
                Log.error("Send text error: \(error)", tag: "WebSocketTransport")
            }
        }
    }

    /// Send a Codable message as a JSON text WebSocket frame.
    func sendCodable<T: Encodable>(_ message: T) {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        guard let data = try? encoder.encode(message),
              let text = String(data: data, encoding: .utf8) else { return }
        webSocketTask?.send(.string(text)) { error in
            if let error {
                Log.error("Send text error: \(error)", tag: "WebSocketTransport")
            }
        }
    }

    // MARK: - Receiving

    /// Start the receive loop for incoming WebSocket messages.
    private func receiveLoop() {
        webSocketTask?.receive { [weak self] result in
            guard let self else { return }

            switch result {
            case .success(let message):
                switch message {
                case .data(let data):
                    self.delegate?.transportDidReceiveBinaryFrame(data)
                case .string(let text):
                    self.delegate?.transportDidReceiveTextFrame(text)
                @unknown default:
                    break
                }
                // Continue receiving.
                self.receiveLoop()

            case .failure(let error):
                // Fix 2: Distinguish 401 (token consumed/expired) from network errors.
                // A 401 means the single-use token is dead. Retrying with backoff would
                // loop forever. Instead, request a fresh session.
                let nsError = error as NSError
                if nsError.domain == NSURLErrorDomain,
                   nsError.code == NSURLErrorUserAuthenticationRequired {
                    self.delegate?.transportRequiresReauthentication()
                } else {
                    self.handleDisconnect(error: error)
                }
            }
        }
    }

    // MARK: - Reconnection

    /// Handle a network disconnect with exponential backoff reconnection.
    func handleDisconnect(error: Error?) {
        guard !intentionalDisconnect else { return }

        delegate?.transportDidDisconnect(error: error)

        let delay = reconnectDelay
        reconnectDelay = min(reconnectDelay * 2, maxReconnectDelay)

        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.attemptReconnect()
        }
    }

    /// Attempt to reconnect using the last known server URL and token.
    private func attemptReconnect() {
        guard !intentionalDisconnect,
              let serverURL = lastServerURL,
              let token = lastToken else { return }
        connect(token: token, serverURL: serverURL)
    }
}
