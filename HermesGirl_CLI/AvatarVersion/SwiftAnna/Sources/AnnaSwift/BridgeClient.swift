import Foundation

struct BridgeConfig: Codable {
    var webModeEnabled: Bool
    var serverURL: String
    var username: String
    var locationInjectionEnabled: Bool
    var signedIn: Bool

    enum CodingKeys: String, CodingKey {
        case webModeEnabled = "web_mode_enabled"
        case serverURL = "server_url"
        case username
        case locationInjectionEnabled = "location_injection_enabled"
        case signedIn = "signed_in"
    }

    init(webModeEnabled: Bool, serverURL: String, username: String, locationInjectionEnabled: Bool, signedIn: Bool) {
        self.webModeEnabled = webModeEnabled
        self.serverURL = serverURL
        self.username = username
        self.locationInjectionEnabled = locationInjectionEnabled
        self.signedIn = signedIn
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        webModeEnabled = try container.decode(Bool.self, forKey: .webModeEnabled)
        serverURL = try container.decode(String.self, forKey: .serverURL)
        username = try container.decode(String.self, forKey: .username)
        locationInjectionEnabled = try container.decodeIfPresent(Bool.self, forKey: .locationInjectionEnabled) ?? false
        signedIn = try container.decode(Bool.self, forKey: .signedIn)
    }
}

struct ChatMessage: Codable, Identifiable, Equatable {
    var id = UUID()
    var role: String
    var content: String

    enum CodingKeys: String, CodingKey {
        case role
        case content
    }
}

struct RemoteFile: Codable, Identifiable, Equatable, Hashable {
    var key: String
    var filename: String
    var status: String
    var summary: String?
    var indexStatus: String?
    var ragIndexed: Bool?
    var size: Int?
    var clientPath: String?
    var serverPath: String?
    var updatedAt: Double?
    var lastUploadedAt: Double?

    var id: String { key.isEmpty ? filename : key }

    enum CodingKeys: String, CodingKey {
        case key
        case filename
        case status
        case summary
        case indexStatus = "index_status"
        case ragIndexed = "rag_indexed"
        case size
        case clientPath = "client_path"
        case serverPath = "server_path"
        case updatedAt = "updated_at"
        case lastUploadedAt = "last_uploaded_at"
    }
}

struct RagSource: Codable, Identifiable, Equatable, Hashable {
    var source: String
    var chunkCount: Int?
    var summary: String?

    var id: String { source }

    enum CodingKeys: String, CodingKey {
        case source
        case chunkCount = "chunk_count"
        case summary
    }
}

struct RagChunk: Codable, Identifiable, Equatable {
    var id = UUID()
    var chunkIndex: Int?
    var text: String
    var summary: String?

    enum CodingKeys: String, CodingKey {
        case chunkIndex = "chunk_index"
        case text
        case summary
    }
}

final class BridgeClient {
    private var baseURL: URL { BridgeRuntime.baseURL }
    private let decoder = JSONDecoder()

    func config() async throws -> BridgeConfig {
        let json = try await get("config")
        return try decodeEnvelope(json, key: "config")
    }

    func saveConfig(webModeEnabled: Bool, serverURL: String, locationInjectionEnabled: Bool) async throws -> BridgeConfig {
        let json = try await post(
            "config",
            body: [
                "web_mode_enabled": webModeEnabled,
                "server_url": serverURL,
                "location_injection_enabled": locationInjectionEnabled,
            ]
        )
        return try decodeEnvelope(json, key: "config")
    }

    func login(serverURL: String, username: String, password: String) async throws -> BridgeConfig {
        let json = try await post(
            "auth/login",
            body: [
                "server_url": serverURL,
                "username": username,
                "password": password,
            ]
        )
        if let ok = json["ok"] as? Bool, !ok {
            throw BridgeError.message(json["error"] as? String ?? "Login failed")
        }
        return try await config()
    }

    func register(serverURL: String, username: String, password: String) async throws -> String {
        let json = try await post(
            "auth/register",
            body: [
                "server_url": serverURL,
                "username": username,
                "password": password,
            ]
        )
        if let ok = json["ok"] as? Bool, ok {
            return json["message"] as? String ?? "Registration sent. An admin must approve this account."
        }
        throw BridgeError.message(json["error"] as? String ?? "Registration failed")
    }

    func logout() async throws -> BridgeConfig {
        _ = try await post("auth/logout", body: [:])
        return try await config()
    }

    func health() async throws -> [String: Any] {
        try await get("health")
    }

    func startGateway() async throws -> [String: Any] {
        try await post("gateway/start", body: [:])
    }

    func send(messages: [ChatMessage]) async throws -> String {
        let payload = messages.map { ["role": $0.role, "content": $0.content] }
        let json = try await post("chat", body: ["messages": payload])
        if let ok = json["ok"] as? Bool, ok {
            return json["text"] as? String ?? ""
        }
        throw BridgeError.message(json["error"] as? String ?? "Chat failed")
    }

    func textEndpoint(_ name: String) async throws -> String {
        let json = try await get(name)
        if let ok = json["ok"] as? Bool, ok {
            return json["text"] as? String ?? ""
        }
        throw BridgeError.message(json["error"] as? String ?? "Could not load \(name)")
    }

    func files() async throws -> [RemoteFile] {
        let json = try await get("files")
        return try decodeEnvelope(json, key: "files")
    }

    func upload(url: URL) async throws -> RemoteFile {
        let didAccess = url.startAccessingSecurityScopedResource()
        defer {
            if didAccess {
                url.stopAccessingSecurityScopedResource()
            }
        }

        let data = try Data(contentsOf: url)
        let json = try await post(
            "files/upload-bytes",
            body: [
                "filename": url.lastPathComponent,
                "local_path": url.path,
                "data_base64": data.base64EncodedString(),
            ]
        )
        if let ok = json["ok"] as? Bool, !ok {
            throw BridgeError.message(json["error"] as? String ?? "Upload failed")
        }
        return try decodeEnvelope(json, key: "file")
    }

    func forgetFile(key: String) async throws {
        let json = try await post("files/forget", body: ["key": key])
        if let ok = json["ok"] as? Bool, !ok {
            throw BridgeError.message(json["error"] as? String ?? "Delete failed")
        }
    }

    func ragSources() async throws -> [RagSource] {
        let json = try await get("rag/sources")
        return try decodeEnvelope(json, key: "sources")
    }

    func ragChunks(source: String) async throws -> [RagChunk] {
        var components = URLComponents(url: baseURL.appendingPathComponent("rag/source"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "source", value: source)]
        let (data, response) = try await URLSession.shared.data(from: components.url!)
        try validate(response: response, data: data)
        let json = try parse(data)
        return try decodeEnvelope(json, key: "chunks")
    }

    func reindex(key: String = "") async throws {
        let json = try await post("rag/reindex", body: key.isEmpty ? [:] : ["key": key])
        if let ok = json["ok"] as? Bool, !ok {
            throw BridgeError.message(json["error"] as? String ?? "Reindex failed")
        }
    }

    private func get(_ endpoint: String) async throws -> [String: Any] {
        let request = URLRequest(url: baseURL.appendingPathComponent(endpoint))
        let (data, response) = try await data(for: request)
        try validate(response: response, data: data)
        return try parse(data)
    }

    private func post(_ endpoint: String, body: [String: Any]) async throws -> [String: Any] {
        var request = URLRequest(url: baseURL.appendingPathComponent(endpoint))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body, options: [])
        let (data, response) = try await data(for: request)
        try validate(response: response, data: data)
        return try parse(data)
    }

    private func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        var lastError: Error?
        for attempt in 0..<30 {
            do {
                return try await URLSession.shared.data(for: request)
            } catch {
                lastError = error
                if attempt == 29 {
                    break
                }
                try await Task.sleep(nanoseconds: 100_000_000)
            }
        }
        throw lastError ?? BridgeError.message("Bridge is not reachable")
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        if http.statusCode < 400 { return }
        let json = (try? parse(data)) ?? [:]
        throw BridgeError.message(json["error"] as? String ?? "HTTP \(http.statusCode)")
    }

    private func parse(_ data: Data) throws -> [String: Any] {
        let value = try JSONSerialization.jsonObject(with: data, options: [])
        guard let json = value as? [String: Any] else {
            throw BridgeError.message("Bridge returned invalid JSON")
        }
        return json
    }

    private func decodeEnvelope<T: Decodable>(_ json: [String: Any], key: String) throws -> T {
        guard let value = json[key] else {
            throw BridgeError.message("Bridge response is missing \(key)")
        }
        let data = try JSONSerialization.data(withJSONObject: value, options: [])
        return try decoder.decode(T.self, from: data)
    }
}

enum BridgeError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let text):
            return text
        }
    }
}
