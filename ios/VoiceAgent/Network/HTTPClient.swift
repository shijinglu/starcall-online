import Foundation

/// HTTP client for REST API calls to the backend.
final class HTTPClient {

    /// Default server URL.
    static let defaultServerURL = URL(string: "http://localhost:8000")!

    private let urlSession: URLSession
    private let decoder: JSONDecoder

    init(urlSession: URLSession = .shared) {
        self.urlSession = urlSession
        self.decoder = JSONDecoder()
        self.decoder.keyDecodingStrategy = .convertFromSnakeCase
    }

    // MARK: - Session Management

    /// Create a new conversation session.
    ///
    /// - Parameter serverURL: Base URL of the backend server.
    /// - Returns: Tuple of (sessionId, authToken).
    /// - Throws: Network or decoding errors.
    func createSession(serverURL: URL = HTTPClient.defaultServerURL) async throws -> (sessionId: String, authToken: String) {
        let url = serverURL.appendingPathComponent("api/v1/sessions")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let (data, response) = try await urlSession.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw HTTPClientError.invalidResponse
        }

        guard httpResponse.statusCode == 200 else {
            throw HTTPClientError.httpError(statusCode: httpResponse.statusCode, body: String(data: data, encoding: .utf8))
        }

        let sessionResponse = try decoder.decode(CreateSessionResponse.self, from: data)
        return (sessionResponse.sessionId, sessionResponse.authToken)
    }

    /// Delete (terminate) an existing conversation session.
    ///
    /// - Parameters:
    ///   - sessionId: The session ID to terminate.
    ///   - serverURL: Base URL of the backend server.
    func deleteSession(sessionId: String, serverURL: URL = HTTPClient.defaultServerURL) async throws {
        let url = serverURL.appendingPathComponent("api/v1/sessions/\(sessionId)")
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"

        let (data, response) = try await urlSession.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw HTTPClientError.invalidResponse
        }

        guard httpResponse.statusCode == 200 || httpResponse.statusCode == 404 else {
            throw HTTPClientError.httpError(statusCode: httpResponse.statusCode, body: String(data: data, encoding: .utf8))
        }
    }

    // MARK: - Health & Agents

    /// Check server health.
    ///
    /// - Parameter serverURL: Base URL of the backend server.
    /// - Returns: Health response with status, version, and active session count.
    func getHealth(serverURL: URL = HTTPClient.defaultServerURL) async throws -> HealthResponse {
        let url = serverURL.appendingPathComponent("api/v1/health")
        let request = URLRequest(url: url)

        let (data, response) = try await urlSession.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw HTTPClientError.invalidResponse
        }

        return try decoder.decode(HealthResponse.self, from: data)
    }

    /// Fetch the list of available agents.
    ///
    /// - Parameter serverURL: Base URL of the backend server.
    /// - Returns: Array of agent info.
    func getAgents(serverURL: URL = HTTPClient.defaultServerURL) async throws -> [AgentInfo] {
        let url = serverURL.appendingPathComponent("api/v1/agents")
        let request = URLRequest(url: url)

        let (data, response) = try await urlSession.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw HTTPClientError.invalidResponse
        }

        let agentsResponse = try decoder.decode(AgentsResponse.self, from: data)
        return agentsResponse.agents
    }
}

// MARK: - Errors

enum HTTPClientError: LocalizedError {
    case invalidResponse
    case httpError(statusCode: Int, body: String?)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "Invalid response from server"
        case .httpError(let statusCode, let body):
            return "HTTP \(statusCode): \(body ?? "No body")"
        }
    }
}
