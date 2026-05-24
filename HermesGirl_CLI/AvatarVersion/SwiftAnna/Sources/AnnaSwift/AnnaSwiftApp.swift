import AppKit
import SwiftUI

@main
struct AnnaSwiftApp: App {
    @NSApplicationDelegateAdaptor(AnnaAppDelegate.self) private var appDelegate
    @StateObject private var bridge = BridgeProcess()

    var body: some Scene {
        WindowGroup {
            AnnaRootView()
                .environmentObject(bridge)
                .task {
                    bridge.start()
                }
                .onDisappear {
                    bridge.stop()
                }
        }
        .windowStyle(.hiddenTitleBar)
    }
}

final class AnnaAppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        configureApplicationIcon()
        DispatchQueue.main.async {
            NSApp.activate(ignoringOtherApps: true)
            NSApp.windows.first?.makeKeyAndOrderFront(nil)
        }
    }
}

func configureApplicationIcon() {
    guard let icon = loadApplicationIcon() else { return }
    NSApp.applicationIconImage = icon
}

func loadApplicationIcon() -> NSImage? {
    let fileManager = FileManager.default
    let localCandidates = [
        repoRoot().appendingPathComponent("assets/icon1.icns"),
        repoRoot().appendingPathComponent("assets/icon.icns"),
        repoRoot().appendingPathComponent("Webversion/assets/icon.icns"),
        repoRoot().deletingLastPathComponent().appendingPathComponent("assets/icon1.icns"),
        repoRoot().deletingLastPathComponent().appendingPathComponent("assets/icon.icns")
    ]

    for url in localCandidates where fileManager.fileExists(atPath: url.path) {
        if let image = NSImage(contentsOf: url) {
            return image
        }
    }

    if let bundledIcon = Bundle.module.url(forResource: "icon", withExtension: "icns") {
        return NSImage(contentsOf: bundledIcon)
    }

    return nil
}

struct AnnaRootView: View {
    @EnvironmentObject private var bridge: BridgeProcess
    @StateObject private var model = AnnaModel()

    var body: some View {
        rootContent
        .frame(minWidth: 1120, minHeight: 720)
        .background(AnnaTheme.background)
        .sheet(isPresented: $model.showingSettings) {
            SettingsView(model: model)
                .frame(width: 560, height: 420)
        }
        .sheet(isPresented: $model.showingFiles) {
            FileManagerView(model: model)
                .frame(width: 860, height: 600)
        }
        .sheet(isPresented: $model.showingSkills) {
            SkillsGridView(model: model)
                .frame(width: 860, height: 600)
        }
        .sheet(item: $model.textSheet) { sheet in
            TextSheet(title: sheet.title, text: sheet.text)
                .frame(width: 700, height: 520)
        }
        .overlay(alignment: .bottomLeading) {
            Text(bridge.status)
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
        }
        .task {
            await model.loadConfig()
            await model.refreshHealth()
        }
    }

    @ViewBuilder
    private var rootContent: some View {
        if !model.didLoadConfig {
            VStack(spacing: 12) {
                ProgressView()
                Text("Starting Anna...")
                    .foregroundStyle(.secondary)
            }
        } else if !model.config.signedIn {
            LoginGateView(model: model)
        } else {
            ZStack(alignment: .bottomTrailing) {
                HStack(spacing: 0) {
                    ChatColumn(model: model)
                    AvatarSidebar(model: model)
                }
                if model.uploadPanelVisible {
                    UploadPanel(model: model)
                        .padding(.trailing, 330)
                        .padding(.bottom, 74)
                } else if !model.uploads.isEmpty {
                    Button {
                        withAnimation(.spring(response: 0.28, dampingFraction: 0.85)) {
                            model.uploadPanelVisible = true
                        }
                    } label: {
                        Label("Uploads", systemImage: "icloud.and.arrow.up")
                    }
                    .buttonStyle(PillButtonStyle())
                    .padding(.trailing, 330)
                    .padding(.bottom, 74)
                }
            }
        }
    }
}

@MainActor
final class AnnaModel: ObservableObject {
    @Published var config = BridgeConfig(webModeEnabled: false, serverURL: "http://127.0.0.1:8765", username: "", locationInjectionEnabled: false, signedIn: false)
    @Published var didLoadConfig = false
    @Published var messages: [ChatMessage] = []
    @Published var draft = ""
    @Published var status = "Ready"
    @Published var avatarState = "idle"
    @Published var isSending = false
    @Published var showingSettings = false
    @Published var showingFiles = false
    @Published var showingSkills = false
    @Published var textSheet: TextSheetData?
    @Published var files: [RemoteFile] = []
    @Published var selectedFile: RemoteFile?
    @Published var ragSources: [RagSource] = []
    @Published var selectedRagSource: RagSource?
    @Published var ragChunks: [RagChunk] = []
    @Published var uploads: [UploadItem] = []
    @Published var uploadPanelVisible = false
    @Published var skills: [SkillInfo] = []

    let client = BridgeClient()

    func loadConfig() async {
        do {
            config = try await client.config()
        } catch {
            status = error.localizedDescription
        }
        didLoadConfig = true
    }

    func refreshHealth() async {
        do {
            let health = try await client.health()
            status = health["gateway_ready"] as? Bool == true ? "Hermes ready" : "Bridge ready"
        } catch {
            status = error.localizedDescription
            avatarState = "warning"
        }
    }

    func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }

        draft = ""
        isSending = true
        avatarState = "thinking"
        status = "Hermes is thinking..."
        messages.append(ChatMessage(role: "user", content: text))

        do {
            let reply = try await client.send(messages: messages)
            messages.append(ChatMessage(role: "assistant", content: reply))
            avatarState = "talking"
            status = "Done"
        } catch {
            messages.append(ChatMessage(role: "assistant", content: "Gateway error: \(error.localizedDescription)"))
            avatarState = "warning"
            status = error.localizedDescription
        }

        isSending = false
    }

    func showText(_ endpoint: String, title: String) async {
        status = "Loading \(title)..."
        do {
            let text = try await client.textEndpoint(endpoint)
            textSheet = TextSheetData(title: title, text: text)
            status = "Loaded \(title)"
        } catch {
            status = error.localizedDescription
            avatarState = "warning"
        }
    }

    func loadSkills() async {
        status = "Loading skills..."
        do {
            let text = try await client.textEndpoint("skills")
            skills = parseSkills(text)
            showingSkills = true
            status = skills.isEmpty ? "No skills found" : "Loaded \(skills.count) skills"
        } catch {
            status = error.localizedDescription
            avatarState = "warning"
        }
    }

    func login(serverURL: String, username: String, password: String) async -> String {
        do {
            config = try await client.login(serverURL: serverURL, username: username, password: password)
            status = "Signed in as \(config.username)"
            await refreshHealth()
            return "Login successful."
        } catch {
            avatarState = "warning"
            return error.localizedDescription
        }
    }

    func register(serverURL: String, username: String, password: String) async -> String {
        do {
            return try await client.register(serverURL: serverURL, username: username, password: password)
        } catch {
            avatarState = "warning"
            return error.localizedDescription
        }
    }

    func logout() async {
        do {
            config = try await client.logout()
            status = "Logged out"
        } catch {
            status = error.localizedDescription
            avatarState = "warning"
        }
    }

    func loadFilesAndRag() async {
        do {
            async let nextFiles = client.files()
            async let nextSources = client.ragSources()
            files = try await nextFiles.filter { $0.status != "forgotten" }
            ragSources = try await nextSources
            if let selectedFile {
                self.selectedFile = files.first(where: { $0.id == selectedFile.id }) ?? files.first
            } else {
                selectedFile = files.first
            }
            status = "Loaded files"
        } catch {
            status = error.localizedDescription
        }
    }

    func upload(urls: [URL]) async {
        guard !urls.isEmpty else { return }
        uploadPanelVisible = true

        for url in urls {
            let id = UUID()
            let name = url.lastPathComponent
            uploads.insert(UploadItem(id: id, filename: name, progress: 0.12, status: "Uploading"), at: 0)

            do {
                _ = try await client.upload(url: url)
                updateUpload(id: id, progress: 1.0, status: "Complete")
            } catch {
                updateUpload(id: id, progress: 0.0, status: "Failed: \(error.localizedDescription)")
                avatarState = "warning"
            }
        }

        await loadFilesAndRag()
    }

    func forget(file: RemoteFile) async {
        do {
            if selectedFile?.id == file.id {
                selectedFile = nil
            }
            try await client.forgetFile(key: file.key)
            await loadFilesAndRag()
        } catch {
            status = error.localizedDescription
            avatarState = "warning"
        }
    }

    func reindex(file: RemoteFile?) async {
        do {
            try await client.reindex(key: file?.key ?? "")
            await loadFilesAndRag()
        } catch {
            status = error.localizedDescription
            avatarState = "warning"
        }
    }

    func selectRagSource(_ source: RagSource) async {
        selectedRagSource = source
        do {
            ragChunks = try await client.ragChunks(source: source.source)
        } catch {
            status = error.localizedDescription
            avatarState = "warning"
        }
    }

    private func updateUpload(id: UUID, progress: Double, status: String) {
        guard let index = uploads.firstIndex(where: { $0.id == id }) else { return }
        uploads[index].progress = progress
        uploads[index].status = status
    }
}

struct UploadItem: Identifiable, Equatable {
    let id: UUID
    var filename: String
    var progress: Double
    var status: String
}

struct SkillInfo: Identifiable, Equatable, Hashable {
    let id = UUID()
    var name: String
    var category: String
    var source: String
    var trust: String
    var status: String
    var raw: String
}

struct LoginGateView: View {
    @ObservedObject var model: AnnaModel
    @State private var serverURL = ""
    @State private var username = ""
    @State private var password = ""
    @State private var note = ""
    @State private var isWorking = false

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [AnnaTheme.background, Color(red: 0.88, green: 0.94, blue: 0.94)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Anna")
                            .font(.system(size: 42, weight: .bold, design: .rounded))
                        Text("Sign in before entering the workspace.")
                            .font(.system(size: 15, design: .rounded))
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                TextField("Server URL", text: $serverURL)
                    .textFieldStyle(AnnaTextFieldStyle())
                TextField("Account", text: $username)
                    .textFieldStyle(AnnaTextFieldStyle())
                SecureField("Password", text: $password)
                    .textFieldStyle(AnnaTextFieldStyle())
                    .onSubmit {
                        Task { await login() }
                    }

                HStack {
                    Button("Login") {
                        Task { await login() }
                    }
                    .buttonStyle(PrimaryTextButtonStyle())
                    .disabled(isWorking)

                    Button("Register") {
                        Task { await register() }
                    }
                    .disabled(isWorking)
                }

                Text(note)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(minHeight: 18, alignment: .leading)
            }
            .frame(width: 430)
            .padding(28)
            .background(.white.opacity(0.88))
            .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            .shadow(color: .black.opacity(0.14), radius: 28, y: 16)
        }
        .onAppear {
            serverURL = model.config.serverURL
            username = model.config.username
        }
    }

    private func login() async {
        isWorking = true
        note = "Logging in..."
        note = await model.login(serverURL: serverURL, username: username, password: password)
        isWorking = false
    }

    private func register() async {
        isWorking = true
        note = "Sending registration request..."
        note = await model.register(serverURL: serverURL, username: username, password: password)
        isWorking = false
    }
}

struct ChatColumn: View {
    @ObservedObject var model: AnnaModel

    var body: some View {
        VStack(spacing: 0) {
            HeaderBar(model: model)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(model.messages) { message in
                            MessageBubble(message: message)
                                .id(message.id)
                        }
                    }
                    .padding(24)
                }
                .onChange(of: model.messages) { messages in
                    guard let last = messages.last else { return }
                    withAnimation(.easeOut(duration: 0.25)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }

            Composer(model: model)
        }
    }
}

struct HeaderBar: View {
    @ObservedObject var model: AnnaModel

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text("Anna")
                    .font(.system(size: 24, weight: .bold, design: .rounded))
                Text(model.config.webModeEnabled ? "Remote Hermes server" : "Local Hermes gateway")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(model.status)
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 18)
        .background(.white.opacity(0.86))
    }
}

struct MessageBubble: View {
    let message: ChatMessage

    var isUser: Bool {
        message.role == "user"
    }

    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 120) }
            MarkdownText(message.content)
                    .font(.system(size: 14, design: .rounded))
                    .foregroundStyle(isUser ? .white : AnnaTheme.ink)
                    .textSelection(.enabled)
                    .padding(.horizontal, 15)
                    .padding(.vertical, 11)
                    .background(isUser ? AnnaTheme.accent : Color.white)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .shadow(color: .black.opacity(isUser ? 0.10 : 0.06), radius: 16, y: 8)
            if !isUser { Spacer(minLength: 120) }
        }
    }
}

struct MarkdownText: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        if let attributed = try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
            Text(attributed)
        } else {
            Text(text)
        }
    }
}

struct Composer: View {
    @ObservedObject var model: AnnaModel

    var body: some View {
        HStack(spacing: 10) {
            Button {
                chooseAndUploadFiles(model: model)
            } label: {
                Image(systemName: "plus")
            }
            .buttonStyle(IconButtonStyle())
            .accessibilityLabel("Upload files")

            TextField("Message Hermes...", text: $model.draft, axis: .vertical)
                .font(.system(size: 14, design: .rounded))
                .lineLimit(1...5)
                .textFieldStyle(.plain)
                .padding(12)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(Color.black.opacity(0.08))
                )
                .onSubmit {
                    Task { await model.send() }
                }

            Button {
                Task { await model.send() }
            } label: {
                Image(systemName: model.isSending ? "hourglass" : "paperplane.fill")
            }
            .buttonStyle(IconButtonStyle(isPrimary: true))
            .disabled(model.isSending)
        }
        .padding(18)
        .background(.white.opacity(0.72))
    }
}

struct AvatarSidebar: View {
    @ObservedObject var model: AnnaModel

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            AvatarImage(state: model.avatarState)
                .frame(width: 230, height: 230)
                .padding(.bottom, 8)
            Text(model.config.signedIn ? model.config.username : "Anna")
                .font(.system(size: 22, weight: .bold, design: .rounded))
            Text(model.config.signedIn ? "Signed in" : "Companion mode")
                .font(.caption)
                .foregroundStyle(.secondary)

            VStack(spacing: 10) {
                SidebarButton(title: "Files", icon: "folder") {
                    model.showingFiles = true
                    Task { await model.loadFilesAndRag() }
                }
                SidebarButton(title: "Memory", icon: "brain") {
                    Task { await model.showText("memory", title: "Memory") }
                }
                SidebarButton(title: "Skills", icon: "sparkles") {
                    Task { await model.loadSkills() }
                }
                SidebarButton(title: "Settings", icon: "gearshape") {
                    model.showingSettings = true
                }
            }
            .padding(.top, 14)

            Spacer()
        }
        .frame(width: 310)
        .background(
            LinearGradient(
                colors: [Color(red: 0.95, green: 0.97, blue: 0.98), Color(red: 0.86, green: 0.92, blue: 0.92)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .overlay(alignment: .leading) {
            Rectangle().fill(Color.black.opacity(0.07)).frame(width: 1)
        }
    }
}

struct SidebarButton: View {
    let title: String
    let icon: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack {
                Image(systemName: icon)
                    .frame(width: 22)
                Text(title)
                Spacer()
            }
            .font(.system(size: 14, weight: .semibold, design: .rounded))
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .frame(width: 210, alignment: .leading)
            .background(Color.white.opacity(0.82))
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .shadow(color: .black.opacity(0.06), radius: 12, y: 6)
        }
        .buttonStyle(.plain)
    }
}

struct AvatarImage: View {
    let state: String

    var body: some View {
        if let image = loadAvatar(state: state) {
            Image(nsImage: image)
                .resizable()
                .scaledToFit()
        } else {
            Image(systemName: "face.smiling")
                .resizable()
                .scaledToFit()
                .foregroundStyle(AnnaTheme.accent)
                .padding(44)
        }
    }
}

struct SettingsView: View {
    @ObservedObject var model: AnnaModel
    @State private var serverURL = ""
    @State private var webMode = false
    @State private var locationInjection = false
    @State private var note = ""
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("Hermes Settings")
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                Spacer()
                WindowCloseButton {
                    dismiss()
                }
            }

            Toggle("Use a remote Hermes server", isOn: $webMode)
                .toggleStyle(.switch)

            TextField("Server URL", text: $serverURL)
                .textFieldStyle(AnnaTextFieldStyle())

            Toggle("Share my current location with Hermes", isOn: $locationInjection)
                .toggleStyle(.switch)

            Text("Location is sent silently with chat requests when enabled.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Divider()

            Text(model.config.signedIn ? "Signed in as \(model.config.username)" : "Not signed in")
                .font(.callout)
                .foregroundStyle(.secondary)

            HStack {
                Button("Save") {
                    Task { await save() }
                }
                Spacer()
                Button("Logout") {
                    Task { await logout() }
                }
                .disabled(!model.config.signedIn)
            }

            Text(note)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(24)
        .background(AnnaTheme.background)
        .onAppear {
            serverURL = model.config.serverURL
            webMode = model.config.webModeEnabled
            locationInjection = model.config.locationInjectionEnabled
        }
    }

    private func save() async {
        do {
            model.config = try await model.client.saveConfig(
                webModeEnabled: webMode,
                serverURL: serverURL,
                locationInjectionEnabled: locationInjection
            )
            note = "Saved."
        } catch {
            note = error.localizedDescription
        }
    }

    private func logout() async {
        await model.logout()
        note = "Logged out."
        dismiss()
    }
}

struct FileManagerView: View {
    @ObservedObject var model: AnnaModel
    @Environment(\.dismiss) private var dismiss
    @State private var selectedTab = 0

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Files")
                        .font(.system(size: 22, weight: .bold, design: .rounded))
                    Text("Uploaded files and Original RAG sources for this account.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                WindowCloseButton {
                    dismiss()
                }
            }
            .padding(22)

            Picker("", selection: $selectedTab) {
                Text("Uploaded").tag(0)
                Text("Original RAG").tag(1)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 22)
            .padding(.bottom, 14)

            if selectedTab == 0 {
                UploadedFilesView(model: model)
            } else {
                RagSourceBrowserView(model: model)
            }
        }
        .background(AnnaTheme.background)
        .task {
            await model.loadFilesAndRag()
        }
    }
}

struct SkillsGridView: View {
    @ObservedObject var model: AnnaModel
    @Environment(\.dismiss) private var dismiss

    private let columns = [
        GridItem(.adaptive(minimum: 152), spacing: 12)
    ]

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Skills")
                        .font(.system(size: 22, weight: .bold, design: .rounded))
                    Text("\(model.skills.count) available Hermes skill\(model.skills.count == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                WindowCloseButton {
                    dismiss()
                }
            }
            .padding(22)

            ScrollView {
                LazyVGrid(columns: columns, spacing: 12) {
                    ForEach(model.skills) { skill in
                        SkillCard(skill: skill) {
                            openSkillDetailWindow(skill)
                        }
                    }
                }
                .padding(.horizontal, 22)
                .padding(.bottom, 22)
            }
        }
        .background(AnnaTheme.background)
    }
}

struct SkillCard: View {
    let skill: SkillInfo
    let action: () -> Void
    @State private var isHovered = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 9) {
                HStack {
                    Image(systemName: iconName(for: skill))
                        .font(.system(size: 18, weight: .bold))
                        .foregroundStyle(AnnaTheme.accent)
                    Spacer()
                    if !skill.status.isEmpty {
                        Circle()
                            .fill(statusColor(skill.status))
                            .frame(width: 9, height: 9)
                    }
                }

                Text(skill.name)
                    .font(.system(size: 14, weight: .bold, design: .rounded))
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)

                Text(skill.category.isEmpty ? "Skill" : skill.category)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                Spacer(minLength: 0)
            }
            .padding(13)
            .frame(height: 116)
            .background(Color.white.opacity(isHovered ? 1.0 : 0.84))
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(isHovered ? AnnaTheme.accent.opacity(0.65) : Color.black.opacity(0.06), lineWidth: isHovered ? 2 : 1)
            )
            .shadow(color: .black.opacity(isHovered ? 0.16 : 0.06), radius: isHovered ? 24 : 10, y: isHovered ? 12 : 5)
            .scaleEffect(isHovered ? 1.08 : 1.0, anchor: .center)
            .animation(.spring(response: 0.24, dampingFraction: 0.82), value: isHovered)
        }
        .buttonStyle(.plain)
        .zIndex(isHovered ? 20 : 0)
        .onHover { hovering in
            isHovered = hovering
        }
    }
}

struct SkillDetailRow: View {
    let label: String
    let value: String

    var body: some View {
        if !value.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .textSelection(.enabled)
            }
        }
    }
}

struct SkillDetailWindowView: View {
    let skill: SkillInfo

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 16) {
                Image(systemName: iconName(for: skill))
                    .font(.system(size: 30, weight: .bold))
                    .foregroundStyle(AnnaTheme.accent)
                    .frame(width: 58, height: 58)
                    .background(Color.white)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .shadow(color: .black.opacity(0.08), radius: 14, y: 6)

                VStack(alignment: .leading, spacing: 5) {
                    Text(skill.name)
                        .font(.system(size: 24, weight: .bold, design: .rounded))
                        .textSelection(.enabled)
                    Text(skill.category.isEmpty ? "Hermes skill" : skill.category)
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .foregroundStyle(.secondary)
                }

                Spacer()
            }
            .padding(24)

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    DetailGrid(rows: [
                        ("Category", skill.category),
                        ("Source", skill.source),
                        ("Trust", skill.trust),
                        ("Status", skill.status)
                    ])

                    DetailSection(title: "Raw", text: skill.raw.isEmpty ? "No raw skill data available." : skill.raw)
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(AnnaTheme.background)
    }
}

struct UploadedFilesView: View {
    @ObservedObject var model: AnnaModel

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Button {
                    chooseAndUploadFiles(model: model)
                } label: {
                    Label("Upload", systemImage: "plus")
                }
                Button {
                    Task { await model.reindex(file: nil) }
                } label: {
                    Label("Reindex All", systemImage: "arrow.clockwise")
                }
                Button {
                    Task { await model.loadFilesAndRag() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise.circle")
                }
                Spacer()
            }
            .padding(.horizontal, 22)

            HStack(spacing: 0) {
                List(model.files, selection: Binding(
                    get: { model.selectedFile },
                    set: { model.selectedFile = $0 }
                )) { file in
                    FileRow(file: file)
                        .tag(file)
                }
                .frame(width: 320)
                .scrollContentBackground(.hidden)

                Divider()

                FileDetailView(model: model, file: model.selectedFile)
            }
        }
    }
}

struct FileRow: View {
    let file: RemoteFile

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: file.ragIndexed == true ? "doc.text.magnifyingglass" : "doc")
                .foregroundStyle(AnnaTheme.accent)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(file.filename)
                    .font(.system(size: 14, weight: .semibold, design: .rounded))
                    .lineLimit(1)
                Text(file.indexStatus ?? file.status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 6)
    }
}

struct FileDetailView: View {
    @ObservedObject var model: AnnaModel
    let file: RemoteFile?

    var body: some View {
        Group {
            if let file {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(file.filename)
                                    .font(.system(size: 20, weight: .bold, design: .rounded))
                                Text(file.status.capitalized)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Image(systemName: file.ragIndexed == true ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                                .foregroundStyle(file.ragIndexed == true ? AnnaTheme.accent : Color.orange)
                                .font(.title2)
                        }

                        HStack {
                            Button("Reindex") {
                                Task { await model.reindex(file: file) }
                            }
                            Button("Delete") {
                                Task { await model.forget(file: file) }
                            }
                            Spacer()
                        }

                        DetailSection(title: "Indexing", text: file.indexStatus ?? "No indexing status.")
                        DetailSection(title: "Summary", text: cleanDetailText(file.summary) ?? "No summary available.")

                        DetailGrid(rows: [
                            ("Key", file.key),
                            ("Size", formattedSize(file.size)),
                            ("Client Path", file.clientPath ?? ""),
                            ("Server Path", file.serverPath ?? ""),
                            ("Updated", formattedDate(file.updatedAt)),
                            ("Uploaded", formattedDate(file.lastUploadedAt)),
                        ])
                    }
                    .padding(22)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                VStack(spacing: 10) {
                    Image(systemName: "doc.text")
                        .font(.largeTitle)
                        .foregroundStyle(.secondary)
                    Text("Select a file to view details.")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }
}

struct DetailSection: View {
    let title: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(text)
                .font(.system(size: 13, design: .rounded))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
    }
}

struct DetailGrid: View {
    let rows: [(String, String)]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(rows.filter { !$0.1.isEmpty }, id: \.0) { label, value in
                VStack(alignment: .leading, spacing: 4) {
                    Text(label)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(value)
                        .font(.system(size: 12, design: .monospaced))
                        .textSelection(.enabled)
                        .lineLimit(3)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

struct RagSourceBrowserView: View {
    @ObservedObject var model: AnnaModel

    var body: some View {
        HStack(spacing: 0) {
            ScrollView {
                LazyVStack(spacing: 8) {
                    ForEach(model.ragSources) { source in
                        Button {
                            Task { await model.selectRagSource(source) }
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(URL(fileURLWithPath: source.source).lastPathComponent)
                                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                                        .lineLimit(1)
                                    Text("\(source.chunkCount ?? 0) chunk(s)")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(model.selectedRagSource?.id == source.id ? Color.white : Color.white.opacity(0.62))
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(10)
            }
            .frame(width: 280)

            Divider()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(model.ragChunks) { chunk in
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Chunk \(chunk.chunkIndex ?? 0)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if let summary = chunk.summary, !summary.isEmpty {
                                Text(summary)
                                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                            }
                            Text(chunk.text)
                                .font(.system(size: 12, design: .monospaced))
                                .textSelection(.enabled)
                        }
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                }
                .padding(18)
            }
        }
    }
}

struct UploadPanel: View {
    @ObservedObject var model: AnnaModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Uploads", systemImage: "icloud.and.arrow.up")
                    .font(.system(size: 14, weight: .bold, design: .rounded))
                Spacer()
                Button {
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.85)) {
                        model.uploadPanelVisible = false
                    }
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(IconButtonStyle())
            }

            ForEach(model.uploads) { upload in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(upload.filename)
                            .font(.system(size: 13, weight: .semibold, design: .rounded))
                            .lineLimit(1)
                        Spacer()
                        Text(upload.status)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    ProgressView(value: upload.progress)
                }
            }
        }
        .padding(16)
        .frame(width: 360)
        .background(.white.opacity(0.94))
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: .black.opacity(0.16), radius: 24, y: 12)
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }
}

func chooseAndUploadFiles(model: AnnaModel) {
    let panel = NSOpenPanel()
    panel.allowsMultipleSelection = true
    panel.canChooseFiles = true
    panel.canChooseDirectories = false
    panel.begin { response in
        guard response == .OK else { return }
        let urls = panel.urls
        Task { @MainActor in
            await model.upload(urls: urls)
        }
    }
}

func openSkillDetailWindow(_ skill: SkillInfo) {
    let window = NSWindow(
        contentRect: NSRect(x: 0, y: 0, width: 500, height: 560),
        styleMask: [.titled, .closable, .resizable, .fullSizeContentView],
        backing: .buffered,
        defer: false
    )
    window.title = skill.name
    window.titleVisibility = .hidden
    window.titlebarAppearsTransparent = true
    window.isReleasedWhenClosed = false
    window.minSize = NSSize(width: 420, height: 420)
    window.contentView = NSHostingView(
        rootView: SkillDetailWindowView(skill: skill)
    )
    window.center()
    window.makeKeyAndOrderFront(nil)
    NSApp.activate(ignoringOtherApps: true)
}

func cleanDetailText(_ value: String?) -> String? {
    let text = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    return text.isEmpty ? nil : text
}

func formattedSize(_ bytes: Int?) -> String {
    guard let bytes else { return "" }
    return ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
}

func formattedDate(_ timestamp: Double?) -> String {
    guard let timestamp, timestamp > 0 else { return "" }
    let date = Date(timeIntervalSince1970: timestamp)
    return date.formatted(date: .abbreviated, time: .shortened)
}

func parseSkills(_ text: String) -> [SkillInfo] {
    var skills: [SkillInfo] = []
    for line in text.components(separatedBy: .newlines) {
        let cells = splitSkillTableRow(line)
        guard cells.count >= 2 else { continue }
        if isSkillHeader(cells) || isSkillSeparator(cells) { continue }

        let name = cells[0].trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { continue }
        skills.append(
            SkillInfo(
                name: name,
                category: cells.count > 1 ? cells[1] : "",
                source: cells.count > 2 ? cells[2] : "",
                trust: cells.count > 3 ? cells[3] : "",
                status: cells.count > 4 ? cells[4] : "",
                raw: line
            )
        )
    }

    return skills.isEmpty ? parseSkillCategoryFallback(text) : skills
}

func splitSkillTableRow(_ line: String) -> [String] {
    let stripped = line.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !stripped.isEmpty, !isBoxBorder(stripped) else { return [] }
    guard "|│┃".contains(stripped.first ?? " ") else { return [] }
    guard stripped.dropFirst().contains(where: { "|│┃".contains($0) }) else { return [] }

    let trimmed = stripped.trimmingCharacters(in: CharacterSet(charactersIn: " |│┃"))
    let cells = trimmed
        .split(whereSeparator: { "|│┃".contains($0) })
        .map { String($0).trimmingCharacters(in: CharacterSet(charactersIn: " \t┆┊╎╏")) }
    return cells.filter { !$0.isEmpty || cells.count >= 5 }
}

func isBoxBorder(_ line: String) -> Bool {
    let allowed = CharacterSet(charactersIn: "┏┓┗┛┡┩┬┴┳┻╇╈╋┌┐└┘├┤┼─━═╞╡╪╤╧╟╢╫ ")
    return !line.isEmpty && line.unicodeScalars.allSatisfy { allowed.contains($0) }
}

func isSkillHeader(_ cells: [String]) -> Bool {
    let lowered = cells.map { $0.lowercased() }
    return lowered.contains("name") && lowered.contains("status")
}

func isSkillSeparator(_ cells: [String]) -> Bool {
    let normalized = cells
        .joined()
        .replacingOccurrences(of: "─", with: "-")
        .replacingOccurrences(of: "━", with: "-")
        .replacingOccurrences(of: "—", with: "-")
        .replacingOccurrences(of: " ", with: "")
    return !normalized.isEmpty && normalized.allSatisfy { "-:".contains($0) }
}

func parseSkillCategoryFallback(_ text: String) -> [SkillInfo] {
    var skills: [SkillInfo] = []
    for line in text.components(separatedBy: .newlines) {
        let parts = line.split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { continue }

        let category = parts[0].trimmingCharacters(in: .whitespacesAndNewlines)
        guard !category.isEmpty, category.split(separator: " ").count <= 4 else { continue }

        for rawName in parts[1].split(separator: ",") {
            let name = rawName.trimmingCharacters(in: CharacterSet(charactersIn: " \t."))
            if !name.isEmpty {
                skills.append(
                    SkillInfo(
                        name: name,
                        category: category,
                        source: "",
                        trust: "",
                        status: "",
                        raw: line
                    )
                )
            }
        }
    }
    return skills
}

func iconName(for skill: SkillInfo) -> String {
    let text = "\(skill.name) \(skill.category)".lowercased()
    if text.contains("git") { return "point.3.connected.trianglepath.dotted" }
    if text.contains("file") || text.contains("doc") { return "doc.text" }
    if text.contains("web") || text.contains("search") { return "globe" }
    if text.contains("code") || text.contains("python") || text.contains("swift") { return "chevron.left.forwardslash.chevron.right" }
    if text.contains("memory") { return "brain" }
    return "sparkles"
}

func statusColor(_ status: String) -> Color {
    let lowered = status.lowercased()
    if lowered.contains("enabled") || lowered.contains("active") || lowered.contains("loaded") {
        return AnnaTheme.accent
    }
    if lowered.contains("error") || lowered.contains("fail") || lowered.contains("disabled") {
        return .red
    }
    return .orange
}

struct TextSheetData: Identifiable {
    let id = UUID()
    let title: String
    let text: String
}

struct TextSheet: View {
    let title: String
    let text: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(title)
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                Spacer()
                WindowCloseButton {
                    dismiss()
                }
            }
            ScrollView {
                Text(text)
                    .font(.system(size: 13, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(24)
        .background(AnnaTheme.background)
    }
}

struct IconButtonStyle: ButtonStyle {
    var isPrimary = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .bold, design: .rounded))
            .frame(width: 40, height: 40)
            .foregroundStyle(isPrimary ? .white : AnnaTheme.ink)
            .background(isPrimary ? AnnaTheme.accent : Color.white)
            .clipShape(Circle())
            .contentShape(Circle())
            .shadow(color: .black.opacity(configuration.isPressed ? 0.04 : 0.10), radius: 12, y: 6)
            .scaleEffect(configuration.isPressed ? 0.96 : 1.0)
    }
}

struct WindowCloseButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "xmark")
                .font(.system(size: 12, weight: .bold))
                .frame(width: 30, height: 30)
        }
        .buttonStyle(.plain)
        .foregroundStyle(AnnaTheme.ink)
        .background(Color.white.opacity(0.92))
        .clipShape(Circle())
        .contentShape(Circle())
        .shadow(color: .black.opacity(0.10), radius: 12, y: 6)
        .accessibilityLabel("Close")
    }
}

struct PrimaryTextButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .bold, design: .rounded))
            .foregroundStyle(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .background(AnnaTheme.accent)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .scaleEffect(configuration.isPressed ? 0.98 : 1.0)
    }
}

struct PillButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .bold, design: .rounded))
            .foregroundStyle(AnnaTheme.ink)
            .padding(.horizontal, 14)
            .padding(.vertical, 9)
            .background(Color.white.opacity(0.94))
            .clipShape(Capsule())
            .contentShape(Capsule())
            .shadow(color: .black.opacity(configuration.isPressed ? 0.05 : 0.13), radius: 14, y: 7)
    }
}

struct AnnaTextFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .textFieldStyle(.plain)
            .padding(10)
            .background(Color.white)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(Color.black.opacity(0.08))
            )
    }
}

enum AnnaTheme {
    static let background = Color(red: 0.97, green: 0.98, blue: 0.97)
    static let ink = Color(red: 0.12, green: 0.14, blue: 0.15)
    static let accent = Color(red: 0.05, green: 0.42, blue: 0.52)
}

func loadAvatar(state: String) -> NSImage? {
    let names = avatarNames(for: state)
    let roots = [
        repoRoot().appendingPathComponent("Webversion/assets/avatar"),
        repoRoot().appendingPathComponent("assets/avatar"),
    ]

    for root in roots {
        for name in names {
            let url = root.appendingPathComponent(name)
            if let image = NSImage(contentsOf: url) {
                return image
            }
        }
    }
    return nil
}

func avatarNames(for state: String) -> [String] {
    let normalized = state == "explain" ? "explaining" : state
    return [
        "\(normalized).png",
        "\(normalized).PNG",
        "idle.png",
        "idle.PNG",
    ]
}
