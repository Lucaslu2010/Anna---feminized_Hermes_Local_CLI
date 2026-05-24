import Foundation

enum BridgeRuntime {
    static let port = Int.random(in: 18_000...24_000)
    static var baseURL: URL {
        URL(string: "http://127.0.0.1:\(port)/v1")!
    }
}

@MainActor
final class BridgeProcess: ObservableObject {
    @Published var status = "Starting Python bridge..."

    private var process: Process?

    func start() {
        guard process == nil else { return }

        let root = repoRoot()
        let script = root.appendingPathComponent("Webversion/swift_bridge.py")
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3", script.path]
        proc.currentDirectoryURL = root
        proc.environment = ProcessInfo.processInfo.environment.merging(
            ["ANNA_SWIFT_BRIDGE_PORT": String(BridgeRuntime.port)]
        ) { _, new in new }

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe

        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor in
                self?.status = text.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }

        do {
            try proc.run()
            process = proc
        } catch {
            status = "Could not start Python bridge: \(error.localizedDescription)"
        }
    }

    func stop() {
        process?.terminate()
        process = nil
    }
}

func repoRoot() -> URL {
    let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    let candidates = [
        cwd,
        cwd.deletingLastPathComponent(),
        cwd.deletingLastPathComponent().deletingLastPathComponent(),
    ]

    for candidate in candidates {
        let script = candidate.appendingPathComponent("Webversion/swift_bridge.py")
        if FileManager.default.fileExists(atPath: script.path) {
            return candidate
        }
    }

    return cwd
}
