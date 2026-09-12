import AppKit
import Foundation
import UserNotifications

struct MenubarNotification: Decodable {
    let code: String
    let title: String
    let body: String
}

struct PipelineStage: Decodable {
    let id: String
    let state: String
}

struct PipelineModel: Decodable {
    let active_index: Int?
    let event_id: String?
    let outcome: String?
    let stages: [PipelineStage]
}

struct RoomModel: Decodable {
    let chat_id: Int
    let selector: String
    let live: Bool
    let auto_reply: Bool
    let geeknews: Bool
    let level: String
    let codes: [String]
    let open_jobs: Int
    let sent: Int
    let skipped: Int
    let delivery_unknown: Int
    let geeknews_slots: [String]
    let pipeline: PipelineModel
}

struct RoomChoice {
    let chat_id: Int
    let title: String
    let live: Bool
    let level: String
    let pipeline: PipelineModel
    let codes: [String]
    let open_jobs: Int
    let sent: Int
    let skipped: Int
    let delivery_unknown: Int
    let geeknews_slots: [String]
}

struct AvailableChat: Decodable {
    let chat_id: Int
    let title: String
    let members: Int
    let chat_type: Int
    let catalog: Bool
    let live: Bool
    let auto_reply: Bool
    let geeknews: Bool

    func updating(catalog: Bool? = nil, autoReply: Bool? = nil, geeknews: Bool? = nil) -> AvailableChat {
        AvailableChat(
            chat_id: chat_id,
            title: title,
            members: members,
            chat_type: chat_type,
            catalog: catalog ?? self.catalog,
            live: live,
            auto_reply: autoReply ?? self.auto_reply,
            geeknews: geeknews ?? self.geeknews
        )
    }
}


struct DoctorCheck: Decodable {
    let code: String
    let level: String
    let title: String
    let detail: String
    let advice: String
    let heal: String
}

struct DoctorReport: Decodable {
    let ok: Bool
    let action: String
    let privacy: String
    let level: String
    let primary_code: String
    let healable: [String]
    let healed: [String]
    let checks: [DoctorCheck]
    let health: [String: String]?
}

struct JobRow: Decodable {
    let event_id: String
    let status: String
    let status_label: String
    let reason_label: String
    let category: String
    let error_class: String
    let when: String
    let attempt_no: Int
    let leftover: Bool
    let chat_id: Int
    let detail: String?
    let category_label: String?
    let can_skip: Bool?
    let can_ack: Bool?
}

struct JobReport: Decodable {
    let ok: Bool
    let action: String
    let privacy: String
    let status: String
    let title: String
    let count: Int
    let truncated: Bool
    let jobs: [JobRow]
}


struct VectorTopicStat: Decodable {
    let key: String
    let label: String
    let count: Int
}

struct VectorRow: Decodable {
    let id: Int
    let source: String
    let origin_label: String
    let chat: String
    let date: String
    let user_name: String
    let message: String
    let preview: String
    let editable: Bool
    let vector_dim: Int?
    let vector_preview: String?
    let kind: String?
    let topics: [String]?
    let topics_label: String?
    let row_key: String?
    let decision: String?
    let decision_label: String?
    let category: String?
    let category_label: String?
    let status: String?
    let status_label: String?
    let reply: String?
    let reason_label: String?
    let deletable: Bool?

    var kindValue: String {
        let value = (kind ?? "message").trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? "message" : value
    }

    var topicsText: String {
        (topics_label ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var canEdit: Bool {
        editable && kindValue != "topic" && kindValue != "profile" && kindValue != "reply" && kindValue != "reference"
    }

    var canDelete: Bool {
        if kindValue == "prompt" {
            return deletable ?? false
        }
        return kindValue == "message" || kindValue == "style" || kindValue == "reply"
    }
}

struct VectorReport: Decodable {
    let ok: Bool
    let action: String
    let privacy: String
    let chat: String
    let query: String
    let count: Int
    let total: Int
    let offset: Int?
    let limit: Int?
    let truncated: Bool
    let rows: [VectorRow]
    let id: Int?
    let memory: VectorMemory?
    let source: String?
    let topic: String?
    let topics: [VectorTopicStat]?
}

struct VectorMemory: Decodable {
    let ok: Bool
    let action: String?
    let privacy: String?
    let state: String
    let total: Int
    let style_total: Int?
    let last_date: String?
    let last_user: String?
    let sync_status: String?
    let source_updated_at: String?
    let age_seconds: Int?
    let db_mtime_unix: Int?

    var fingerprint: String {
        "\(state)|\(total)|\(style_total ?? 0)|\(last_date ?? "")|\(source_updated_at ?? "")|\(db_mtime_unix ?? 0)"
    }
}

struct ReplyModelItem: Decodable {
    let id: String
    let label: String
    let canonical: String?
}

struct ReplyModelProvider: Decodable {
    let id: String
    let label: String
    let models: [ReplyModelItem]
}

struct ReplyModelSelection: Decodable {
    let id: String
    let label: String
    let canonical: String?
    let provider: String?
    let source: String?
    let enabled: Bool?
}

struct ModelsReport: Decodable {
    let ok: Bool?
    let action: String?
    let privacy: String?
    let model: String?
    let label: String?
    let provider: String?
    let source: String?
    let providers: [ReplyModelProvider]?
    let warnings: [String]?
}

struct ReplyProviderPreset: Decodable {
    let id: String
    let name: String?
    let description: String?
    let aliases: [String]?
    let api_key_env: String?
    let needs_base_url: Bool?
    let models: [String]?
}

struct ProviderPresetsReport: Decodable {
    let ok: Bool?
    let action: String?
    let privacy: String?
    let source: String?
    let presets: [ReplyProviderPreset]?
    let warnings: [String]?
}

struct ProviderAddReport: Decodable {
    let ok: Bool?
    let action: String?
    let privacy: String?
    let reason: String?
    let provider: String?
    let preset: String?
    let models: [String]?
    let warnings: [String]?
}

struct ProviderOAuthItem: Decodable {
    let id: String
    let name: String?
}

struct ProviderOAuthReport: Decodable {
    let ok: Bool?
    let action: String?
    let privacy: String?
    let reason: String?
    let provider: String?
    let providers: [ProviderOAuthItem]?
    let warnings: [String]?
}

struct MenubarModel: Decodable {
    let schema_version: Int
    let privacy: String
    let level: String
    let primary_code: String
    let codes: [String]
    let menu_lines: [String]
    let notifications: [MenubarNotification]
    let watermark: String?
    let open_jobs: Int
    let sent: Int
    let skipped: Int
    let delivery_unknown: Int
    let geeknews_slots: [String]
    let geeknews_newest_id: Int?
    let skip_reasons: [String]
    let journal: [String]
    let log_lines: [String]?
    let log_summary: String?
    let log_display: [String]?
    let pipeline: PipelineModel?
    let rooms: [RoomModel]?
    let available_chats: [AvailableChat]?
    let health: [String: String]?
    let vector_memory: VectorMemory?
    let reply_model: ReplyModelSelection?
    let image_reply_model: ReplyModelSelection?
    let reply_model_providers: [ReplyModelProvider]?
}

// 라이브옵스 창 뷰 모델 (task 11). 각 필드는 코어의 ui_shell::*_view가 만든
// 쉬운말 문자열 그대로입니다. Swift는 이 문자열을 붙여서 그리기만 합니다.
struct DurabilityViewModel: Decodable {
    let auto_reply: [String]
    let geeknews: [String]
    let verdict: [String]
    let seed: String
}

struct CoverageViewModel: Decodable {
    let summary: String
    let status: String
    let rows: [String]
}

struct ImprovementViewModel: Decodable {
    // 코어의 ImprovementView가 만든 줄. 비어 있으면 empty 안내가 채워집니다.
    let rows: [String]
    let empty: String?
}

struct OnboardingViewModel: Decodable {
    let available: [String]
    let blocked: [String]
}

struct Config {
    var python: String = "/opt/homebrew/opt/python@3.11/bin/python3.11"
    var script: String = ""
    var stateRoot: String = ""
    var logsDir: String = ""
    var expectedDigest: String = ""
    var rooms: [String] = []
    var bin: String = ""
    var interval: TimeInterval = 2.0
}

enum Palette {
    static func level(_ value: String) -> NSColor {
        switch value {
        case "green": return NSColor.systemGreen
        case "red": return NSColor.systemRed
        case "off": return NSColor.systemGray
        default: return NSColor.systemYellow
        }
    }

    static func lamp(_ value: String) -> NSColor {
        switch value {
        case "ok": return NSColor.systemGreen
        case "warn": return NSColor.systemYellow
        case "err": return NSColor.systemRed
        default: return NSColor.systemGray
        }
    }

    static func stage(_ value: String) -> NSColor {
        switch value {
        case "active": return NSColor.systemBlue
        case "done": return NSColor.systemGreen
        case "skipped": return NSColor.systemYellow
        case "failed": return NSColor.systemRed
        case "blocked": return NSColor.systemOrange
        case "idle": return NSColor.tertiaryLabelColor
        default: return NSColor.tertiaryLabelColor
        }
    }

    static func title(level: String) -> String {
        switch level {
        case "green": return "정상"
        case "yellow": return "처리 중"
        case "red": return "오류"
        case "off": return "꺼짐"
        default: return "대기"
        }
    }

    static func caption(code: String) -> String {
        switch code {
        case "ready": return "대기 완료"
        case "processing": return "파이프라인 동작"
        case "ax_window_missing": return "창 없음"
        case "leftover_occupancy": return "잔여 점유"
        case "worker_unhealthy": return "워커 이상"
        case "supervisor_unhealthy": return "감독 이상"
        case "watchdog_unhealthy": return "감시 이상"
        case "fenced": return "차단됨"
        case "delivery_unknown": return "전송 미확인"
        case "bake_digest_mismatch": return "런타임 불일치"
        case "snapshot_unavailable": return "스냅샷 없음"
        case "model_temporarily_unavailable": return "모델 대기"
        case "auto_reply_off": return "자동답변 꺼짐"
        case "service_off": return "서비스 꺼짐"
        case "journal_error": return "저널 오류"
        case "identity_mismatch": return "신원 불일치"
        case "circuit_open": return "감시 회로"
        case "preflight_failed": return "시작 전 점검"
        case "stopped_unclean": return "감독 종료"
        case "db_watch_exited": return "대화 감시 중단"
        case "python_pin_missing": return "파이썬 없음"
        case "kakaotalk_stopped": return "카카오톡 꺼짐"
        case "launch_agent_missing": return "모니터 없음"
        case "scheduled_waiting": return "예약 대기"
        case "session_not_ready": return "세션 미준비"
        case "session_unenrolled": return "세션 미등록"
        case "session_bake_stale": return "런타임 구버전"
        case "session_bake_current": return "런타임 최신"
        case "reply_model_default": return "기본 모델"
        default: return code
        }
    }
}

enum Chrome {
    static func operatorWindow(title: String, size: NSSize, autosave: String) -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = title
        window.isReleasedWhenClosed = false
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.minSize = NSSize(width: min(560, size.width), height: min(380, size.height))
        window.setFrameAutosaveName(autosave)
        window.titlebarSeparatorStyle = .line
        window.center()
        return window
    }

    static func label(
        _ text: String,
        size: CGFloat,
        weight: NSFont.Weight = .regular,
        color: NSColor = .labelColor,
        lines: Int = 2
    ) -> NSTextField {
        let field = NSTextField(labelWithString: text)
        field.font = NSFont.systemFont(ofSize: size, weight: weight)
        field.textColor = color
        field.translatesAutoresizingMaskIntoConstraints = false
        field.maximumNumberOfLines = lines
        field.cell?.wraps = true
        field.lineBreakMode = .byWordWrapping
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return field
    }

    static func hint(_ text: String) -> NSTextField {
        label(text, size: 11, color: .secondaryLabelColor)
    }

    static func summary(_ text: String) -> NSTextField {
        label(text, size: 15, weight: .semibold, lines: 1)
    }

    static func roundedButton(_ title: String, target: AnyObject, action: Selector) -> NSButton {
        let button = NSButton(title: title, target: target, action: action)
        button.bezelStyle = .rounded
        button.translatesAutoresizingMaskIntoConstraints = false
        button.setContentHuggingPriority(.required, for: .horizontal)
        return button
    }

    static func searchField(
        placeholder: String,
        target: AnyObject?,
        action: Selector?,
        delegate: NSTextFieldDelegate?,
        immediate: Bool = true
    ) -> NSSearchField {
        let field = NSSearchField()
        field.placeholderString = placeholder
        field.translatesAutoresizingMaskIntoConstraints = false
        field.delegate = delegate as? NSSearchFieldDelegate
        field.target = target
        field.action = action
        field.sendsSearchStringImmediately = immediate
        field.sendsWholeSearchString = immediate == false
        return field
    }

    static func table() -> (NSScrollView, NSTableView) {
        let scroll = NSScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .noBorder
        scroll.drawsBackground = false
        scroll.hasHorizontalScroller = false
        let table = NSTableView()
        table.rowHeight = 32
        table.usesAlternatingRowBackgroundColors = true
        table.allowsMultipleSelection = false
        table.allowsEmptySelection = true
        table.gridStyleMask = []
        table.intercellSpacing = NSSize(width: 8, height: 2)
        table.headerView = NSTableHeaderView()
        table.columnAutoresizingStyle = .uniformColumnAutoresizingStyle
        if #available(macOS 11.0, *) {
            table.style = .inset
        }
        scroll.documentView = table
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)
        scroll.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        return (scroll, table)
    }

    static func addColumn(
        _ table: NSTableView,
        id: String,
        title: String,
        width: CGFloat,
        minWidth: CGFloat = 48,
        alignment: NSTextAlignment = .center
    ) {
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(id))
        column.title = title
        column.width = width
        column.minWidth = minWidth
        column.resizingMask = [.autoresizingMask, .userResizingMask]
        column.headerCell.alignment = alignment
        if let cell = column.dataCell as? NSTextFieldCell {
            cell.alignment = alignment
        }
        table.addTableColumn(column)
    }

    static func fill(
        _ child: NSView,
        in parent: NSView,
        insets: NSEdgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
    ) {
        child.translatesAutoresizingMaskIntoConstraints = false
        if child.superview !== parent {
            parent.addSubview(child)
        }
        NSLayoutConstraint.activate([
            child.leadingAnchor.constraint(equalTo: parent.leadingAnchor, constant: insets.left),
            child.trailingAnchor.constraint(equalTo: parent.trailingAnchor, constant: -insets.right),
            child.topAnchor.constraint(equalTo: parent.topAnchor, constant: insets.top),
            child.bottomAnchor.constraint(equalTo: parent.bottomAnchor, constant: -insets.bottom),
        ])
    }

    static func hstack(_ views: [NSView], spacing: CGFloat = 8) -> NSStackView {
        let stack = NSStackView(views: views)
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = spacing
        stack.translatesAutoresizingMaskIntoConstraints = false
        return stack
    }

    static func vstack(_ views: [NSView], spacing: CGFloat = 10) -> NSStackView {
        let stack = NSStackView(views: views)
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.distribution = .fill
        stack.spacing = spacing
        stack.translatesAutoresizingMaskIntoConstraints = false
        return stack
    }

    static func spacer() -> NSView {
        let view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false
        view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return view
    }
}
func parseConfig(_ args: [String]) -> Config {
    var config = Config()
    var index = 0
    let argv = Array(args.dropFirst())
    while index < argv.count {
        let arg = argv[index]
        func take() -> String {
            index += 1
            return index < argv.count ? argv[index] : ""
        }
        switch arg {
        case "--python": config.python = take()
        case "--script": config.script = take()
        case "--state-root": config.stateRoot = take()
        case "--logs-dir": config.logsDir = take()
        case "--expected-command-sha256": config.expectedDigest = take()
        case "--room": config.rooms.append(take())
        case "--bin": config.bin = take()
        case "--interval":
            if let value = Double(take()), value >= 0.5, value <= 15 {
                config.interval = value
            }
        default:
            break
        }
        index += 1
    }
    if config.stateRoot.isEmpty {
        config.stateRoot = NSString(
            string: "~/Library/Application Support/openkakao/auto-reply"
        ).expandingTildeInPath
    }
    if config.logsDir.isEmpty {
        config.logsDir = NSString(
            string: "~/Library/Logs/AutoReplyMenu"
        ).expandingTildeInPath
    }
    if config.bin.isEmpty {
        // GUI launches (launchd, Finder, login items) do not inherit a PATH that
        // contains the bundled CLI. Without --bin the Python layer cannot list
        // KakaoTalk rooms, so 단체 채팅방 fell back to the catalog room alone with an
        // `id:<chat_id>` title. Default to the CLI shipped inside this bundle.
        let bundled = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/bin/openkakao-cli")
            .path
        if FileManager.default.isExecutableFile(atPath: bundled) {
            config.bin = bundled
        }
    }
    return config
}

final class PipelineView: NSView {
    var stages: [PipelineStage] = []
    var level: String = "yellow"

    static let labels: [(id: String, title: String)] = [
        ("detect", "수신"),
        ("authorize", "인가"),
        ("queue", "대기"),
        ("context", "문맥"),
        ("model", "생성"),
        ("delay", "지연"),
        ("send", "전송"),
        ("confirm", "확인"),
    ]

    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let bounds = self.bounds
        NSColor.clear.setFill()
        bounds.fill()
        let count = Self.labels.count
        guard count > 0 else { return }
        let pad: CGFloat = 18
        let usable = bounds.width - pad * 2
        let step = usable / CGFloat(max(count - 1, 1))
        let nodeY = bounds.height * 0.40
        let radius: CGFloat = 7
        var centers: [CGPoint] = []
        for index in 0..<count {
            centers.append(CGPoint(x: pad + CGFloat(index) * step, y: nodeY))
        }
        let states = Dictionary(uniqueKeysWithValues: stages.map { ($0.id, $0.state) })
        for index in 0..<(count - 1) {
            let start = centers[index]
            let end = centers[index + 1]
            let path = NSBezierPath()
            path.move(to: CGPoint(x: start.x + radius, y: start.y))
            path.line(to: CGPoint(x: end.x - radius, y: end.y))
            path.lineWidth = 2
            Palette.stage(states[Self.labels[index + 1].id] ?? "idle").withAlphaComponent(0.45).setStroke()
            path.stroke()
        }
        for (index, spec) in Self.labels.enumerated() {
            let state = states[spec.id] ?? "idle"
            let center = centers[index]
            let rect = NSRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
            Palette.stage(state).setFill()
            NSBezierPath(ovalIn: rect).fill()
            NSColor.white.withAlphaComponent(0.85).setStroke()
            let ring = NSBezierPath(ovalIn: rect.insetBy(dx: 0.4, dy: 0.4))
            ring.lineWidth = 1
            ring.stroke()
            let label = NSString(string: spec.title)
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 10, weight: .medium),
                .foregroundColor: NSColor.secondaryLabelColor,
            ]
            let size = label.size(withAttributes: attrs)
            label.draw(
                at: CGPoint(x: center.x - size.width / 2, y: center.y + radius + 5),
                withAttributes: attrs
            )
        }
    }
}

final class MenuPanelView: NSView {
    var model: MenubarModel {
        didSet { sync() }
    }
    let pipelineView = PipelineView(frame: .zero)
    weak var tileTarget: AnyObject?
    weak var hamburgerTarget: AnyObject?
    let roomButton = NSButton(title: "방", target: nil, action: #selector(AppDelegate.roomPickerClicked(_:)))
    var roomTitle = ""
    var selectedRoomId = 0
    static let panelWidth: CGFloat = 408
    static let panelBaseHeight: CGFloat = 228
    static let roomGridColumns = 2
    static let roomCellHeight: CGFloat = 28
    static let roomGridGap: CGFloat = 6
    static let roomGridTop: CGFloat = 94
    var roomsExpanded = false {
        didSet {
            guard roomsExpanded != oldValue else { return }
            rebuildRoomPopup()
            invalidateIntrinsicContentSize()
            needsLayout = true
        }
    }
    var roomRowButtons: [NSButton] = []
    var tileButtons: [NSButton] = []
    let autoButton = NSButton(title: "즉시 자동 답변", target: nil, action: #selector(AppDelegate.instantAutoReplyClicked))
    let geekButton = NSButton(title: "즉시 긱뉴스 전송", target: nil, action: #selector(AppDelegate.instantGeekNewsClicked))

    init(model: MenubarModel, frame: NSRect) {
        self.model = model
        super.init(frame: frame)
        pipelineView.frame = NSRect(x: 0, y: 26, width: frame.width, height: 54)
        pipelineView.autoresizingMask = [.width]
        pipelineView.stages = model.pipeline?.stages ?? []
        pipelineView.level = model.level
        addSubview(pipelineView)
        roomButton.bezelStyle = .inline
        roomButton.controlSize = .small
        roomButton.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        roomButton.image = NSImage(systemSymbolName: "chevron.down", accessibilityDescription: "방 고르기")
        roomButton.imagePosition = .imageTrailing
        roomButton.imageHugsTitle = true
        roomButton.toolTip = "지금 보고 있는 채팅방 워커를 고릅니다"
        roomButton.identifier = NSUserInterfaceItemIdentifier("room-popup")
        addSubview(roomButton)
        let kinds = AppDelegate.jobKinds
        let titles = ["대기", "전송", "건너뜀", "미확인"]
        for (index, kind) in kinds.enumerated() {
            let button = NSButton(title: "", target: nil, action: #selector(AppDelegate.tileClicked(_:)))
            button.bezelStyle = .regularSquare
            button.isBordered = false
            button.tag = index
            button.identifier = NSUserInterfaceItemIdentifier(kind)
            button.toolTip = "\(titles[index]) 목록 열기"
            addSubview(button)
            tileButtons.append(button)
        }
        styleAction(autoButton)
        styleAction(geekButton)
        autoButton.toolTip = "이 방의 예약된 자동 답변을 지금 보냅니다. 메뉴에서 직접 보내지는 않습니다."
        geekButton.toolTip = "이 방에 지금 긱뉴스를 보냅니다. 카카오톡 창이 열려 있어야 합니다."
        addSubview(autoButton)
        addSubview(geekButton)
        sync()
    }

    func styleAction(_ button: NSButton) {
        button.bezelStyle = .rounded
        button.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
        button.controlSize = .regular
    }

    static func roomGridExtra(count: Int, expanded: Bool) -> CGFloat {
        guard expanded else { return 0 }
        let rooms = max(count, 1)
        let rows = (rooms + roomGridColumns - 1) / roomGridColumns
        return 8 + CGFloat(rows) * roomCellHeight + CGFloat(max(rows - 1, 0)) * roomGridGap
    }

    func roomGridExtra() -> CGFloat {
        Self.roomGridExtra(count: AppDelegate.inspectableRooms(in: model).count, expanded: roomsExpanded)
    }

    func layoutWidth() -> CGFloat {
        max(bounds.width, Self.panelWidth)
    }

    override var intrinsicContentSize: NSSize {
        NSSize(width: Self.panelWidth, height: Self.panelBaseHeight + roomGridExtra())
    }

    func rebuildRoomPopup() {
        let rooms = AppDelegate.inspectableRooms(in: model)
        let title = roomTitle.isEmpty ? "방" : roomTitle
        let mark = roomsExpanded ? "▾" : "▸"
        roomButton.isHidden = rooms.isEmpty
        roomButton.isEnabled = !rooms.isEmpty
        roomButton.title = "\(mark) \(title)"
        roomButton.bezelStyle = .recessed
        roomButton.controlSize = .small
        roomButton.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        roomButton.image = nil
        roomButton.target = hamburgerTarget
        roomButton.action = #selector(AppDelegate.toggleRoomListClicked(_:))
        for button in roomRowButtons {
            button.removeFromSuperview()
        }
        roomRowButtons.removeAll()
        guard roomsExpanded else { return }
        for room in rooms {
            let selected = room.chat_id == selectedRoomId
            let button = NSButton(
                title: selected ? "✓ \(room.title)" : room.title,
                target: hamburgerTarget,
                action: #selector(AppDelegate.inspectRoomButtonClicked(_:))
            )
            button.bezelStyle = .inline
            button.isBordered = false
            button.controlSize = .small
            button.font = NSFont.systemFont(ofSize: 11, weight: selected ? .semibold : .medium)
            button.alignment = .center
            button.tag = room.chat_id
            button.toolTip = "이 방 워커를 봅니다"
            button.contentTintColor = selected ? NSColor.controlAccentColor : NSColor.labelColor
            if let cell = button.cell as? NSButtonCell {
                cell.lineBreakMode = .byTruncatingTail
            }
            button.isHidden = true
            addSubview(button)
            roomRowButtons.append(button)
        }
        layoutRoomGrid()
        for button in roomRowButtons {
            button.isHidden = false
        }
    }

    func layoutRoomGrid() {
        let extra = roomGridExtra()
        let width = layoutWidth()
        roomButton.frame = NSRect(x: width - 116, y: 7, width: 100, height: 22)
        pipelineView.frame = NSRect(x: 8, y: 36, width: width - 16, height: 56)
        let columns = Self.roomGridColumns
        let gap = Self.roomGridGap
        let cellH = Self.roomCellHeight
        let cellW = max(80, (width - 32 - gap) / CGFloat(columns))
        for (index, button) in roomRowButtons.enumerated() {
            let col = index % columns
            let row = index / columns
            button.frame = NSRect(
                x: 16 + CGFloat(col) * (cellW + gap),
                y: Self.roomGridTop + CGFloat(row) * (cellH + gap),
                width: cellW,
                height: cellH
            )
        }
        let tileY: CGFloat = 98 + extra
        let actionY: CGFloat = 186 + extra
        let tileW = (width - 32 - 18) / 4
        for (index, button) in tileButtons.enumerated() {
            button.target = tileTarget
            button.frame = NSRect(x: 16 + CGFloat(index) * (tileW + 6), y: tileY, width: tileW, height: 52)
        }
        autoButton.target = tileTarget
        geekButton.target = tileTarget
        let buttonW = (width - 32 - 8) / 2
        autoButton.frame = NSRect(x: 16, y: actionY, width: buttonW, height: 28)
        geekButton.frame = NSRect(x: 16 + buttonW + 8, y: actionY, width: buttonW, height: 28)
    }

    func sync() {
        let room = AppDelegate.selectedRoom(in: model, preferred: selectedRoomId)
        selectedRoomId = room?.chat_id ?? 0
        roomTitle = room?.title ?? "전체"
        pipelineView.stages = room?.pipeline.stages ?? model.pipeline?.stages ?? []
        pipelineView.level = room?.level ?? model.level
        pipelineView.needsDisplay = true
        rebuildRoomPopup()
        let selectedLive = room.map { choice in
            (model.rooms ?? []).contains { $0.chat_id == choice.chat_id && $0.live && $0.auto_reply }
        } ?? false
        let selectedGeek = room.map { choice in
            (model.rooms ?? []).contains { $0.chat_id == choice.chat_id && $0.live && $0.geeknews }
        } ?? false
        autoButton.isEnabled = selectedLive
        geekButton.isEnabled = selectedGeek
        needsDisplay = true
    }

    required init?(coder: NSCoder) {
        return nil
    }

    override var isFlipped: Bool { true }

    override func layout() {
        super.layout()
        layoutRoomGrid()
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let bounds = self.bounds
        let width = layoutWidth()
        NSColor.clear.setFill()
        bounds.fill()

        let room = AppDelegate.selectedRoom(in: model, preferred: selectedRoomId)
        let level = room?.level ?? model.level
        let color = Palette.level(level)
        let titleAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
            .foregroundColor: color,
        ]
        let title = NSString(string: Palette.title(level: level))
        let titleSize = title.size(withAttributes: titleAttrs)
        let statusPill = NSRect(x: 16, y: 8, width: 24 + titleSize.width, height: 22)
        color.withAlphaComponent(0.16).setFill()
        NSBezierPath(roundedRect: statusPill, xRadius: 11, yRadius: 11).fill()
        color.setFill()
        NSBezierPath(ovalIn: NSRect(x: statusPill.minX + 6, y: statusPill.minY + 6, width: 10, height: 10)).fill()
        let titleY = statusPill.midY - titleSize.height / 2
        title.draw(
            at: CGPoint(x: statusPill.minX + 20, y: titleY),
            withAttributes: titleAttrs
        )
        let captionX = statusPill.maxX + 8
        let captionMax = max(40, width - 126 - captionX)
        let caption = NSString(string: "\(roomTitle) · \(Palette.caption(code: room?.codes.first ?? model.primary_code))")
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byTruncatingTail
        paragraph.alignment = .left
        let captionAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 13, weight: .semibold),
            .foregroundColor: NSColor.labelColor,
            .paragraphStyle: paragraph,
        ]
        let captionSize = caption.size(withAttributes: captionAttrs)
        let captionY = statusPill.midY - captionSize.height / 2
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(rect: NSRect(x: captionX, y: statusPill.minY, width: captionMax, height: statusPill.height)).addClip()
        caption.draw(
            at: CGPoint(x: captionX, y: captionY),
            withAttributes: captionAttrs
        )
        NSGraphicsContext.restoreGraphicsState()

        let extra = roomGridExtra()
        let tileY: CGFloat = 98 + extra
        let lampY: CGFloat = 158 + extra
        if roomsExpanded {
            let card = NSRect(x: 12, y: 90, width: width - 24, height: extra)
            NSColor.labelColor.withAlphaComponent(0.045).setFill()
            NSBezierPath(roundedRect: card, xRadius: 10, yRadius: 10).fill()
        }
        let metrics: [(String, Int, NSColor)] = [
            ("대기", room?.open_jobs ?? 0, (room?.open_jobs ?? 0) > 0 ? NSColor.systemBlue : NSColor.tertiaryLabelColor),
            ("전송", room?.sent ?? 0, NSColor.labelColor),
            ("건너뜀", room?.skipped ?? 0, NSColor.secondaryLabelColor),
            ("미확인", room?.delivery_unknown ?? 0, (room?.delivery_unknown ?? 0) > 0 ? NSColor.systemRed : NSColor.tertiaryLabelColor),
        ]
        let tileW = (width - 32 - 18) / 4
        for (index, metric) in metrics.enumerated() {
            let x = 16 + CGFloat(index) * (tileW + 6)
            let rect = NSRect(x: x, y: tileY, width: tileW, height: 52)
            NSColor.labelColor.withAlphaComponent(0.055).setFill()
            NSBezierPath(roundedRect: rect, xRadius: 10, yRadius: 10).fill()
            let value = NSString(string: Self.compact(metric.1))
            let valueAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.monospacedDigitSystemFont(ofSize: 18, weight: .semibold),
                .foregroundColor: metric.2,
            ]
            let valueSize = value.size(withAttributes: valueAttrs)
            value.draw(
                at: CGPoint(x: rect.midX - valueSize.width / 2, y: rect.minY + 8),
                withAttributes: valueAttrs
            )
            let name = NSString(string: metric.0)
            let nameAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 10, weight: .medium),
                .foregroundColor: NSColor.secondaryLabelColor,
            ]
            let nameSize = name.size(withAttributes: nameAttrs)
            name.draw(
                at: CGPoint(x: rect.midX - nameSize.width / 2, y: rect.minY + 32),
                withAttributes: nameAttrs
            )
        }

        let health = model.health ?? [:]
        let lamps: [(String, String)] = [
            ("감시", health["watchdog"] ?? "off"),
            ("감독", health["supervisor"] ?? "off"),
            ("창", health["ax"] ?? "off"),
            ("워커", health["worker"] ?? "off"),
            ("모델", health["model"] ?? "off"),
        ]
        var x: CGFloat = 16
        for lamp in lamps {
            let label = NSString(string: lamp.0)
            let labelAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 10, weight: .medium),
                .foregroundColor: NSColor.secondaryLabelColor,
            ]
            let labelSize = label.size(withAttributes: labelAttrs)
            let chip = NSRect(x: x, y: lampY, width: 16 + labelSize.width + 8, height: 18)
            NSColor.labelColor.withAlphaComponent(0.05).setFill()
            NSBezierPath(roundedRect: chip, xRadius: 9, yRadius: 9).fill()
            Palette.lamp(lamp.1).setFill()
            NSBezierPath(ovalIn: NSRect(x: chip.minX + 5, y: chip.minY + 5, width: 8, height: 8)).fill()
            label.draw(at: CGPoint(x: chip.minX + 16, y: chip.minY + 2), withAttributes: labelAttrs)
            x = chip.maxX + 6
        }

        let posted = Set(room?.geeknews_slots ?? [])
        let day = Self.kstDay()
        let slots: [(String, String)] = [("아침", "morning"), ("점심", "lunch"), ("저녁", "evening")]
        var slotX = width - 16
        for slot in slots.reversed() {
            let filled = posted.contains("\(day):\(slot.1)")
            let label = NSString(string: slot.0)
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 10, weight: .semibold),
                .foregroundColor: filled ? NSColor.white : NSColor.secondaryLabelColor,
            ]
            let size = label.size(withAttributes: attrs)
            let pill = NSRect(x: slotX - size.width - 16, y: lampY, width: size.width + 14, height: 18)
            (filled ? NSColor.systemGreen : NSColor.labelColor.withAlphaComponent(0.08)).setFill()
            NSBezierPath(roundedRect: pill, xRadius: 8, yRadius: 8).fill()
            label.draw(at: CGPoint(x: pill.minX + 7, y: pill.minY + 2), withAttributes: attrs)
            slotX = pill.minX - 6
        }
    }

    static func compact(_ value: Int) -> String {
        if value >= 1000 {
            return String(format: "%.1fk", Double(value) / 1000.0)
        }
        return String(value)
    }

    static func kstDay() -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Seoul") ?? .current
        let parts = calendar.dateComponents([.year, .month, .day], from: Date())
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
}

final class MiniPipelineView: NSView {
    var stages: [PipelineStage] = []
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        NSColor.clear.setFill()
        bounds.fill()
        let count = PipelineView.labels.count
        guard count > 0 else { return }
        let states = Dictionary(uniqueKeysWithValues: stages.map { ($0.id, $0.state) })
        let pad: CGFloat = 2
        let usable = bounds.width - pad * 2
        let step = usable / CGFloat(count)
        for (index, spec) in PipelineView.labels.enumerated() {
            let x = pad + CGFloat(index) * step
            let rect = NSRect(x: x, y: (bounds.height - 8) / 2, width: max(step - 2, 2), height: 8)
            Palette.stage(states[spec.id] ?? "idle").setFill()
            NSBezierPath(roundedRect: rect, xRadius: 2, yRadius: 2).fill()
        }
    }
}

final class CenteredLabelCell: NSTableCellView {
    let label: NSTextField

    override init(frame frameRect: NSRect) {
        let field = NSTextField(labelWithString: "")
        field.translatesAutoresizingMaskIntoConstraints = false
        field.drawsBackground = false
        field.backgroundColor = .clear
        field.isBordered = false
        field.isBezeled = false
        field.isEditable = false
        field.isSelectable = false
        field.alignment = .center
        field.lineBreakMode = .byTruncatingTail
        field.usesSingleLineMode = true
        field.maximumNumberOfLines = 1
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        (field.cell as? NSTextFieldCell)?.alignment = .center
        (field.cell as? NSTextFieldCell)?.lineBreakMode = .byTruncatingTail
        self.label = field
        super.init(frame: frameRect)
        addSubview(field)
        NSLayoutConstraint.activate([
            field.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 4),
            field.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -4),
            field.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
        textField = field
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

final class LampCell: NSView {
    var on = false
    var color: NSColor = .systemGray
    var interactive = false
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        NSColor.clear.setFill()
        bounds.fill()
        (on ? color : NSColor.tertiaryLabelColor).setFill()
        let lampSize: CGFloat = 10
        let lampRect = NSRect(
            x: (bounds.width - lampSize) / 2,
            y: (bounds.height - lampSize) / 2,
            width: lampSize,
            height: lampSize
        )
        NSBezierPath(ovalIn: lampRect).fill()
        if interactive {
            NSColor.white.withAlphaComponent(0.35).setStroke()
            let ring = NSBezierPath(ovalIn: lampRect.insetBy(dx: 0.4, dy: 0.4))
            ring.lineWidth = 1
            ring.stroke()
        }
    }
    override func resetCursorRects() {
        discardCursorRects()
        if interactive {
            addCursorRect(bounds, cursor: .pointingHand)
        }
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        // Let the table receive lamp clicks so roomsTableClicked can toggle.
        return nil
    }
}


final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate, NSMenuDelegate, NSTableViewDataSource, NSTableViewDelegate, NSTextFieldDelegate, NSSearchFieldDelegate, NSWindowDelegate {
    let config: Config
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var timer: Timer?
    var lastNotifyCodes: Set<String> = []
    var lastSignature = ""
    var lastModel: MenubarModel?
    var logWindow: NSWindow?
    var logTextView: NSTextView?
    var logPipeline: PipelineView?
    var logSummary: NSTextField?
    var logHint: NSTextField?
    var roomsWindow: NSWindow?
    var roomsTable: NSTableView?
    var roomsFilterField: NSTextField?
    var roomsPipeline: PipelineView?
    var displayedChats: [AvailableChat] = []
    var allChats: [AvailableChat] = []
    var doctorWindow: NSWindow?
    var doctorTable: NSTableView?
    var doctorSummary: NSTextField?
    var doctorHealButton: NSButton?
    var doctorHint: NSTextField?
    var doctorFilter = "all"
    var doctorFilterControl: NSSegmentedControl?
    var improveRunning = false
    var progressField: NSTextField?
    var recheckButton: NSButton?
    var componentLamps: [(key: String, label: NSTextField)] = []
    var displayedChecks: [DoctorCheck] = []
    var lastDoctor: DoctorReport?
    var jobsWindow: NSWindow?
    var jobsTable: NSTableView?
    var jobsSummary: NSTextField?
    var displayedJobs: [JobRow] = []
    var jobsStatus = "open"
    var jobsSkipButton: NSButton?
    var jobsAckButton: NSButton?
    var jobsHint: NSTextField?
    var jobsTrace: NSTextView?
    var jobsFilterControl: NSSegmentedControl?
    var selectedJobEventId = ""
    var roomsSelectedChatId = 0
    var lastRoomsFingerprint = ""
    var vectorWindow: NSWindow?
    var vectorTable: NSTableView?
    var vectorSummary: NSTextField?
    var vectorHint: NSTextField?
    var vectorSearchField: NSTextField?
    var vectorChatField: NSTextField?
    var vectorUserField: NSTextField?
    var vectorDateField: NSTextField?
    var vectorMessageView: NSTextView?
    var vectorEmbeddingField: NSTextField?
    var vectorTopicsField: NSTextField?
    var displayedVectors: [VectorRow] = []
    var selectedVectorId: Int = 0
    var selectedVectorChat = ""
    var selectedVectorKey = ""
    var vectorOffset: Int = 0
    var vectorPageSize: Int = 200
    var vectorPrevButton: NSButton?
    var vectorNextButton: NSButton?
    var vectorAddButton: NSButton?
    var vectorSaveButton: NSButton?
    var vectorDeleteButton: NSButton?
    var vectorLoadToken = 0
    var lastVectorFingerprint = ""
    var vectorSourceStyle = true
    var vectorSourceKind = "style"
    var vectorTopicKey = ""
    var vectorSourceButton: NSPopUpButton?
    var vectorTopicButton: NSPopUpButton?
    let vectorSourceTitles = ["최연우 기억", "모든 대화", "주제별 지식", "설명 자료", "답장 기록", "말투·반응 통계", "탐색 프롬프트"]
    let vectorSourceKeys = ["style", "messages", "topics", "references", "replies", "profiles", "prompts"]
    var vectorRestoreButton: NSButton?
    // 모델 설정 창 (답변 모델 + 이미지 모델 통합, R5)
    var modelWindow: NSWindow?
    var modelReplyPopup: NSPopUpButton?
    var modelImagePopup: NSPopUpButton?
    var modelReplySummary: NSTextField?
    var modelImageSummary: NSTextField?
    var modelStatusField: NSTextField?
    var modelReplyStatus: NSTextField?
    var modelImageStatus: NSTextField?
    var modelRevertButton: NSButton?
    /// 같은 값이 두 곳에 남지 않도록, 되돌리기에 필요한 직전 선택만 보관한다.
    var lastReplySelection: ReplyModelSelection?
    var lastImageSelection: ReplyModelSelection?
    // 라이브옵스 창 (task 11) — 판단은 전부 코어(ui_shell::*_view)가 만든 문자열을
    // 그리기만 합니다. Swift에는 조건 분기가 없습니다.
    var durabilityWindow: NSWindow?
    var durabilityTextView: NSTextView?
    var coverageWindow: NSWindow?
    var coverageTextView: NSTextView?
    var improvementWindow: NSWindow?
    var improvementTextView: NSTextView?
    var onboardingWindow: NSWindow?
    var onboardingTextView: NSTextView?
    var menuPanel: MenuPanelView?
    var inspectedRoomId = 0
    var roomsListExpanded = false
    var menuTracking = false
    var refreshInFlight = false
    var refreshQueued = false
    var lastStatusImageKey = ""
    var lastApplySignature = ""
    var lastLogTextKey = ""
    var currentReplyModel: ReplyModelSelection?
    var currentImageReplyModel: ReplyModelSelection?
    var catalogProviders: [ReplyModelProvider] = []
    var catalogPresets: [ReplyProviderPreset] = []
    var oauthProviders: [ProviderOAuthItem] = []
    var catalogLoading = false
    var presetsLoading = false
    var oauthLoading = false
    let logLock = NSLock()
    static let jobKinds = ["open", "sent", "skipped", "unknown"]

    init(config: Config) {
        self.config = config
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(
            atPath: config.logsDir,
            withIntermediateDirectories: true
        )
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        if let button = statusItem.button {
            button.toolTip = "자동 답변"
            button.image = Self.statusImage(level: "yellow", stages: [])
            button.imagePosition = .imageOnly
        }
        catalogLoading = true
        presetsLoading = true
        statusItem.menu = buildMenu(Self.unavailableModel())
        loadModelCatalog()
        loadProviderPresets()
        loadOAuthProviders()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: config.interval, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        if let timer {
            RunLoop.main.add(timer, forMode: .common)
        }
        // 처음 실행(온보딩 완료 기록 없음)이면 권한 설정 화면을 바로 엽니다 (R9.6).
        // 어떤 권한에 무엇이 필요한지·허용 방법은 코어 capabilities()가 만듭니다.
        if !UserDefaults.standard.bool(forKey: "AutoReplyOnboardingShown") {
            UserDefaults.standard.set(true, forKey: "AutoReplyOnboardingShown")
            showOnboardingWindow()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        timer = nil
        vectorLoadToken += 1
    }

    func refresh() {
        if !Thread.isMainThread {
            DispatchQueue.main.async { [weak self] in self?.refresh() }
            return
        }
        if refreshInFlight {
            refreshQueued = true
            return
        }
        refreshInFlight = true
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            let model = self.loadModel() ?? Self.unavailableModel()
            DispatchQueue.main.async {
                self.refreshInFlight = false
                self.apply(model)
                if self.refreshQueued {
                    self.refreshQueued = false
                    self.refresh()
                }
            }
        }
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuTracking = true
    }

    func menuDidClose(_ menu: NSMenu) {
        guard menu === statusItem.menu else { return }
        menuTracking = false
        roomsListExpanded = false
        statusItem.button?.highlight(false)
        if let model = lastModel {
            statusItem.menu = buildMenu(model)
        }
    }

    func pythonArguments(_ extra: [String] = []) -> [String] {
        var arguments = [
            "-E", "-B", config.script,
            "--state-root", config.stateRoot,
            "--logs-dir", config.logsDir,
        ]
        if !config.expectedDigest.isEmpty {
            arguments.append(contentsOf: ["--expected-command-sha256", config.expectedDigest])
        }
        for room in config.rooms {
            arguments.append(contentsOf: ["--room", room])
        }
        if !config.bin.isEmpty {
            arguments.append(contentsOf: ["--bin", config.bin])
        }
        arguments.append(contentsOf: extra)
        return arguments
    }

    func runPython(_ extra: [String] = [], timeout: TimeInterval = 8) -> Data? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.python)
        process.arguments = pythonArguments(extra)
        let home = NSHomeDirectory()
        process.environment = [
            "HOME": home,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:" + home + "/.bun/bin:/usr/bin:/bin",
            "TMPDIR": "/tmp",
        ]
        let stdout = Pipe()
        process.standardOutput = stdout
        process.standardError = FileHandle.nullDevice
        process.standardInput = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return nil
        }
        // Drain stdout while the process runs. Waiting first deadlocks when
        // vector-list JSON exceeds the ~64KB pipe buffer.
        let group = DispatchGroup()
        let lock = NSLock()
        var data = Data()
        group.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            let chunk = stdout.fileHandleForReading.readDataToEndOfFile()
            lock.lock()
            data = chunk
            lock.unlock()
            group.leave()
        }
        let timeoutWork = DispatchWorkItem {
            if process.isRunning {
                process.terminate()
            }
        }
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + timeout, execute: timeoutWork)
        process.waitUntilExit()
        timeoutWork.cancel()
        _ = group.wait(timeout: .now() + 2)
        guard process.terminationStatus == 0, !data.isEmpty else {
            return nil
        }
        return data
    }

    func loadModel() -> MenubarModel? {
        guard let data = runPython() else { return nil }
        return try? JSONDecoder().decode(MenubarModel.self, from: data)
    }

    func applySignature(_ model: MenubarModel) -> String {
        let stages = (model.pipeline?.stages ?? []).map { "\($0.id):\($0.state)" }.joined(separator: ",")
        let rooms = (model.rooms ?? []).map {
            "\($0.chat_id):\($0.live):\($0.auto_reply):\($0.geeknews):\($0.open_jobs):\($0.sent):\($0.skipped):\($0.delivery_unknown)"
        }.joined(separator: ";")
        let logs = (model.log_display ?? model.log_lines ?? []).joined(separator: "\n")
        let replyModels = (model.reply_model_providers ?? []).map {
            "\($0.id):\($0.models.count)"
        }.joined(separator: ",")
        return [
            model.level,
            model.primary_code,
            model.reply_model?.id ?? "",
            model.image_reply_model?.id ?? "",
            replyModels,
            model.codes.joined(separator: ","),
            model.watermark ?? "",
            String(model.open_jobs),
            String(model.sent),
            String(model.skipped),
            String(model.delivery_unknown),
            model.geeknews_slots.joined(separator: ","),
            stages,
            rooms,
            roomsFingerprint(model.available_chats ?? []),
            model.vector_memory?.fingerprint ?? "",
            model.log_summary ?? "",
            logs,
        ].joined(separator: "|")
    }

    func apply(_ model: MenubarModel) {
        lastModel = model
        if let current = model.reply_model, !current.id.isEmpty {
            let live = currentReplyModel
            let authoritativeIdle = current.enabled == false || current.source == "unused"
            let snapshotLag = !authoritativeIdle
                && live?.source == "override"
                && current.source != "override"
                && current.id != live?.id
            if !snapshotLag {
                currentReplyModel = current
            }
        }
        if let current = model.image_reply_model, !current.id.isEmpty {
            let live = currentImageReplyModel
            let authoritativeIdle = current.enabled == false || current.source == "unused"
            let snapshotLag = !authoritativeIdle
                && live?.source == "override"
                && current.source != "override"
                && current.id != live?.id
            if !snapshotLag {
                currentImageReplyModel = current
            }
        }
        if let providers = model.reply_model_providers, !providers.isEmpty {
            catalogProviders = providers
            catalogLoading = false
        }
        let signature = applySignature(model)
        let changed = signature != lastApplySignature
        lastApplySignature = signature
        let stages = model.pipeline?.stages ?? []
        let imageKey = "\(model.level)|\(stages.map { "\($0.id):\($0.state)" }.joined(separator: ","))"
        if let button = statusItem.button {
            if imageKey != lastStatusImageKey {
                lastStatusImageKey = imageKey
                button.image = Self.statusImage(level: model.level, stages: stages)
            }
            button.toolTip = "자동 답변 · \(Palette.title(level: model.level)) · \(Self.vectorStatusLine(model.vector_memory))"
        }
        if changed {
            notify(model)
            appendLog(model)
        }
        if menuTracking {
            if changed, let panel = menuPanel {
                panel.model = model
                panel.selectedRoomId = inspectedRoomId
                panel.needsDisplay = true
            }
            return
        }
        if changed {
            statusItem.menu = buildMenu(model)
        }
        if !changed {
            return
        }
        if let window = logWindow, window.isVisible {
            updateLogWindow(model)
        }
        if let window = roomsWindow, window.isVisible {
            updateRoomsWindow(model)
        }
        if let window = vectorWindow, window.isVisible {
            applyVectorStatus(model.vector_memory)
        }
        // 새로고침 메뉴 없이도 코어 변경을 감지해 자동으로 갱신합니다 (R9.2, R9.3).
        if let window = modelWindow, window.isVisible {
            updateModelSettingsWindow()
        }
        // 라이브옵스 창도 열려 있으면 같은 자동 갱신 규약을 따릅니다 (R9.2, R9.3).
        if let window = durabilityWindow, window.isVisible {
            refreshDurabilityWindow()
        }
        if let window = coverageWindow, window.isVisible {
            refreshCoverageWindow()
        }
        if let window = improvementWindow, window.isVisible {
            refreshImprovementWindow()
        }
        if let window = onboardingWindow, window.isVisible {
            refreshOnboardingWindow()
        }
    }

    func buildMenu(_ model: MenubarModel) -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false
        menu.delegate = self
        let rooms = Self.inspectableRooms(in: model)
        let extra = MenuPanelView.roomGridExtra(count: rooms.count, expanded: roomsListExpanded)
        let graphic = NSMenuItem()
        let panel = MenuPanelView(model: model, frame: NSRect(x: 0, y: 0, width: MenuPanelView.panelWidth, height: MenuPanelView.panelBaseHeight + extra))
        panel.tileTarget = self
        panel.hamburgerTarget = self
        panel.selectedRoomId = inspectedRoomId
        panel.roomsExpanded = roomsListExpanded
        graphic.view = panel
        graphic.isEnabled = true
        menu.addItem(graphic)
        menuPanel = panel
        menu.addItem(.separator())
        // 주요 기능은 모두 "클릭하면 창"으로 엽니다 — 마우스를 올려 두는 드롭다운이
        // 아니라 클릭 한 번으로 별도의 창이 열립니다 (R1.2, R5.2, R6.1, R8.1).
        // 답변 모델과 이미지 모델은 하나의 "모델 설정" 창으로 합쳤습니다 (R5.1).
        // 새로고침 메뉴는 없앴습니다 (R9.1). 화면은 스스로 갱신됩니다.
        let logsItem = NSMenuItem(title: "기록…", action: #selector(showLogWindow), keyEquivalent: "l")
        logsItem.target = self
        logsItem.isEnabled = true
        menu.addItem(logsItem)
        let modelItem = NSMenuItem(title: "모델 설정…", action: #selector(showModelSettingsWindow), keyEquivalent: ",")
        modelItem.target = self
        modelItem.isEnabled = true
        menu.addItem(modelItem)
        let roomsItem = NSMenuItem(title: "채팅방…", action: #selector(showRoomsWindow), keyEquivalent: "m")
        roomsItem.target = self
        roomsItem.isEnabled = true
        menu.addItem(roomsItem)
        let doctorItem = NSMenuItem(title: "자가 점검…", action: #selector(showDoctorWindow), keyEquivalent: "d")
        doctorItem.target = self
        doctorItem.isEnabled = true
        menu.addItem(doctorItem)
        let vectorItem = NSMenuItem(title: "대화 기억…", action: #selector(showVectorWindow), keyEquivalent: "k")
        vectorItem.target = self
        vectorItem.isEnabled = true
        menu.addItem(vectorItem)
        menu.addItem(.separator())
        let quitItem = NSMenuItem(title: "메뉴 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quitItem)
        return menu
    }

    func buildLogsMenu(_ model: MenubarModel) -> NSMenuItem {
        let logsMenu = NSMenu()
        logsMenu.autoenablesItems = false
        let windowItem = NSMenuItem(
            title: "기록 창 열기",
            action: #selector(showLogWindow),
            keyEquivalent: "l"
        )
        windowItem.target = self
        windowItem.isEnabled = true
        logsMenu.addItem(windowItem)
        let count = displayLogLines(model).count
        let summary = NSMenuItem(
            title: count == 0 ? "기록 없음" : "최근 \(count)건",
            action: nil,
            keyEquivalent: ""
        )
        summary.isEnabled = false
        logsMenu.addItem(summary)
        let root = NSMenuItem(title: "기록", action: nil, keyEquivalent: "")
        root.submenu = logsMenu
        return root
    }

    func buildModelMenu(_ model: MenubarModel) -> NSMenuItem {
        let menu = NSMenu()
        menu.autoenablesItems = false
        let current = currentReplyModel ?? model.reply_model
        let currentId = current?.id ?? ""
        let currentLabel = (current?.label ?? currentId).trimmingCharacters(in: .whitespacesAndNewlines)
        let summaryTitle: String
        if currentLabel.isEmpty {
            summaryTitle = catalogLoading ? "현재 모델 불러오는 중…" : "현재 모델 없음"
        } else {
            summaryTitle = "현재 \(currentLabel)"
        }
        let summary = NSMenuItem(
            title: summaryTitle,
            action: nil,
            keyEquivalent: ""
        )
        summary.isEnabled = false
        menu.addItem(summary)
        menu.addItem(.separator())
        let providers = catalogProviders.isEmpty ? (model.reply_model_providers ?? []) : catalogProviders
        if providers.isEmpty {
            let emptyTitle = catalogLoading ? "모델 목록 불러오는 중…" : "모델 목록 없음 — 아래 새로고침"
            let empty = NSMenuItem(title: emptyTitle, action: nil, keyEquivalent: "")
            empty.isEnabled = false
            menu.addItem(empty)
        } else {
            for provider in providers {
                let providerItem = NSMenuItem(title: provider.label, action: nil, keyEquivalent: "")
                let sub = NSMenu()
                sub.autoenablesItems = false
                for item in provider.models {
                    let row = NSMenuItem(
                        title: item.label,
                        action: #selector(modelClicked(_:)),
                        keyEquivalent: ""
                    )
                    row.target = self
                    row.representedObject = item.id
                    row.state = item.id == currentId ? .on : .off
                    row.isEnabled = true
                    sub.addItem(row)
                }
                providerItem.submenu = sub
                menu.addItem(providerItem)
            }
        }
        menu.addItem(.separator())
        menu.addItem(buildProviderRegisterMenu())
        let reload = NSMenuItem(
            title: "목록 다시 불러오기",
            action: #selector(reloadModelsClicked),
            keyEquivalent: ""
        )
        reload.target = self
        menu.addItem(reload)
        let root = NSMenuItem(title: "답변 모델", action: nil, keyEquivalent: "")
        root.submenu = menu
        return root
    }

    func buildImageModelMenu(_ model: MenubarModel) -> NSMenuItem {
        let menu = NSMenu()
        menu.autoenablesItems = false
        let current = currentImageReplyModel ?? model.image_reply_model
        let enabled = current?.enabled ?? true
        let currentId = current?.id ?? "google-antigravity/gemini-3.7-flash-tiered"
        let currentLabel = (current?.label ?? currentId).trimmingCharacters(in: .whitespacesAndNewlines)
        let summaryTitle: String
        if !enabled {
            summaryTitle = "답변 모델이 이미지 처리"
        } else if currentLabel.isEmpty {
            summaryTitle = "현재 이미지 모델 없음"
        } else {
            summaryTitle = "현재 \(currentLabel)"
        }
        let summary = NSMenuItem(
            title: summaryTitle,
            action: nil,
            keyEquivalent: ""
        )
        summary.isEnabled = false
        menu.addItem(summary)
        menu.addItem(.separator())
        if !enabled {
            let unused = NSMenuItem(title: "선택 불가 — 답변 모델이 비전 지원", action: nil, keyEquivalent: "")
            unused.isEnabled = false
            menu.addItem(unused)
        } else {
            let providers = catalogProviders.isEmpty ? (model.reply_model_providers ?? []) : catalogProviders
            if providers.isEmpty {
                let empty = NSMenuItem(title: "모델 목록 없음 — 아래 새로고침", action: nil, keyEquivalent: "")
                empty.isEnabled = false
                menu.addItem(empty)
            } else {
                for provider in providers {
                    let providerItem = NSMenuItem(title: provider.label, action: nil, keyEquivalent: "")
                    let sub = NSMenu()
                    sub.autoenablesItems = false
                    for item in provider.models {
                        let row = NSMenuItem(
                            title: item.label,
                            action: #selector(imageModelClicked(_:)),
                            keyEquivalent: ""
                        )
                        row.target = self
                        row.representedObject = item.id
                        row.state = item.id == currentId ? .on : .off
                        row.isEnabled = true
                        sub.addItem(row)
                    }
                    providerItem.submenu = sub
                    menu.addItem(providerItem)
                }
            }
            menu.addItem(.separator())
            let reload = NSMenuItem(
                title: "목록 다시 불러오기",
                action: #selector(reloadModelsClicked),
                keyEquivalent: ""
            )
            reload.target = self
            menu.addItem(reload)
        }
        let root = NSMenuItem(title: "이미지 모델", action: nil, keyEquivalent: "")
        root.submenu = menu
        root.isEnabled = enabled
        return root
    }

    func applyImageReplyModelSelection(_ selection: ReplyModelSelection) {
        currentImageReplyModel = selection
        lastApplySignature = ""
        if !menuTracking {
            statusItem.menu = buildMenu(lastModel ?? Self.unavailableModel())
        }
    }

    @objc func imageModelClicked(_ sender: NSMenuItem) {
        guard let modelId = sender.representedObject as? String, !modelId.isEmpty else { return }
        let previous = currentImageReplyModel
        applyImageReplyModelSelection(replyModelSelection(id: modelId, label: sender.title, source: "override"))
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--action", "image-model-set", "--model", modelId], timeout: 8)
            DispatchQueue.main.async {
                guard let self else { return }
                if let data,
                   let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
                   report.ok == true,
                   let id = report.model, !id.isEmpty {
                    self.applyImageReplyModelSelection(
                        self.replyModelSelection(
                            id: id,
                            label: report.label ?? sender.title,
                            source: report.source ?? "override"
                        )
                    )
                    self.presentOperatorResult(action: "image-model-set", data: data)
                    return
                }
                if let previous {
                    self.applyImageReplyModelSelection(previous)
                }
                self.presentOperatorResult(action: "image-model-set", data: data)
            }
        }
    }

    func replyModelSelection(id: String, label: String, source: String) -> ReplyModelSelection {
        let parts = id.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: false)
        let provider = parts.first.map(String.init)
        let canonical = parts.count > 1 ? String(parts[1]) : id
        return ReplyModelSelection(
            id: id,
            label: label.isEmpty ? canonical : label,
            canonical: canonical,
            provider: provider,
            source: source,
            enabled: source != "unused"
        )
    }

    func applyReplyModelSelection(_ selection: ReplyModelSelection) {
        currentReplyModel = selection
        lastApplySignature = ""
        if !menuTracking {
            statusItem.menu = buildMenu(lastModel ?? Self.unavailableModel())
        }
    }

    @objc func modelClicked(_ sender: NSMenuItem) {
        guard let modelId = sender.representedObject as? String, !modelId.isEmpty else { return }
        let previous = currentReplyModel
        applyReplyModelSelection(replyModelSelection(id: modelId, label: sender.title, source: "override"))
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--action", "model-set", "--model", modelId], timeout: 8)
            DispatchQueue.main.async {
                guard let self else { return }
                if let data,
                   let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
                   report.ok == true,
                   let id = report.model, !id.isEmpty {
                    self.applyReplyModelSelection(
                        self.replyModelSelection(
                            id: id,
                            label: report.label ?? sender.title,
                            source: report.source ?? "override"
                        )
                    )
                    self.presentOperatorResult(action: "model-set", data: data)
                    return
                }
                if let previous {
                    self.applyReplyModelSelection(previous)
                }
                self.presentOperatorResult(action: "model-set", data: data)
            }
        }
    }

    @objc func reloadModelsClicked() {
        loadModelCatalog()
        loadProviderPresets()
        loadOAuthProviders()
        refreshModelSettingsWindowIfOpen()
    }

    // MARK: - 모델 설정 창 (답변 모델 + 이미지 모델 통합, R5)

    /// 진행 중·실패 안내는 새로고침이 덮지 않는다. 비어 있으면 현재 값 기준 문구를 쓴다.
    var modelReplyNote: String?
    var modelImageNote: String?

    /// 답변 모델을 바꾼다. 진행 중 답변은 옛 설정으로 끝까지 완료되고, 다음
    /// 답변부터 새 모델을 씁니다 (R5.5, R5.6은 코어의 스냅샷 스왑이 보장).
    func setReplyModel(id: String, label: String) {
        guard !id.isEmpty else { return }
        let previous = currentReplyModel
        modelReplyNote = "적용 중…"
        applyReplyModelSelection(replyModelSelection(id: id, label: label, source: "override"))
        modelStatusField?.stringValue = "답변 모델을 바꾸는 중이에요… 준비에 최대 3분까지 걸릴 수 있어요."
        modelReplyStatus?.stringValue = "적용 중…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            // 코어는 새 모델을 저장한 뒤 준비를 최대 180초 기다린다. 여기서 8초에
            // 끊으면 저장은 됐는데 화면만 실패로 보이는 구간이 생긴다.
            let data = self?.runPython(["--action", "model-set", "--model", id], timeout: 200)
            DispatchQueue.main.async {
                guard let self else { return }
                if let data,
                   let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
                   report.ok == true,
                   let rid = report.model, !rid.isEmpty {
                    self.applyReplyModelSelection(
                        self.replyModelSelection(
                            id: rid,
                            label: report.label ?? label,
                            source: report.source ?? "override"
                        )
                    )
                    self.modelStatusField?.stringValue = "답변 모델을 바꿨어요. 다음 답변부터 적용됩니다."
                    self.modelReplyStatus?.stringValue = "적용됨 · 다음 답변부터 사용"
                    self.modelReplyNote = nil
                    self.lastReplySelection = previous
                    self.modelRevertButton?.isEnabled = previous != nil
                    self.refreshModelSettingsWindowIfOpen()
                    return
                }
                // 실패하면 화면만 되돌리지 않고 저장된 값도 이전 모델로 되돌린다.
                // 되돌리기까지 실패하면 "이전 모델을 유지합니다"라고 말하지 않는다.
                let restored = self.restoreStoredReplyModel(previous)
                if restored, let previous { self.applyReplyModelSelection(previous) }
                self.modelStatusField?.stringValue = "답변 모델을 바꾸지 못했어요. 값을 확인한 뒤 다시 골라 주세요."
                self.modelReplyNote = restored
                    ? "적용하지 못했습니다. 이전 모델을 유지합니다. 모델을 다시 골라 주세요."
                    : "적용하지 못했습니다. 저장된 설정이 바뀌었을 수 있어요. 모델을 다시 골라 주세요."
                if !restored, let previous {
                    self.lastReplySelection = previous
                    self.modelRevertButton?.isEnabled = true
                }
                self.presentOperatorResult(action: "model-set", data: data)
                self.refreshModelSettingsWindowIfOpen()
            }
        }
    }

    /// 이미지 모델을 바꾼다 (R5.1, R5.4). 실패 시 이전 설정 유지 (R5.7).
    func setImageModel(id: String, label: String) {
        guard !id.isEmpty else { return }
        let previous = currentImageReplyModel
        modelImageNote = "적용 중…"
        applyImageReplyModelSelection(replyModelSelection(id: id, label: label, source: "override"))
        modelStatusField?.stringValue = "이미지 모델을 바꾸는 중이에요… 준비에 최대 3분까지 걸릴 수 있어요."
        modelImageStatus?.stringValue = "적용 중…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--action", "image-model-set", "--model", id], timeout: 200)
            DispatchQueue.main.async {
                guard let self else { return }
                if let data,
                   let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
                   report.ok == true,
                   let rid = report.model, !rid.isEmpty {
                    self.applyImageReplyModelSelection(
                        self.replyModelSelection(
                            id: rid,
                            label: report.label ?? label,
                            source: report.source ?? "override"
                        )
                    )
                    self.modelStatusField?.stringValue = "이미지 모델을 바꿨어요. 다음 답변부터 적용됩니다."
                    self.modelImageStatus?.stringValue = "적용됨 · 다음 답변부터 사용"
                    self.modelImageNote = nil
                    self.lastImageSelection = previous
                    self.modelRevertButton?.isEnabled = previous != nil
                    self.refreshModelSettingsWindowIfOpen()
                    return
                }
                let restored = self.restoreStoredImageModel(previous)
                if restored, let previous { self.applyImageReplyModelSelection(previous) }
                self.modelStatusField?.stringValue = "이미지 모델을 바꾸지 못했어요. 값을 확인한 뒤 다시 골라 주세요."
                self.modelImageNote = restored
                    ? "적용하지 못했습니다. 이전 모델을 유지합니다. 모델을 다시 골라 주세요."
                    : "적용하지 못했습니다. 저장된 설정이 바뀌었을 수 있어요. 모델을 다시 골라 주세요."
                if !restored, let previous {
                    self.lastImageSelection = previous
                    self.modelRevertButton?.isEnabled = true
                }
                self.presentOperatorResult(action: "image-model-set", data: data)
                self.refreshModelSettingsWindowIfOpen()
            }
        }
    }

    @objc func modelReplyPopupChanged(_ sender: NSPopUpButton) {
        guard let id = sender.selectedItem?.representedObject as? String, !id.isEmpty else { return }
        if id == (currentReplyModel?.id ?? "") { return }
        setReplyModel(id: id, label: sender.selectedItem?.title ?? id)
    }

    @objc func modelImagePopupChanged(_ sender: NSPopUpButton) {
        guard let id = sender.selectedItem?.representedObject as? String, !id.isEmpty else { return }
        if id == (currentImageReplyModel?.id ?? "") { return }
        setImageModel(id: id, label: sender.selectedItem?.title ?? id)
    }

    @objc func modelProviderRegisterClicked(_ sender: NSButton) {
        let root = buildProviderRegisterMenu()
        guard let submenu = root.submenu else { return }
        submenu.popUp(positioning: nil, at: NSPoint(x: 0, y: sender.bounds.height + 4), in: sender)
    }

    func modelProvidersForWindow() -> [ReplyModelProvider] {
        let live = lastModel?.reply_model_providers ?? []
        return catalogProviders.isEmpty ? live : catalogProviders
    }

    /// "opencode-go-session/deepseek-v4.1-flash" → "DeepSeek V4.1 Flash · OpenCode Go".
    static func friendlyModelName(_ id: String) -> String {
        let parts = id.split(separator: "/", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return id }
        let provider = parts[0].replacingOccurrences(of: "-", with: " ")
        let model = parts[1].replacingOccurrences(of: "-", with: " ")
        let prettyProvider = provider.split(separator: " ").map { $0.capitalized }.joined(separator: " ")
        let prettyModel = model.split(separator: " ").map { $0.capitalized }.joined(separator: " ")
        return "\(prettyModel) · \(prettyProvider)"
    }

    func populateModelPopup(
        _ popup: NSPopUpButton,
        providers: [ReplyModelProvider],
        currentId: String,
        enabled: Bool
    ) {
        // NSPopUpButton.addItem(withTitle:) skips duplicate titles, so build the
        // menu by hand to keep every provider/model pair.
        let menu = NSMenu()
        menu.autoenablesItems = false
        var matched: NSMenuItem?
        for provider in providers {
            for item in provider.models {
                let entry = NSMenuItem(title: "\(provider.label) · \(item.label)", action: nil, keyEquivalent: "")
                entry.representedObject = item.id
                menu.addItem(entry)
                if item.id == currentId { matched = entry }
            }
        }
        if matched == nil, !currentId.isEmpty {
            // 목록에 없는 모델은 긴 내부 ID 대신 읽을 수 있는 이름으로 보여 준다.
            let entry = NSMenuItem(title: Self.friendlyModelName(currentId), action: nil, keyEquivalent: "")
            entry.representedObject = currentId
            menu.addItem(entry)
            matched = entry
        }
        if menu.items.isEmpty {
            let entry = NSMenuItem(
                title: catalogLoading ? "모델 목록 불러오는 중…" : "모델 목록 없음 — 아래에서 다시 불러오세요",
                action: nil,
                keyEquivalent: ""
            )
            entry.representedObject = ""
            menu.addItem(entry)
        }
        popup.menu = menu
        if let matched { popup.select(matched) }
        let hasReal = !(menu.items.count == 1 && ((menu.items.first?.representedObject as? String) ?? "").isEmpty)
        popup.isEnabled = enabled && hasReal
    }

    func updateModelSettingsWindow() {
        guard modelWindow != nil else { return }
        let providers = modelProvidersForWindow()

        let reply = currentReplyModel ?? lastModel?.reply_model
        let replyId = reply?.id ?? ""
        let replyLabel = (reply?.label ?? replyId).trimmingCharacters(in: .whitespacesAndNewlines)
        // 역할 설명은 제목 아래에 고정하고, 현재 값은 선택란과 상태 줄에만 둔다.
        modelReplySummary?.stringValue = "메시지에 답할 때 사용합니다."
        modelReplyStatus?.stringValue = modelReplyNote ?? (replyLabel.isEmpty
            ? (catalogLoading ? "모델 목록 불러오는 중…" : "모델을 선택하세요.")
            : "적용됨 · 다음 답변부터 사용")
        modelReplyPopup?.toolTip = replyId.isEmpty ? nil : "현재 모델 ID: \(replyId)"
        if let popup = modelReplyPopup {
            populateModelPopup(popup, providers: providers, currentId: replyId, enabled: true)
        }

        let image = currentImageReplyModel ?? lastModel?.image_reply_model
        let imageEnabled = image?.enabled ?? true
        let imageId = image?.id ?? ""
        let imageLabel = (image?.label ?? imageId).trimmingCharacters(in: .whitespacesAndNewlines)
        modelImageSummary?.stringValue = "이미지가 포함된 메시지에 답할 때 사용합니다."
        let autoSelected = imageEnabled && (imageId.lowercased().contains("auto") || imageLabel.lowercased() == "auto")
        if let note = modelImageNote {
            modelImageStatus?.stringValue = note
        } else if !imageEnabled {
            modelImageStatus?.stringValue = "답변 모델이 사진도 함께 봐요 — 이미지 모델을 따로 고르지 않아도 됩니다."
        } else if autoSelected {
            // '고른 모델 없음'과 '자동 선택'을 같은 문구로 보여 주지 않는다.
            modelImageStatus?.stringValue = "자동 선택 · 이미지 메시지에 사용할 모델을 자동으로 선택합니다."
        } else if imageLabel.isEmpty {
            modelImageStatus?.stringValue = "이미지 모델 선택… — 이미지 메시지에 답하려면 모델을 선택하세요."
        } else {
            modelImageStatus?.stringValue = "적용됨 · 다음 답변부터 사용"
        }
        modelImagePopup?.toolTip = imageId.isEmpty ? nil : "현재 모델 ID: \(imageId)"
        if let popup = modelImagePopup {
            populateModelPopup(popup, providers: providers, currentId: imageId, enabled: imageEnabled)
        }
    }

    func refreshModelSettingsWindowIfOpen() {
        if let window = modelWindow, window.isVisible {
            updateModelSettingsWindow()
        }
    }

    func ensureModelSettingsWindow() {
        if modelWindow != nil {
            return
        }
        let window = Chrome.operatorWindow(
            title: "모델 설정",
            size: NSSize(width: 620, height: 400),
            autosave: "AutoReplyModelSettings"
        )
        let content = NSView()
        window.contentView = content

        let hint = Chrome.hint("선택한 모델은 자동 저장되며, 다음 답변부터 적용됩니다.")

        let replyTitle = Chrome.label("답변 모델", size: 13, weight: .semibold, lines: 1)
        let replySummary = Chrome.hint("현재 모델을 불러오는 중…")
        modelReplySummary = replySummary
        let replyPopup = NSPopUpButton(frame: .zero, pullsDown: false)
        replyPopup.translatesAutoresizingMaskIntoConstraints = false
        replyPopup.target = self
        replyPopup.action = #selector(modelReplyPopupChanged(_:))
        modelReplyPopup = replyPopup

        let imageTitle = Chrome.label("이미지 답변 모델", size: 13, weight: .semibold, lines: 1)
        let imageSummary = Chrome.hint("현재 이미지 모델을 불러오는 중…")
        modelImageSummary = imageSummary
        let imagePopup = NSPopUpButton(frame: .zero, pullsDown: false)
        imagePopup.translatesAutoresizingMaskIntoConstraints = false
        imagePopup.target = self
        imagePopup.action = #selector(modelImagePopupChanged(_:))
        modelImagePopup = imagePopup

        let status = Chrome.label("", size: 12, color: .secondaryLabelColor, lines: 2)
        modelStatusField = status
        // 모델별 상태 줄: 바꾼 행에서 바로 결과를 볼 수 있게 한다.
        let replyStatus = Chrome.label("", size: 12, color: .secondaryLabelColor, lines: 2)
        modelReplyStatus = replyStatus
        let imageStatus = Chrome.label("", size: 12, color: .secondaryLabelColor, lines: 2)
        modelImageStatus = imageStatus

        let registerButton = Chrome.roundedButton("모델 제공자 추가…", target: self, action: #selector(modelProviderRegisterClicked(_:)))
        let reloadButton = Chrome.roundedButton("모델 목록 새로고침", target: self, action: #selector(reloadModelsClicked))
        let revertButton = Chrome.roundedButton("이전 모델로 되돌리기", target: self, action: #selector(modelRevertClicked(_:)))
        revertButton.isEnabled = false
        modelRevertButton = revertButton
        let actions = Chrome.hstack([registerButton, reloadButton, revertButton, Chrome.spacer()])

        let stack = Chrome.vstack(
            [hint, replyTitle, replySummary, replyPopup, replyStatus,
             imageTitle, imageSummary, imagePopup, imageStatus, status, actions],
            spacing: 8
        )
        stack.setCustomSpacing(16, after: replyStatus)
        Chrome.fill(stack, in: content)
        NSLayoutConstraint.activate([
            hint.widthAnchor.constraint(equalTo: stack.widthAnchor),
            replyPopup.widthAnchor.constraint(equalTo: stack.widthAnchor),
            imagePopup.widthAnchor.constraint(equalTo: stack.widthAnchor),
            status.widthAnchor.constraint(equalTo: stack.widthAnchor),
            actions.widthAnchor.constraint(equalTo: stack.widthAnchor),
        ])
        // 키보드 순서: 답변 모델 → 이미지 모델 → 제공자 추가 → 목록 새로고침.
        window.initialFirstResponder = replyPopup
        replyPopup.nextKeyView = imagePopup
        imagePopup.nextKeyView = registerButton
        registerButton.nextKeyView = reloadButton
        reloadButton.nextKeyView = revertButton
        modelWindow = window
    }

    /// 모델 설정 명령이 실제로 성공했는지 응답으로 확인한다.
    func reportSucceeded(_ data: Data?) -> Bool {
        guard let data,
              let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
              report.ok == true,
              let rid = report.model, !rid.isEmpty
        else { return false }
        return true
    }

    /// 실패한 적용을 되돌릴 때는 저장된 값도 함께 되돌린다. 되돌릴 값이 없으면 false.
    func restoreStoredReplyModel(_ previous: ReplyModelSelection?) -> Bool {
        guard let previous, !previous.id.isEmpty else { return false }
        return reportSucceeded(runPython(["--action", "model-set", "--model", previous.id], timeout: 200))
    }

    func restoreStoredImageModel(_ previous: ReplyModelSelection?) -> Bool {
        guard let previous, !previous.id.isEmpty else { return false }
        return reportSucceeded(runPython(["--action", "image-model-set", "--model", previous.id], timeout: 200))
    }

    /// 직전에 적용했던 모델로 되돌린다. 답변·이미지 어느 쪽을 바꿨든 그 항목을
    /// 되돌리고, 성공한 항목의 이력만 지워 재시도를 막지 않는다.
    @objc func modelRevertClicked(_ sender: Any?) {
        let previousReply = lastReplySelection
        let previousImage = lastImageSelection
        guard previousReply != nil || previousImage != nil else { return }
        modelStatusField?.stringValue = "이전 모델로 되돌리는 중이에요…"
        modelReplyNote = previousReply == nil ? modelReplyNote : "되돌리는 중…"
        modelImageNote = previousImage == nil ? modelImageNote : "되돌리는 중…"
        modelRevertButton?.isEnabled = false
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            var restoredReply = previousReply == nil
            var restoredImage = previousImage == nil
            if let previousImage {
                restoredImage = self.restoreStoredImageModel(previousImage)
                if restoredImage {
                    DispatchQueue.main.async { self.applyImageReplyModelSelection(previousImage) }
                }
            }
            if let previousReply {
                restoredReply = self.restoreStoredReplyModel(previousReply)
                if restoredReply {
                    DispatchQueue.main.async { self.applyReplyModelSelection(previousReply) }
                }
            }
            DispatchQueue.main.async {
                if restoredReply { self.lastReplySelection = nil }
                else if let previousReply { self.lastReplySelection = previousReply }
                if restoredImage { self.lastImageSelection = nil }
                else if let previousImage { self.lastImageSelection = previousImage }
                self.modelReplyNote = restoredReply
                    ? nil
                    : "되돌리지 못했습니다. 다시 시도해 주세요."
                self.modelImageNote = restoredImage
                    ? nil
                    : "되돌리지 못했습니다. 다시 시도해 주세요."
                self.modelStatusField?.stringValue = (restoredReply && restoredImage)
                    ? "이전 모델로 되돌렸어요. 다음 답변부터 적용됩니다."
                    : "일부 모델을 되돌리지 못했어요. 모델 설정에서 다시 시도해 주세요."
                self.modelRevertButton?.isEnabled = self.lastReplySelection != nil || self.lastImageSelection != nil
                self.refreshModelSettingsWindowIfOpen()
            }
        }
    }

    @objc func showModelSettingsWindow() {
        ensureModelSettingsWindow()
        guard let window = modelWindow else {
            // 창을 열지 못하면 쉬운말 안내 + 이전 상태 유지 (R1.5, R10.4).
            alertOperator(
                title: "모델 설정 창을 열지 못했어요",
                message: "잠시 뒤 메뉴에서 모델 설정을 다시 눌러 주세요. 지금 설정은 그대로 있어요."
            )
            return
        }
        presentOperatorWindow(window)
        if catalogProviders.isEmpty && !catalogLoading {
            loadModelCatalog()
        }
        updateModelSettingsWindow()
    }

    func buildProviderRegisterMenu() -> NSMenuItem {
        let menu = NSMenu()
        menu.autoenablesItems = false
        let presets = catalogPresets
        if presetsLoading && presets.isEmpty {
            let loading = NSMenuItem(title: "프리셋 불러오는 중…", action: nil, keyEquivalent: "")
            loading.isEnabled = false
            menu.addItem(loading)
        } else {
            for preset in presets {
                let title = preset.name?.isEmpty == false ? (preset.name ?? preset.id) : preset.id
                let row = NSMenuItem(
                    title: title,
                    action: #selector(providerPresetClicked(_:)),
                    keyEquivalent: ""
                )
                row.target = self
                row.representedObject = preset.id
                row.toolTip = preset.description
                row.isEnabled = true
                menu.addItem(row)
            }
        }
        menu.addItem(.separator())
        let openai = NSMenuItem(
            title: "직접 입력 (OpenAI 호환)…",
            action: #selector(customProviderClicked(_:)),
            keyEquivalent: ""
        )
        openai.target = self
        openai.representedObject = "openai"
        menu.addItem(openai)
        let anthropic = NSMenuItem(
            title: "직접 입력 (Anthropic 호환)…",
            action: #selector(customProviderClicked(_:)),
            keyEquivalent: ""
        )
        anthropic.target = self
        anthropic.representedObject = "anthropic"
        menu.addItem(anthropic)
        menu.addItem(.separator())
        let oauthRoot = NSMenuItem(title: "OAuth 브라우저 로그인", action: nil, keyEquivalent: "")
        let oauthMenu = NSMenu()
        oauthMenu.autoenablesItems = false
        if oauthLoading && oauthProviders.isEmpty {
            let loading = NSMenuItem(title: "OAuth 목록 불러오는 중…", action: nil, keyEquivalent: "")
            loading.isEnabled = false
            oauthMenu.addItem(loading)
        } else if oauthProviders.isEmpty {
            let empty = NSMenuItem(title: "목록 다시 불러오기", action: #selector(reloadModelsClicked), keyEquivalent: "")
            empty.target = self
            oauthMenu.addItem(empty)
        } else {
            for item in oauthProviders {
                let ident = item.id
                guard !ident.isEmpty else { continue }
                let title = (item.name?.isEmpty == false ? item.name : ident) ?? ident
                let row = NSMenuItem(
                    title: title,
                    action: #selector(providerOAuthClicked(_:)),
                    keyEquivalent: ""
                )
                row.target = self
                row.representedObject = ident
                row.toolTip = "가재코드 auth-broker login으로 브라우저 로그인을 엽니다."
                oauthMenu.addItem(row)
            }
        }
        oauthRoot.submenu = oauthMenu
        menu.addItem(oauthRoot)
        let root = NSMenuItem(title: "프로바이더 등록", action: nil, keyEquivalent: "")
        root.submenu = menu
        return root
    }

    func promptProviderFields(
        title: String,
        message: String,
        fields: [(label: String, value: String, placeholder: String)]
    ) -> [String]? {
        statusItem.menu?.cancelTracking()
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: "등록")
        alert.addButton(withTitle: "취소")
        let width: CGFloat = 420
        let rowHeight: CGFloat = 48
        let height = CGFloat(max(fields.count, 1)) * rowHeight
        let accessory = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        var inputs: [NSTextField] = []
        for (index, field) in fields.enumerated() {
            let y = height - CGFloat(index + 1) * rowHeight
            let caption = NSTextField(labelWithString: field.label)
            caption.frame = NSRect(x: 0, y: y + 24, width: width, height: 16)
            caption.font = NSFont.systemFont(ofSize: 11)
            caption.textColor = .secondaryLabelColor
            caption.isEditable = false
            caption.isBordered = false
            caption.drawsBackground = false
            let input = NSTextField(string: field.value)
            input.placeholderString = field.placeholder
            input.frame = NSRect(x: 0, y: y + 2, width: width, height: 22)
            accessory.addSubview(caption)
            accessory.addSubview(input)
            inputs.append(input)
        }
        alert.accessoryView = accessory
        if let first = inputs.first {
            alert.window.initialFirstResponder = first
        }
        let response = alert.runModal()
        guard response == .alertFirstButtonReturn else { return nil }
        return inputs.map { $0.stringValue.trimmingCharacters(in: .whitespacesAndNewlines) }
    }

    func confirmOverwriteProvider() -> Bool {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "이미 등록된 프로바이더"
        alert.informativeText = "같은 이름이 있습니다. 덮어쓸까요? 키 원문은 저장하지 않고, 환경 변수 이름만 다시 적습니다."
        alert.addButton(withTitle: "덮어쓰기")
        alert.addButton(withTitle: "취소")
        return alert.runModal() == .alertFirstButtonReturn
    }

    func loadProviderPresets() {
        presetsLoading = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--action", "provider-presets"], timeout: 12)
            DispatchQueue.main.async {
                guard let self else { return }
                self.presetsLoading = false
                if let data,
                   let report = try? JSONDecoder().decode(ProviderPresetsReport.self, from: data),
                   let presets = report.presets, !presets.isEmpty {
                    self.catalogPresets = presets
                }
                if let model = self.lastModel, !self.menuTracking {
                    self.statusItem.menu = self.buildMenu(model)
                }
            }
        }
    }

    func submitProviderAdd(_ extra: [String], force: Bool = false) {
        var arguments = ["--action", "provider-add"]
        arguments.append(contentsOf: extra)
        if force {
            arguments.append("--provider-force")
        }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(arguments, timeout: 45)
            DispatchQueue.main.async {
                guard let self else { return }
                if let data,
                   let report = try? JSONDecoder().decode(ProviderAddReport.self, from: data) {
                    if report.ok == true {
                        self.alertOperator(
                            title: "프로바이더 등록",
                            message: "등록했습니다. 모델 목록을 다시 불러옵니다."
                        )
                        self.loadModelCatalog()
                        return
                    }
                    if report.reason == "provider_exists", self.confirmOverwriteProvider() {
                        self.submitProviderAdd(extra, force: true)
                        return
                    }
                }
                self.presentOperatorResult(action: "provider-add", data: data)
            }
        }
    }

    @objc func providerPresetClicked(_ sender: NSMenuItem) {
        guard let presetId = sender.representedObject as? String, !presetId.isEmpty else { return }
        let preset = catalogPresets.first(where: { $0.id == presetId })
        let needsURL = preset?.needs_base_url == true
        var fields: [(label: String, value: String, placeholder: String)] = [
            (
                "환경 변수 이름",
                preset?.api_key_env ?? "",
                "ZAI_API_KEY"
            )
        ]
        if needsURL {
            fields.append(("API 주소", "", "https://"))
        }
        fields.append(("모델 (비우면 기본값)", preset?.models?.first ?? "", "선택"))
        let values = promptProviderFields(
            title: preset?.name ?? presetId,
            message: "메뉴바 앱 전용 프로바이더로 등록합니다 (맥의 가재코드 설정과 독립적입니다). API 키 원문은 적지 말고, 키가 들어 있는 환경 변수 이름만 적으세요.",
            fields: fields
        )
        guard let values else { return }
        var extra = ["--provider-preset", presetId]
        let envName = values[0]
        if !envName.isEmpty {
            extra.append(contentsOf: ["--provider-api-key-env", envName])
        }
        if needsURL {
            let url = values[1]
            extra.append(contentsOf: ["--provider-base-url", url])
            let model = values.count > 2 ? values[2] : ""
            if !model.isEmpty {
                extra.append(contentsOf: ["--provider-model", model])
            }
        } else {
            let model = values.count > 1 ? values[1] : ""
            if !model.isEmpty {
                extra.append(contentsOf: ["--provider-model", model])
            }
        }
        submitProviderAdd(extra)
    }

    @objc func customProviderClicked(_ sender: NSMenuItem) {
        let compat = (sender.representedObject as? String) ?? "openai"
        let values = promptProviderFields(
            title: compat == "anthropic" ? "Anthropic 호환 프로바이더" : "OpenAI 호환 프로바이더",
            message: "메뉴바 앱 전용 프로바이더로 등록합니다 (맥의 가재코드 설정과 독립적입니다). API 키 원문은 받지 않습니다.",
            fields: [
                ("프로바이더 이름", "", "my-provider"),
                ("API 주소", "", "https://api.example.com/v1"),
                ("환경 변수 이름", "", "MY_PROVIDER_API_KEY"),
                ("모델", "", "model-id")
            ]
        )
        guard let values else { return }
        submitProviderAdd([
            "--provider-compat", compat,
            "--provider-id", values[0],
            "--provider-base-url", values[1],
            "--provider-api-key-env", values[2],
            "--provider-model", values[3]
        ])
    }

    func loadOAuthProviders() {
        oauthLoading = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--action", "provider-oauth-list"], timeout: 20)
            DispatchQueue.main.async {
                guard let self else { return }
                self.oauthLoading = false
                if let data,
                   let report = try? JSONDecoder().decode(ProviderOAuthReport.self, from: data),
                   let providers = report.providers, !providers.isEmpty {
                    self.oauthProviders = providers.filter { !$0.id.isEmpty }
                }
                if let model = self.lastModel, !self.menuTracking {
                    self.statusItem.menu = self.buildMenu(model)
                }
            }
        }
    }

    @objc func providerOAuthClicked(_ sender: NSMenuItem) {
        guard let providerId = sender.representedObject as? String, !providerId.isEmpty else { return }
        statusItem.menu?.cancelTracking()
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "OAuth 브라우저 로그인"
        alert.informativeText = providerId + " 브라우저 로그인 창을 엽니다. 끝나면 이 알림이 닫힙니다. 키 원문은 저장하지 않습니다."
        alert.addButton(withTitle: "로그인")
        alert.addButton(withTitle: "취소")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(
                ["--action", "provider-oauth-login", "--provider-id", providerId],
                timeout: 180
            )
            DispatchQueue.main.async {
                guard let self else { return }
                if let data,
                   let report = try? JSONDecoder().decode(ProviderOAuthReport.self, from: data),
                   report.ok == true {
                    self.alertOperator(
                        title: "OAuth 로그인",
                        message: "로그인했습니다. 모델 목록을 다시 불러옵니다."
                    )
                    self.loadModelCatalog()
                    return
                }
                self.presentOperatorResult(action: "provider-oauth-login", data: data)
            }
        }
    }

    func loadModelCatalog() {
        catalogLoading = true
        if !menuTracking {
            statusItem.menu = buildMenu(lastModel ?? Self.unavailableModel())
        }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--action", "models"], timeout: 45)
            DispatchQueue.main.async {
                guard let self else { return }
                self.catalogLoading = false
                if let data,
                   let report = try? JSONDecoder().decode(ModelsReport.self, from: data) {
                    if let providers = report.providers, !providers.isEmpty {
                        self.catalogProviders = providers
                    }
                    if let id = report.model, !id.isEmpty {
                        self.currentReplyModel = ReplyModelSelection(
                            id: id,
                            label: report.label ?? id,
                            canonical: report.model,
                            provider: report.provider,
                            source: report.source,
                            enabled: true
                        )
                    }
                } else if !self.catalogProviders.isEmpty {
                    // 목록이 이미 있는데 새로 고침이 실패하면 조용히 넘어가지 않는다.
                    self.modelStatusField?.stringValue = "모델 목록을 새로 고치지 못했어요. 이전 목록을 그대로 보여 드려요."
                }
                if let model = self.lastModel {
                    if !self.menuTracking {
                        self.statusItem.menu = self.buildMenu(model)
                    }
                } else if !self.menuTracking {
                    self.statusItem.menu = self.buildMenu(Self.unavailableModel())
                }
                // 새로 고침이 끝나면 열려 있는 설정 창도 같은 목록으로 맞춘다.
                self.refreshModelSettingsWindowIfOpen()
                self.refresh()
            }
        }
    }

    func displayLogLines(_ model: MenubarModel) -> [String] {
        let friendly = model.log_display ?? []
        if !friendly.isEmpty {
            return friendly
        }
        let fromModel = model.log_lines ?? []
        if !fromModel.isEmpty {
            return fromModel
        }
        return model.journal.map { "journal \($0)" }
    }

    func notify(_ model: MenubarModel) {
        let incoming = Set(model.notifications.map(\.code))
        let fresh = incoming.subtracting(lastNotifyCodes)
        lastNotifyCodes = incoming
        for note in model.notifications where fresh.contains(note.code) {
            let content = UNMutableNotificationContent()
            content.title = note.title
            content.body = note.body
            let request = UNNotificationRequest(
                identifier: "auto_reply.\(note.code)",
                content: content,
                trigger: nil
            )
            UNUserNotificationCenter.current().add(request, withCompletionHandler: nil)
        }
    }

    func appendLog(_ model: MenubarModel) {
        let signature = "\(model.level)|\(model.codes.joined(separator: ","))|\(model.watermark ?? "")"
        guard signature != lastSignature else { return }
        lastSignature = signature
        var payload: [String: Any] = [
            "ts": Int(Date().timeIntervalSince1970),
            "level": model.level,
            "codes": model.codes,
            "open_jobs": model.open_jobs,
            "delivery_unknown": model.delivery_unknown,
            "geeknews_slots": model.geeknews_slots,
        ]
        if let watermark = model.watermark {
            payload["watermark"] = watermark
        }
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload),
              var line = String(data: data, encoding: .utf8) else { return }
        line.append("\n")
        let path = (config.logsDir as NSString).appendingPathComponent("transitions.jsonl")
        logLock.lock()
        defer { logLock.unlock() }
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        guard let handle = FileHandle(forWritingAtPath: path) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: Data(line.utf8))
    }

    @objc func refreshClicked() {
        refresh()
    }

    @objc func instantAutoReplyClicked() {
        runOperatorAction("auto-reply-now", chatId: inspectedRoomId)
    }

    @objc func instantGeekNewsClicked() {
        runOperatorAction("geeknews-now", chatId: inspectedRoomId)
    }

    func runOperatorAction(_ action: String, chatId: Int = 0) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var extra = ["--action", action]
            if chatId != 0 {
                extra.append(contentsOf: ["--chat-id", String(chatId)])
            }
            let data = self?.runPython(extra)
            DispatchQueue.main.async {
                self?.presentOperatorResult(action: action, data: data)
                self?.refresh()
            }
        }
    }

    func presentOperatorResult(action: String, data: Data?) {
        guard let data,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }
        let ok = object["ok"] as? Bool ?? false
        let reason = object["reason"] as? String ?? ""
        let warnings = object["warnings"] as? [String] ?? []
        let axOk = object["ax_window_ok"] as? Bool ?? true
        if reason == "ax_window_missing" || axOk == false {
            alertOperator(
                title: "채팅방을 창으로 띄워 주세요",
                message: warnings.first
                    ?? "카카오톡에서 해당 단체 채팅방을 창으로 띄운 다음 다시 눌러 주세요. 창이 없으면 입력칸에만 들어가고 전송 버튼을 누르지 못합니다."
            )
            return
        }
        if reason == "occupancy_blocked" {
            alertOperator(
                title: "미확인 전송이 남아 있습니다",
                message: warnings.first ?? "작업 목록의 미확인에서 건너뛰거나 확인한 뒤 다시 눌러 주세요."
            )
            return
        }
        if !ok {
            let fallback: String
            if action == "geeknews-now" {
                fallback = "긱뉴스를 보내지 못했습니다."
            } else if action == "model-set" {
                fallback = "모델을 바꾸지 못했습니다."
            } else if action == "image-model-set" {
                fallback = "이미지 모델을 바꾸지 못했습니다."
            } else if action == "provider-add" {
                fallback = "프로바이더를 등록하지 못했습니다."
            } else {
                fallback = "바로 실행하지 못했습니다."
            }
            let title: String
            if action == "model-set" {
                title = "답변 모델"
            } else if action == "image-model-set" {
                title = "이미지 모델"
            } else if action == "provider-add" {
                title = "프로바이더 등록"
            } else {
                title = "바로 실행"
            }
            alertOperator(
                title: title,
                message: warnings.first ?? fallback
            )
            return
        }
        if !warnings.isEmpty {
            alertOperator(title: "요청을 넣었습니다", message: warnings.joined(separator: "\n"))
        }
    }

    func alertOperator(title: String, message: String) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: "확인")
        alert.runModal()
    }

    func presentOperatorWindow(_ window: NSWindow?) {
        statusItem.button?.highlight(false)
        statusItem.menu?.cancelTracking()
        guard let window else { return }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()
    }

    @objc func showLogWindow() {
        ensureLogWindow()
        guard let window = logWindow else {
            // 창을 열지 못하면 쉬운말로 안내하고 이전 상태를 그대로 둡니다 (R1.5, R10.4).
            alertOperator(
                title: "기록 창을 열지 못했어요",
                message: "잠시 뒤 메뉴에서 기록을 다시 눌러 주세요. 지금까지의 상태는 그대로 있어요."
            )
            return
        }
        presentOperatorWindow(window)
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if let model = self.lastModel {
                self.updateLogWindow(model)
            }
        }
    }


    static func roomChoices(in model: MenubarModel) -> [RoomChoice] {
        let titles = Dictionary(uniqueKeysWithValues: (model.available_chats ?? []).map { ($0.chat_id, $0.title) })
        return (model.rooms ?? []).map { room in
            RoomChoice(
                chat_id: room.chat_id,
                title: titles[room.chat_id].flatMap { $0.isEmpty ? nil : $0 } ?? "방 \(room.chat_id)",
                live: room.live,
                level: room.level,
                pipeline: room.pipeline,
                codes: room.codes,
                open_jobs: room.open_jobs,
                sent: room.sent,
                skipped: room.skipped,
                delivery_unknown: room.delivery_unknown,
                geeknews_slots: room.geeknews_slots
            )
        }
    }

    static func inspectableRooms(in model: MenubarModel) -> [RoomChoice] {
        let workers = Dictionary(uniqueKeysWithValues: roomChoices(in: model).map { ($0.chat_id, $0) })
        var rooms: [RoomChoice] = []
        var seen = Set<Int>()
        for chat in model.available_chats ?? [] {
            guard chat.catalog || chat.live else { continue }
            guard seen.insert(chat.chat_id).inserted else { continue }
            if let room = workers[chat.chat_id] {
                rooms.append(
                    RoomChoice(
                        chat_id: room.chat_id,
                        title: chat.title.isEmpty ? room.title : chat.title,
                        live: room.live,
                        level: room.level,
                        pipeline: room.pipeline,
                        codes: room.codes,
                        open_jobs: room.open_jobs,
                        sent: room.sent,
                        skipped: room.skipped,
                        delivery_unknown: room.delivery_unknown,
                        geeknews_slots: room.geeknews_slots
                    )
                )
                continue
            }
            rooms.append(
                RoomChoice(
                    chat_id: chat.chat_id,
                    title: chat.title.isEmpty ? "방 \(chat.chat_id)" : chat.title,
                    live: chat.live,
                    level: chat.live ? "green" : "off",
                    pipeline: PipelineModel(active_index: nil, event_id: "none", outcome: "none", stages: []),
                    codes: chat.live ? [] : ["auto_reply_off"],
                    open_jobs: 0,
                    sent: 0,
                    skipped: 0,
                    delivery_unknown: 0,
                    geeknews_slots: []
                )
            )
        }
        for room in roomChoices(in: model) where seen.insert(room.chat_id).inserted {
            rooms.append(room)
        }
        return rooms
    }

    static func selectedRoom(in model: MenubarModel, preferred: Int) -> RoomChoice? {
        let rooms = inspectableRooms(in: model)
        if preferred != 0, let match = rooms.first(where: { $0.chat_id == preferred }) {
            return match
        }
        let catalog = inspectableRooms(in: model)
        if let busy = catalog.first(where: { $0.open_jobs > 0 }) {
            return busy
        }
        return catalog.first(where: { $0.live }) ?? catalog.first
    }

    func roomPickerMenu(from model: MenubarModel) -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false
        let rooms = Self.inspectableRooms(in: model)
        if rooms.isEmpty {
            let empty = NSMenuItem(title: "고를 방이 없습니다", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            menu.addItem(empty)
            return menu
        }
        for room in rooms {
            let busy = room.open_jobs > 0 ? " · 처리중" : ""
            let live = room.live ? "" : " · 꺼짐"
            let item = NSMenuItem(
                title: "\(room.title)\(busy)\(live)",
                action: #selector(inspectRoomClicked(_:)),
                keyEquivalent: ""
            )
            item.target = self
            item.representedObject = room.chat_id
            let selectedId = inspectedRoomId == 0 ? Self.selectedRoom(in: model, preferred: 0)?.chat_id : inspectedRoomId
            item.state = room.chat_id == selectedId ? .on : .off
            item.toolTip = room.live ? "이 방 워커를 봅니다" : "카탈로그에 있는 방입니다. 워커는 다음 기동부터 붙습니다"
            menu.addItem(item)
        }
        return menu
    }

    @objc func toggleRoomListClicked(_ sender: NSButton) {
        _ = sender
        roomsListExpanded.toggle()
        if let panel = menuPanel, let model = lastModel {
            applyRoomList(to: panel, model: model)
        }
    }

    @objc func inspectRoomButtonClicked(_ sender: NSButton) {
        inspectRoom(sender.tag)
    }

    @objc func roomPickerClicked(_ sender: NSButton) {
        toggleRoomListClicked(sender)
    }

    @objc func hamburgerClicked(_ sender: NSButton) {
        toggleRoomListClicked(sender)
    }

    @objc func inspectRoomClicked(_ sender: NSMenuItem) {
        guard let chatId = sender.representedObject as? Int else { return }
        inspectRoom(chatId)
    }

    func applyRoomList(to panel: MenuPanelView, model: MenubarModel) {
        let rooms = Self.inspectableRooms(in: model)
        let extra = MenuPanelView.roomGridExtra(count: rooms.count, expanded: roomsListExpanded)
        panel.roomsExpanded = roomsListExpanded
        panel.selectedRoomId = inspectedRoomId
        panel.model = model
        panel.setFrameSize(NSSize(width: MenuPanelView.panelWidth, height: MenuPanelView.panelBaseHeight + extra))
        panel.layoutRoomGrid()
        panel.needsDisplay = true
        if let item = statusItem.menu?.items.first {
            item.view = panel
        }
    }

    func inspectRoom(_ chatId: Int) {
        inspectedRoomId = chatId
        if let panel = menuPanel, let model = lastModel {
            applyRoomList(to: panel, model: model)
        }
        if let window = roomsWindow, window.isVisible, let model = lastModel {
            roomsSelectedChatId = chatId
            updateRoomsWindow(model)
        }
        if let window = logWindow, window.isVisible, let model = lastModel {
            updateLogWindow(model)
        }
        if let window = jobsWindow, window.isVisible {
            refreshJobs(status: jobsStatus)
        }
    }

    @objc func showRoomsWindow() {
        ensureRoomsWindow()
        presentOperatorWindow(roomsWindow)
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if let model = self.lastModel {
                self.updateRoomsWindow(model)
            }
        }
    }

    @objc func showDoctorWindow() {
        ensureDoctorWindow()
        presentOperatorWindow(doctorWindow)
        DispatchQueue.main.async { [weak self] in
            self?.refreshDoctor(heal: false)
        }
    }

    @objc func showVectorWindow() {
        ensureVectorWindow()
        vectorOffset = 0
        displayedVectors = []
        selectedVectorId = 0
        selectedVectorChat = ""
        selectedVectorKey = ""
        vectorEmbeddingField?.stringValue = ""
        vectorTopicsField?.stringValue = ""
        vectorSummary?.stringValue = "기억을 불러오는 중…"
        vectorTable?.reloadData()
        presentOperatorWindow(vectorWindow)
        DispatchQueue.main.async { [weak self] in
            self?.refreshVectorList()
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if sender === vectorWindow {
            vectorLoadToken += 1
            sender.orderOut(nil)
            return false
        }
        return true
    }

    @objc func tileClicked(_ sender: NSButton) {
        let kinds = Self.jobKinds
        let tag = sender.tag
        guard tag >= 0, tag < kinds.count else { return }
        showJobsWindow(status: kinds[tag])
    }

    @objc func jobsFilterChanged(_ sender: NSSegmentedControl) {
        let kinds = Self.jobKinds
        let index = sender.selectedSegment
        guard index >= 0, index < kinds.count else { return }
        jobsStatus = kinds[index]
        refreshJobs(status: jobsStatus)
    }

    func showJobsWindow(status: String) {
        jobsStatus = status
        ensureJobsWindow()
        let kinds = Self.jobKinds
        if let index = kinds.firstIndex(of: status) {
            jobsFilterControl?.selectedSegment = index
        }
        presentOperatorWindow(jobsWindow)
        DispatchQueue.main.async { [weak self] in
            self?.refreshJobs(status: status)
        }
    }

    func loadJobs(status: String) -> JobReport? {
        guard let data = runPython(["--action", "jobs", "--jobs-status", status]) else { return nil }
        return try? JSONDecoder().decode(JobReport.self, from: data)
    }

    func refreshJobs(status: String) {
        jobsStatus = status
        jobsSummary?.stringValue = "목록을 읽는 중"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let report = self.loadJobs(status: status) ?? JobReport(
                ok: false,
                action: "jobs",
                privacy: "content_redacted",
                status: status,
                title: "목록",
                count: 0,
                truncated: false,
                jobs: []
            )
            DispatchQueue.main.async {
                guard self.jobsStatus == status else { return }
                self.applyJobs(report)
            }
        }
    }

    func applyJobs(_ report: JobReport) {
        let selected = selectedJobEventId
        let roomId = inspectedRoomId
        displayedJobs = roomId == 0 ? report.jobs : report.jobs.filter { $0.chat_id == roomId }
        if let summary = jobsSummary {
            let extra = report.truncated ? " · 최근만 표시" : ""
            summary.stringValue = "\(report.title) \(report.count)건\(extra)"
        }
        jobsTable?.reloadData()
        restoreJobsSelection(eventId: selected)
        updateJobsActions()
    }

    func restoreJobsSelection(eventId: String) {
        guard !eventId.isEmpty,
              let index = displayedJobs.firstIndex(where: { $0.event_id == eventId }) else {
            updateJobsActions()
            return
        }
        jobsTable?.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false)
    }

    func updateJobsActions() {
        let row = jobsTable?.selectedRow ?? -1
        let job = (row >= 0 && row < displayedJobs.count) ? displayedJobs[row] : nil
        selectedJobEventId = job?.event_id ?? selectedJobEventId
        let unknown = job?.status == "delivery_unknown"
        jobsSkipButton?.isEnabled = unknown || (job?.can_skip ?? false)
        jobsAckButton?.isEnabled = unknown || (job?.can_ack ?? false)
        switch jobsStatus {
        case "sent":
            jobsHint?.stringValue = "이미 보낸 기록입니다. 다시 보내지는 않습니다."
        case "skipped":
            jobsHint?.stringValue = "건너뛴 작업입니다. 다시 보내지는 않습니다."
        case "unknown":
            jobsHint?.stringValue = "미확인은 입력칸에만 들어갔거나 결과를 모를 때입니다. 건너뛰기는 다시 보내지 않습니다. 확인은 카카오톡에 이미 올라간 경우만 기록합니다."
        default:
            jobsHint?.stringValue = "시간 순서로 보여 줍니다. 미확인은 건너뛰거나, 이미 보낸 경우에만 확인으로 기록합니다. 다시 보내지는 않습니다."
        }
        if let view = jobsTrace {
            if let job {
                let body = [
                    job.detail ?? "",
                    "event \(job.event_id)",
                    "status \(job.status_label) · \(job.reason_label)",
                    job.category_label.map { "category \($0)" } ?? "",
                ].filter { !$0.isEmpty }.joined(separator: "\n")
                view.string = body.isEmpty ? "선택한 작업의 상세가 없습니다." : body
            } else {
                view.string = "작업을 선택하면 프롬프트·검색·전송 과정이 여기 표시됩니다."
            }
        }
    }

    @objc func jobsSkipClicked() {
        mutateSelectedUnknownJob(action: "jobs-skip")
    }

    @objc func jobsAckClicked() {
        mutateSelectedUnknownJob(action: "jobs-ack")
    }

    func mutateSelectedUnknownJob(action: String) {
        let row = jobsTable?.selectedRow ?? -1
        guard row >= 0, row < displayedJobs.count else {
            alertOperator(title: "작업을 선택해 주세요", message: "미확인 목록에서 처리할 줄을 클릭한 뒤 버튼을 누르세요.")
            return
        }
        let eventId = displayedJobs[row].event_id
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--action", action, "--jobs-event-id", eventId])
            DispatchQueue.main.async {
                self?.presentOperatorResult(action: action, data: data)
                if let status = self?.jobsStatus {
                    self?.refreshJobs(status: status)
                }
                self?.refresh()
            }
        }
    }

    func ensureJobsWindow() {
        if jobsWindow != nil {
            return
        }
        let window = Chrome.operatorWindow(title: "작업 목록", size: NSSize(width: 720, height: 540), autosave: "AutoReplyJobs")
        let content = NSView()
        window.contentView = content

        let summary = Chrome.summary("목록을 읽는 중")
        jobsSummary = summary

        let filter = NSSegmentedControl(
            labels: ["대기", "전송", "건너뜀", "미확인"],
            trackingMode: .selectOne,
            target: self,
            action: #selector(jobsFilterChanged(_:))
        )
        filter.translatesAutoresizingMaskIntoConstraints = false
        filter.segmentStyle = .rounded
        filter.selectedSegment = 0
        jobsFilterControl = filter

        let hint = Chrome.hint("시간 순서로 보여 줍니다. 미확인은 건너뛰거나, 이미 보낸 경우에만 확인으로 기록합니다. 다시 보내지는 않습니다.")
        jobsHint = hint

        let skip = Chrome.roundedButton("미확인 건너뛰기", target: self, action: #selector(jobsSkipClicked))
        skip.isEnabled = false
        jobsSkipButton = skip
        let ack = Chrome.roundedButton("전송된 것으로 확인", target: self, action: #selector(jobsAckClicked))
        ack.isEnabled = false
        jobsAckButton = ack
        let actions = Chrome.hstack([skip, ack, Chrome.spacer()])

        let (scroll, table) = Chrome.table()
        table.delegate = self
        table.dataSource = self
        for spec in [
            ("when", "시각", 168.0),
            ("status", "상태", 88.0),
            ("reason", "구분", 340.0),
        ] as [(String, String, CGFloat)] {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(spec.0))
            column.title = spec.1
            column.width = spec.2
            column.minWidth = 72
            column.headerCell.alignment = .center
            if let cell = column.dataCell as? NSTextFieldCell {
                cell.alignment = .center
            }
            table.addTableColumn(column)
        }
        jobsTable = table

        let traceScroll = NSScrollView()
        traceScroll.translatesAutoresizingMaskIntoConstraints = false
        traceScroll.hasVerticalScroller = true
        traceScroll.borderType = .bezelBorder
        let trace = NSTextView(frame: .zero)
        trace.isEditable = false
        trace.isSelectable = true
        trace.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        trace.textColor = NSColor.labelColor
        trace.backgroundColor = NSColor.textBackgroundColor
        trace.minSize = NSSize(width: 0, height: 0)
        trace.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        trace.isHorizontallyResizable = false
        trace.isVerticallyResizable = true
        trace.textContainerInset = NSSize(width: 8, height: 8)
        trace.textContainer?.widthTracksTextView = true
        trace.string = "작업을 선택하면 프롬프트·검색·전송 과정이 여기 표시됩니다."
        traceScroll.documentView = trace
        jobsTrace = trace

        let stack = Chrome.vstack([summary, filter, hint, scroll, traceScroll, actions], spacing: 10)
        Chrome.fill(stack, in: content)
        NSLayoutConstraint.activate([
            summary.widthAnchor.constraint(equalTo: stack.widthAnchor),
            filter.widthAnchor.constraint(equalTo: stack.widthAnchor),
            hint.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            actions.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 180),
            traceScroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            traceScroll.heightAnchor.constraint(equalToConstant: 120),
        ])
        jobsWindow = window
    }

    func loadDoctor(heal: Bool) -> DoctorReport? {
        let action = heal ? "doctor-heal" : "doctor"
        guard let data = runPython(["--action", action]) else { return nil }
        return try? JSONDecoder().decode(DoctorReport.self, from: data)
    }

    func refreshDoctor(heal: Bool) {
        doctorSummary?.stringValue = "점검 중"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let report = self.loadDoctor(heal: heal) ?? Self.unavailableDoctor()
            DispatchQueue.main.async {
                self.applyDoctor(report)
            }
        }
    }

    func applyDoctor(_ report: DoctorReport) {
        lastDoctor = report
        let filtered: [DoctorCheck]
        switch doctorFilter {
        case "problem":
            filtered = report.checks.filter { $0.level == "fail" || $0.level == "warn" }
        case "ok":
            filtered = report.checks.filter { $0.level == "ok" }
        default:
            filtered = report.checks
        }
        displayedChecks = filtered.sorted { lhs, rhs in
            let rank: [String: Int] = ["fail": 0, "warn": 1, "off": 2, "ok": 3]
            let left = rank[lhs.level] ?? 4
            let right = rank[rhs.level] ?? 4
            if left != right { return left < right }
            return lhs.title < rhs.title
        }
        let fail = report.checks.filter { $0.level == "fail" }.count
        let warn = report.checks.filter { $0.level == "warn" }.count
        let ok = report.checks.filter { $0.level == "ok" }.count
        if let summary = doctorSummary {
            let title = Palette.title(level: report.level)
            let caption = Palette.caption(code: report.primary_code)
            let healed = report.healed.isEmpty ? "" : " · 고침 \(report.healed.count)"
            summary.stringValue = "\(title) · \(caption) · 문제 \(fail) · 주의 \(warn) · 정상 \(ok)\(healed)"
            summary.textColor = Palette.level(report.level)
        }
        if let hint = doctorHint {
            if fail + warn == 0 {
                hint.stringValue = "지금은 막힌 항목이 없습니다. 메시지를 보내거나 세션을 재시작하지는 않습니다."
            } else {
                hint.stringValue = "문제와 주의가 위에 있습니다. 고칠 수 있는 항목만 표시를 지우거나 이미 예약된 답변을 프로그램에 알립니다. 세션을 재시작하거나 카카오톡을 앞으로 가져오지는 않습니다."
            }
        }
        doctorHealButton?.isEnabled = !report.healable.isEmpty
        if improveRunning {
            doctorHealButton?.isEnabled = false
        } else {
            doctorHealButton?.isEnabled = !report.healable.isEmpty
        }
        updateComponentLamps(report.health ?? [:])
        doctorTable?.reloadData()
    }

    func updateComponentLamps(_ health: [String: String]) {
        let color: (String) -> NSColor = { value in
            switch value {
            case "ok": return NSColor.systemGreen
            case "warn": return NSColor.systemYellow
            case "err": return NSColor.systemRed
            default: return NSColor.systemGray
            }
        }
        for (key, label) in componentLamps {
            let state = health[key] ?? "off"
            let name = ["watchdog": "감시", "supervisor": "감독", "ax": "창",
                        "worker": "워커", "model": "모델"][key] ?? key
            label.attributedStringValue = NSAttributedString(
                string: "● \(name)",
                attributes: [
                    .font: NSFont.systemFont(ofSize: 11, weight: .medium),
                    .foregroundColor: color(state),
                ]
            )
            label.toolTip = "\(name): \(state)"
        }
    }

    @objc func doctorRecheckClicked() {
        refreshDoctor(heal: false)
        refresh()
    }

    @objc func doctorHealClicked() {
        runImprovePipeline()
    }

    /// 자가 개선 파이프라인: 잔여 정리 → 재조화 → 세션 기동 → 구성요소 램프가
    /// 전부 초록이 될 때까지 2초 간격 폴링. 메시지를 보내거나 카카오톡을
    /// 앞으로 가져오지는 않습니다.
    func runImprovePipeline() {
        guard !improveRunning else { return }
        improveRunning = true
        doctorHealButton?.isEnabled = false
        progressField?.stringValue = "① 잔여 정리·재조화…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            _ = self.runPython(["--action", "improve-prep"], timeout: 20)
            DispatchQueue.main.async {
                self.progressField?.stringValue = "② 세션 기동 확인…"
            }
            _ = self.runPython(["--action", "improve-launch"], timeout: 25)
            var finalReport: DoctorReport?
            for step in 0..<30 {
                if !self.improveRunning { break }
                DispatchQueue.main.async {
                    self.progressField?.stringValue =
                        "③ 상태 확인 중… (\(step + 1)/30) — 감시·감독·창·워커·모델"
                }
                if let data = self.runPython(["--action", "doctor"], timeout: 15),
                   let report = try? JSONDecoder().decode(DoctorReport.self, from: data) {
                    finalReport = report
                    DispatchQueue.main.async {
                        self.applyDoctor(report)
                    }
                    if let health = report.health,
                       health.values.allSatisfy({ $0 == "ok" }) {
                        break
                    }
                }
                Thread.sleep(forTimeInterval: 2)
            }
            DispatchQueue.main.async {
                self.improveRunning = false
                self.doctorHealButton?.isEnabled = !(self.lastDoctor?.healable.isEmpty ?? true)
                if let report = finalReport {
                    let allGreen = (report.health ?? [:]).values.allSatisfy { $0 == "ok" }
                    self.progressField?.stringValue = allGreen
                        ? "✓ 감시·감독·창·워커·모델 전부 초록"
                        : "미확인 항목이 남아 있습니다(창은 채팅방 창이 열려 있어야 합니다)."
                    if allGreen, let summary = self.doctorSummary {
                        summary.textColor = Palette.level("green")
                    }
                } else {
                    self.progressField?.stringValue = "개선 결과를 읽지 못했습니다. 다시 시도해 주세요."
                }
            }
        }
    }

    @objc func doctorFilterChanged(_ sender: NSSegmentedControl) {
        switch sender.selectedSegment {
        case 1: doctorFilter = "problem"
        case 2: doctorFilter = "ok"
        default: doctorFilter = "all"
        }
        if let report = lastDoctor {
            applyDoctor(report)
        }
    }

    func ensureDoctorWindow() {
        if doctorWindow != nil {
            return
        }
        let window = Chrome.operatorWindow(title: "자가 점검", size: NSSize(width: 760, height: 560), autosave: "AutoReplyDoctor")
        let content = NSView()
        window.contentView = content

        let summary = Chrome.summary("점검 중")
        doctorSummary = summary
        let hint = Chrome.hint("자가 점검은 보기만 합니다. 자가 개선은 고칠 수 있는 항목만 적용합니다. 메시지를 보내거나 세션을 재시작하거나 카카오톡을 앞으로 가져오지는 않습니다.")
        doctorHint = hint
        let filter = NSSegmentedControl(labels: ["전체", "문제", "정상"], trackingMode: .selectOne, target: self, action: #selector(doctorFilterChanged(_:)))
        filter.selectedSegment = 0
        filter.segmentStyle = .rounded
        doctorFilterControl = filter

        let recheckButton = Chrome.roundedButton("다시 점검", target: self, action: #selector(doctorRecheckClicked))
        self.recheckButton = recheckButton
        let recheck = recheckButton
        let heal = Chrome.roundedButton("자가 개선", target: self, action: #selector(doctorHealClicked))
        doctorHealButton = heal
        let actions = Chrome.hstack([recheck, heal, Chrome.spacer()])

        let (scroll, table) = Chrome.table()
        table.delegate = self
        table.dataSource = self
        table.rowHeight = 44
        table.usesAlternatingRowBackgroundColors = true
        for spec in [
            ("level", "상태", 72.0),
            ("title", "항목", 110.0),
            ("advice", "설명", 430.0),
            ("heal", "조치", 90.0),
        ] as [(String, String, CGFloat)] {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(spec.0))
            column.title = spec.1
            column.width = spec.2
            column.minWidth = 56
            column.headerCell.alignment = .center
            if let cell = column.dataCell as? NSTextFieldCell {
                cell.alignment = .center
            }
            table.addTableColumn(column)
        }
        doctorTable = table

        var componentLamps: [(key: String, label: NSTextField)] = []
        for (key, name) in [("watchdog", "감시"), ("supervisor", "감독"), ("ax", "창"), ("worker", "워커"), ("model", "모델")] {
            let label = NSTextField(labelWithString: "● \(name)")
            label.font = NSFont.systemFont(ofSize: 11, weight: .medium)
            label.textColor = NSColor.systemGray
            componentLamps.append((key, label))
        }
        self.componentLamps = componentLamps
        let lampsRow = Chrome.hstack(componentLamps.map { $0.label } + [Chrome.spacer()])
        let progress = Chrome.hint("자가 개선을 누르면 진행 상황이 여기 표시됩니다.")
        progressField = progress
        let stack = Chrome.vstack([summary, hint, filter, lampsRow, progress, scroll, actions], spacing: 10)
        Chrome.fill(stack, in: content)
        NSLayoutConstraint.activate([
            summary.widthAnchor.constraint(equalTo: stack.widthAnchor),
            hint.widthAnchor.constraint(equalTo: stack.widthAnchor),
            lampsRow.widthAnchor.constraint(equalTo: stack.widthAnchor),
            progress.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            actions.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 260),
        ])
        doctorWindow = window
    }


    func ensureLogWindow() {
        if logWindow != nil {
            return
        }
        let window = Chrome.operatorWindow(title: "최근 기록", size: NSSize(width: 740, height: 560), autosave: "AutoReplyLogs")
        let content = NSView()
        window.contentView = content

        let pipeline = PipelineView(frame: .zero)
        pipeline.translatesAutoresizingMaskIntoConstraints = false
        pipeline.heightAnchor.constraint(equalToConstant: 56).isActive = true
        logPipeline = pipeline

        let summary = Chrome.summary("상태를 읽는 중")
        logSummary = summary
        let hint = Chrome.hint("최근 무슨 일이 있었는지 쉬운 말로 보여 줍니다. 대화 내용, 초안, 이름은 나오지 않습니다.")
        logHint = hint

        let scroll = NSScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.borderType = .noBorder
        scroll.drawsBackground = true
        scroll.autohidesScrollers = true
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)

        let text = NSTextView(frame: .zero)
        text.isEditable = false
        text.isSelectable = true
        text.font = NSFont.systemFont(ofSize: 13)
        text.textColor = NSColor.labelColor
        text.backgroundColor = NSColor.textBackgroundColor
        text.minSize = NSSize(width: 0, height: 0)
        text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        text.isHorizontallyResizable = false
        text.isVerticallyResizable = true
        text.textContainerInset = NSSize(width: 16, height: 14)
        text.textContainer?.widthTracksTextView = true
        text.textContainer?.lineFragmentPadding = 4
        text.textContainer?.containerSize = NSSize(width: 700, height: CGFloat.greatestFiniteMagnitude)
        scroll.documentView = text
        logTextView = text

        let stack = Chrome.vstack([pipeline, summary, hint, scroll], spacing: 10)
        Chrome.fill(stack, in: content, insets: NSEdgeInsets(top: 16, left: 16, bottom: 0, right: 16))
        NSLayoutConstraint.activate([
            pipeline.widthAnchor.constraint(equalTo: stack.widthAnchor),
            summary.widthAnchor.constraint(equalTo: stack.widthAnchor),
            hint.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 280),
        ])
        logWindow = window
    }

    // ---------------------------------------------------------------------
    // 라이브옵스 창 (task 11, R1/R5/R8/R9)
    //
    // 이 네 창과 온보딩 화면은 코어의 ui_shell::*_view가 만든 쉬운말 문자열을
    // 그대로 그립니다. Swift에는 판단·분기 로직이 없습니다 (design: "Swift only
    // draws the strings core produces").
    //
    // 스모크 확인(별도 1회): 아이콘 클릭 후 10초 안에 메뉴바 항목이 뜨는지(R9.2),
    // 그리고 DMG 안에 서명·공증을 통과한 .app 1개와 끌어다 놓을 설치 위치 1개가
    // 있는지(R9.4)는 무거운 런타임 로직 없이 배포 스모크 스크립트에서 한 번
    // 확인합니다. packaging::package() / capabilities()가 코어에서 그 규칙을
    // 검증합니다.
    // ---------------------------------------------------------------------

    /// 여러 줄 텍스트를 그리는 단순 창을 만듭니다 (라이브옵스 창 공용).
    func makeLinesWindow(title: String, autosave: String, hint: String) -> (NSWindow, NSTextView) {
        let window = Chrome.operatorWindow(title: title, size: NSSize(width: 720, height: 540), autosave: autosave)
        let content = NSView()
        window.contentView = content

        let hintField = Chrome.hint(hint)

        let scroll = NSScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.borderType = .noBorder
        scroll.drawsBackground = true
        scroll.autohidesScrollers = true
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)

        let text = NSTextView(frame: .zero)
        text.isEditable = false
        text.isSelectable = true
        text.font = NSFont.systemFont(ofSize: 13)
        text.textColor = NSColor.labelColor
        text.backgroundColor = NSColor.textBackgroundColor
        text.minSize = NSSize(width: 0, height: 0)
        text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        text.isHorizontallyResizable = false
        text.isVerticallyResizable = true
        text.textContainerInset = NSSize(width: 16, height: 14)
        text.textContainer?.widthTracksTextView = true
        text.textContainer?.lineFragmentPadding = 4
        text.textContainer?.containerSize = NSSize(width: 680, height: CGFloat.greatestFiniteMagnitude)
        scroll.documentView = text

        let stack = Chrome.vstack([hintField, scroll], spacing: 10)
        Chrome.fill(stack, in: content, insets: NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16))
        NSLayoutConstraint.activate([
            hintField.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 320),
        ])
        return (window, text)
    }

    /// 코어가 만든 줄을 그대로 텍스트 뷰에 붙입니다. 서식 판단은 없습니다.
    func setLines(_ lines: [String], on text: NSTextView?) {
        guard let text else { return }
        text.string = lines.joined(separator: "\n")
    }

    // ----- 대량 검증 (R1) -----

    func ensureDurabilityWindow() {
        if durabilityWindow != nil { return }
        let (window, text) = makeLinesWindow(
            title: "대량 검증",
            autosave: "AutoReplyDurability",
            hint: "가짜 어댑터로 자동 답변 1000회·긱뉴스 100회를 돌린 결과입니다. 실제 전송과 외부 송신은 0건입니다."
        )
        durabilityWindow = window
        durabilityTextView = text
    }

    @objc func showDurabilityWindow() {
        ensureDurabilityWindow()
        guard let window = durabilityWindow else {
            alertOperator(title: "대량 검증 창을 열지 못했어요", message: "잠시 뒤 메뉴에서 다시 눌러 주세요.")
            return
        }
        presentOperatorWindow(window)
        setLines(["결과를 불러오는 중…"], on: durabilityTextView)
        refreshDurabilityWindow()
    }

    func refreshDurabilityWindow() {
        guard durabilityWindow != nil else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let data = self.runPython(["--action", "durability-view"], timeout: 15)
            DispatchQueue.main.async {
                guard let text = self.durabilityTextView else { return }
                guard let data,
                      let view = try? JSONDecoder().decode(DurabilityViewModel.self, from: data)
                else {
                    self.setLines(
                        ["대량 검증 결과를 아직 불러오지 못했어요. 잠시 뒤 다시 열어 주세요."],
                        on: text
                    )
                    return
                }
                var lines = view.auto_reply
                lines.append("")
                lines.append(contentsOf: view.geeknews)
                lines.append("")
                lines.append(contentsOf: view.verdict)
                lines.append("")
                lines.append(view.seed)
                self.setLines(lines, on: text)
            }
        }
    }

    // ----- 기능 점검 (R5) -----

    func ensureCoverageWindow() {
        if coverageWindow != nil { return }
        let (window, text) = makeLinesWindow(
            title: "기능 점검",
            autosave: "AutoReplyCoverage",
            hint: "방마다 여섯 기능이 제대로 되는지 점검한 결과입니다. 실제 전송은 하지 않습니다."
        )
        coverageWindow = window
        coverageTextView = text
    }

    @objc func showCoverageWindow() {
        ensureCoverageWindow()
        guard let window = coverageWindow else {
            alertOperator(title: "기능 점검 창을 열지 못했어요", message: "잠시 뒤 메뉴에서 다시 눌러 주세요.")
            return
        }
        presentOperatorWindow(window)
        setLines(["결과를 불러오는 중…"], on: coverageTextView)
        refreshCoverageWindow()
    }

    func refreshCoverageWindow() {
        guard coverageWindow != nil else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let data = self.runPython(["--action", "coverage-view"], timeout: 15)
            DispatchQueue.main.async {
                guard let text = self.coverageTextView else { return }
                guard let data,
                      let view = try? JSONDecoder().decode(CoverageViewModel.self, from: data)
                else {
                    self.setLines(
                        ["기능 점검 결과를 아직 불러오지 못했어요. 잠시 뒤 다시 열어 주세요."],
                        on: text
                    )
                    return
                }
                var lines = [view.summary, view.status, ""]
                lines.append(contentsOf: view.rows)
                self.setLines(lines, on: text)
            }
        }
    }

    // ----- 자기개선 (R8) -----

    func ensureImprovementWindow() {
        if improvementWindow != nil { return }
        let (window, text) = makeLinesWindow(
            title: "자기개선",
            autosave: "AutoReplyImprovement",
            hint: "답변 품질을 스스로 고친 기록입니다. 대화 내용·초안·이름은 나오지 않습니다."
        )
        improvementWindow = window
        improvementTextView = text
    }

    @objc func showImprovementWindow() {
        ensureImprovementWindow()
        guard let window = improvementWindow else {
            alertOperator(title: "자기개선 창을 열지 못했어요", message: "잠시 뒤 메뉴에서 다시 눌러 주세요.")
            return
        }
        presentOperatorWindow(window)
        setLines(["기록을 불러오는 중…"], on: improvementTextView)
        refreshImprovementWindow()
    }

    func refreshImprovementWindow() {
        guard improvementWindow != nil else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let data = self.runPython(["--action", "improvement-view"], timeout: 15)
            DispatchQueue.main.async {
                guard let text = self.improvementTextView else { return }
                guard let data,
                      let view = try? JSONDecoder().decode(ImprovementViewModel.self, from: data)
                else {
                    self.setLines(
                        ["자기개선 기록을 아직 불러오지 못했어요. 잠시 뒤 다시 열어 주세요."],
                        on: text
                    )
                    return
                }
                let lines = view.rows.isEmpty
                    ? [view.empty ?? "아직 자기개선 기록이 없어요."]
                    : view.rows
                self.setLines(lines, on: text)
            }
        }
    }

    // ----- 권한 설정 / 온보딩 (R9.6, R9.11) -----

    func ensureOnboardingWindow() {
        if onboardingWindow != nil { return }
        let (window, text) = makeLinesWindow(
            title: "권한 설정",
            autosave: "AutoReplyOnboarding",
            hint: "지금 허용된 권한과 그로 인해 쓸 수 있는 기능, 막힌 기능과 허용 방법을 보여 줍니다."
        )
        onboardingWindow = window
        onboardingTextView = text
    }

    @objc func showOnboardingWindow() {
        ensureOnboardingWindow()
        guard let window = onboardingWindow else {
            alertOperator(title: "권한 설정 창을 열지 못했어요", message: "잠시 뒤 메뉴에서 다시 눌러 주세요.")
            return
        }
        presentOperatorWindow(window)
        setLines(["권한 상태를 불러오는 중…"], on: onboardingTextView)
        refreshOnboardingWindow()
    }

    func refreshOnboardingWindow() {
        guard onboardingWindow != nil else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let data = self.runPython(["--action", "onboarding-view"], timeout: 15)
            DispatchQueue.main.async {
                guard let text = self.onboardingTextView else { return }
                guard let data,
                      let view = try? JSONDecoder().decode(OnboardingViewModel.self, from: data)
                else {
                    self.setLines(
                        ["권한 상태를 아직 불러오지 못했어요. 잠시 뒤 다시 열어 주세요."],
                        on: text
                    )
                    return
                }
                var lines = ["사용할 수 있는 기능:"]
                lines.append(contentsOf: view.available.map { " · \($0)" })
                lines.append("")
                lines.append("막힌 기능과 허용 방법:")
                lines.append(contentsOf: view.blocked.map { " · \($0)" })
                self.setLines(lines, on: text)
            }
        }
    }


    func updateLogWindow(_ model: MenubarModel) {
        let inspected = Self.selectedRoom(in: model, preferred: inspectedRoomId)
        logPipeline?.stages = inspected?.pipeline.stages ?? model.pipeline?.stages ?? []
        logPipeline?.level = inspected?.level ?? model.level
        logPipeline?.needsDisplay = true
        let summary = model.log_summary?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if summary.isEmpty {
            logSummary?.stringValue = "\(Palette.title(level: model.level)) — \(Palette.caption(code: model.primary_code))"
        } else {
            logSummary?.stringValue = summary
        }
        applyLogText(displayLogLines(model))
    }

    func logLineColor(_ line: String) -> NSColor {
        if line.hasPrefix("문제") {
            return NSColor.systemRed
        }
        if line.hasPrefix("주의") {
            return NSColor.systemOrange
        }
        if line.hasPrefix("정상") {
            return NSColor.systemGreen
        }
        if line.hasPrefix("꺼짐") {
            return NSColor.systemGray
        }
        return NSColor.secondaryLabelColor
    }

    func applyLogText(_ lines: [String]) {
        guard let text = logTextView else { return }
        let key = lines.joined(separator: "\n")
        if key == lastLogTextKey, (text.textStorage?.length ?? 0) > 0 {
            return
        }
        lastLogTextKey = key
        let output = NSMutableAttributedString()
        let para = NSMutableParagraphStyle()
        para.lineSpacing = 3
        para.paragraphSpacing = 8
        para.lineBreakMode = .byWordWrapping
        let bodyFont = NSFont.systemFont(ofSize: 13)
        let headFont = NSFont.systemFont(ofSize: 13, weight: .semibold)
        let rows = lines.isEmpty ? ["아직 쉽게 풀어서 보여 줄 기록이 없습니다."] : lines
        for (index, line) in rows.enumerated() {
            if index > 0 {
                output.append(NSAttributedString(string: "\n"))
            }
            let color = logLineColor(line)
            let separator = line.range(of: " — ") ?? line.range(of: " · ")
            if let separator {
                let prefix = String(line[..<separator.lowerBound])
                let rest = String(line[separator.lowerBound...])
                output.append(NSAttributedString(
                    string: prefix,
                    attributes: [
                        .font: headFont,
                        .foregroundColor: color,
                        .paragraphStyle: para,
                    ]
                ))
                output.append(NSAttributedString(
                    string: rest,
                    attributes: [
                        .font: bodyFont,
                        .foregroundColor: NSColor.labelColor,
                        .paragraphStyle: para,
                    ]
                ))
            } else {
                output.append(NSAttributedString(
                    string: line,
                    attributes: [
                        .font: bodyFont,
                        .foregroundColor: NSColor.labelColor,
                        .paragraphStyle: para,
                    ]
                ))
            }
        }
        text.textStorage?.setAttributedString(output)
        text.scrollToEndOfDocument(nil)
    }

    func ensureRoomsWindow() {
        if roomsWindow != nil {
            return
        }
        let window = Chrome.operatorWindow(title: "단체 채팅방", size: NSSize(width: 760, height: 560), autosave: "AutoReplyRooms")
        let content = NSView()
        window.contentView = content

        let pipeline = PipelineView(frame: .zero)
        pipeline.translatesAutoresizingMaskIntoConstraints = false
        pipeline.heightAnchor.constraint(equalToConstant: 56).isActive = true
        roomsPipeline = pipeline

        let hint = Chrome.hint("동작·답변·긱뉴스·추가됨 칸의 상태를 눌러 켜고 끕니다. 답변이나 긱뉴스를 켜면 동작과 추가됨도 같이 켜집니다.")

        let filter = Chrome.searchField(
            placeholder: "단체 채팅방 검색",
            target: nil,
            action: nil,
            delegate: self
        )
        roomsFilterField = filter
        let addButton = NSButton(title: "추가", target: self, action: #selector(addRoomClicked))
        addButton.bezelStyle = .rounded
        addButton.translatesAutoresizingMaskIntoConstraints = false
        let deleteButton = NSButton(title: "삭제", target: self, action: #selector(removeRoomClicked))
        deleteButton.bezelStyle = .rounded
        deleteButton.translatesAutoresizingMaskIntoConstraints = false
        let toolbar = Chrome.hstack([filter, addButton, deleteButton], spacing: 8)

        let (scroll, table) = Chrome.table()
        table.delegate = self
        table.dataSource = self
        table.target = self
        table.action = #selector(roomsTableClicked(_:))
        for spec in [
            ("title", "제목", 280.0),
            ("members", "인원", 52.0),
            ("live", "동작", 64.0),
            ("reply", "답변", 64.0),
            ("geek", "긱뉴스", 72.0),
            ("catalog", "추가됨", 64.0),
        ] as [(String, String, CGFloat)] {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(spec.0))
            column.title = spec.1
            column.width = spec.2
            column.minWidth = 48
            column.headerCell.alignment = .center
            if let cell = column.dataCell as? NSTextFieldCell {
                cell.alignment = .center
            }
            table.addTableColumn(column)
        }
        roomsTable = table

        let stack = Chrome.vstack([pipeline, hint, toolbar, scroll], spacing: 10)
        Chrome.fill(stack, in: content)
        NSLayoutConstraint.activate([
            pipeline.widthAnchor.constraint(equalTo: stack.widthAnchor),
            hint.widthAnchor.constraint(equalTo: stack.widthAnchor),
            toolbar.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 280),
            filter.widthAnchor.constraint(greaterThanOrEqualToConstant: 220),
        ])
        window.initialFirstResponder = filter
        roomsWindow = window
    }


    func applyRoomsFilter() {
        rememberRoomsSelection()
        let query = (roomsFilterField?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if query.isEmpty {
            displayedChats = allChats
        } else {
            displayedChats = allChats.filter { $0.title.localizedCaseInsensitiveContains(query) }
        }
        roomsTable?.reloadData()
        restoreRoomsSelection()
    }

    func roomsFingerprint(_ chats: [AvailableChat]) -> String {
        chats.map {
            "\($0.chat_id):\($0.catalog):\($0.live):\($0.auto_reply):\($0.geeknews):\($0.members):\($0.title)"
        }.joined(separator: "|")
    }

    func rememberRoomsSelection() {
        let row = roomsTable?.selectedRow ?? -1
        if row >= 0, row < displayedChats.count {
            roomsSelectedChatId = displayedChats[row].chat_id
        }
    }

    func restoreRoomsSelection() {
        guard roomsSelectedChatId != 0,
              let index = displayedChats.firstIndex(where: { $0.chat_id == roomsSelectedChatId }) else {
            return
        }
        roomsTable?.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false)
    }

    func updateRoomsWindow(_ model: MenubarModel) {
        rememberRoomsSelection()
        let chats = model.available_chats ?? []
        let fingerprint = roomsFingerprint(chats)
        allChats = chats
        let inspected = Self.selectedRoom(in: model, preferred: roomsSelectedChatId != 0 ? roomsSelectedChatId : inspectedRoomId)
        roomsPipeline?.stages = inspected?.pipeline.stages ?? model.pipeline?.stages ?? []
        roomsPipeline?.level = inspected?.level ?? model.level
        roomsPipeline?.needsDisplay = true
        if fingerprint != lastRoomsFingerprint {
            lastRoomsFingerprint = fingerprint
            applyRoomsFilter()
        } else {
            restoreRoomsSelection()
        }
    }

    func reusedLabel(
        in tableView: NSTableView,
        column: String,
        text: String,
        font: NSFont,
        color: NSColor,
        alignment: NSTextAlignment = .center
    ) -> CenteredLabelCell {
        let ident = NSUserInterfaceItemIdentifier("cell.\(column)")
        let cell: CenteredLabelCell
        if let reused = tableView.makeView(withIdentifier: ident, owner: self) as? CenteredLabelCell {
            cell = reused
        } else {
            cell = CenteredLabelCell(frame: NSRect(x: 0, y: 0, width: 80, height: 32))
            cell.identifier = ident
        }
        cell.label.stringValue = text
        cell.label.font = font
        cell.label.textColor = color
        cell.label.alignment = .center
        (cell.label.cell as? NSTextFieldCell)?.alignment = .center
        cell.label.alignment = alignment
        (cell.label.cell as? NSTextFieldCell)?.alignment = alignment
        return cell
    }


    func reusedLamp(
        in tableView: NSTableView,
        column: String,
        on: Bool,
        color: NSColor,
        interactive: Bool = false,
        toolTip: String? = nil
    ) -> LampCell {
        let ident = NSUserInterfaceItemIdentifier("lamp.\(column)")
        let lamp: LampCell
        if let reused = tableView.makeView(withIdentifier: ident, owner: self) as? LampCell {
            lamp = reused
        } else {
            lamp = LampCell(frame: NSRect(x: 0, y: 0, width: 46, height: 28))
            lamp.identifier = ident
            lamp.autoresizingMask = [.width, .height]
        }
        lamp.on = on
        lamp.color = color
        lamp.interactive = interactive
        lamp.toolTip = toolTip
        lamp.needsDisplay = true
        lamp.window?.invalidateCursorRects(for: lamp)
        return lamp
    }

    func numberOfRows(in tableView: NSTableView) -> Int {

        if tableView === doctorTable {
            return displayedChecks.count
        }
        if tableView === jobsTable {
            return displayedJobs.count
        }
        if tableView === vectorTable {
            return displayedVectors.count
        }
        return displayedChats.count
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        if tableView === vectorTable {
            guard row >= 0, row < displayedVectors.count, let column = tableColumn else { return nil }
            let item = displayedVectors[row]
            switch column.identifier.rawValue {
            case "date":
                return reusedLabel(
                    in: tableView,
                    column: "date",
                    text: item.date.isEmpty ? "—" : item.date,
                    font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
                    color: NSColor.secondaryLabelColor
                )
            case "chat":
                return reusedLabel(
                    in: tableView,
                    column: "chat",
                    text: item.chat.isEmpty ? "전체" : item.chat,
                    font: NSFont.systemFont(ofSize: 11),
                    color: NSColor.secondaryLabelColor,
                    alignment: .left
                )
            case "user":
                return reusedLabel(
                    in: tableView,
                    column: "user",
                    text: item.user_name,
                    font: NSFont.systemFont(ofSize: 12, weight: .medium),
                    color: NSColor.labelColor,
                    alignment: .left
                )
            case "preview":
                let field = reusedLabel(
                    in: tableView,
                    column: "preview",
                    text: item.preview.isEmpty ? item.message : item.preview,
                    font: NSFont.systemFont(ofSize: 11),
                    color: NSColor.labelColor,
                    alignment: .left
                )
                field.toolTip = item.message
                return field
            case "topics":
                let field = reusedLabel(
                    in: tableView,
                    column: "topics",
                    text: item.topicsText.isEmpty ? "—" : item.topicsText,
                    font: NSFont.systemFont(ofSize: 11),
                    color: NSColor.secondaryLabelColor,
                    alignment: .left
                )
                field.toolTip = item.topicsText
                return field
            case "origin":
                return reusedLabel(
                    in: tableView,
                    column: "origin",
                    text: item.origin_label,
                    font: NSFont.systemFont(ofSize: 11),
                    color: NSColor.secondaryLabelColor
                )
            default:
                return nil
            }
        }
        if tableView === jobsTable {
            guard row >= 0, row < displayedJobs.count, let column = tableColumn else { return nil }
            let job = displayedJobs[row]
            switch column.identifier.rawValue {
            case "when":
                return reusedLabel(
                    in: tableView,
                    column: "when",
                    text: job.when.isEmpty ? "—" : job.when,
                    font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
                    color: NSColor.secondaryLabelColor
                )
            case "status":
                let field = reusedLabel(
                    in: tableView,
                    column: "status",
                    text: job.status_label,
                    font: NSFont.systemFont(ofSize: 12, weight: .medium),
                    color: NSColor.labelColor
                )
                field.toolTip = job.status
                return field
            case "reason":
                let detail = (job.detail ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                let fallback = job.leftover ? "남은 작업" : (job.reason_label.isEmpty ? (job.category_label ?? job.error_class) : job.reason_label)
                let field = reusedLabel(
                    in: tableView,
                    column: "reason",
                    text: detail.isEmpty ? (fallback.isEmpty ? "—" : fallback) : detail,
                    font: NSFont.systemFont(ofSize: 11),
                    color: NSColor.secondaryLabelColor,
                    alignment: .left
                )
                field.toolTip = job.event_id
                return field
            default:
                return nil
            }
        }
        if tableView === doctorTable {
            guard row >= 0, row < displayedChecks.count, let column = tableColumn else { return nil }
            let check = displayedChecks[row]
            switch column.identifier.rawValue {
            case "level":
                let label = check.level == "fail" ? "문제" : check.level == "warn" ? "주의" : check.level == "off" ? "꺼짐" : "정상"
                return reusedLabel(
                    in: tableView,
                    column: "level",
                    text: label,
                    font: NSFont.systemFont(ofSize: 11, weight: .semibold),
                    color: Palette.lamp(check.level == "fail" ? "err" : check.level == "warn" ? "warn" : check.level == "ok" ? "ok" : "off")
                )
            case "title":
                let field = reusedLabel(
                    in: tableView,
                    column: "title",
                    text: check.title,
                    font: NSFont.systemFont(ofSize: 12, weight: .medium),
                    color: NSColor.labelColor,
                    alignment: .left
                )
                field.toolTip = "\(check.title) · \(check.advice)"
                return field
            case "advice":
                let field = reusedLabel(
                    in: tableView,
                    column: "advice",
                    text: check.advice,
                    font: NSFont.systemFont(ofSize: 11),
                    color: NSColor.secondaryLabelColor,
                    alignment: .left
                )
                field.toolTip = check.advice
                return field
            case "heal":
                return reusedLabel(
                    in: tableView,
                    column: "heal",
                    text: check.heal.isEmpty ? "—" : "고칠 수 있음",
                    font: NSFont.systemFont(ofSize: 11, weight: .medium),
                    color: check.heal.isEmpty ? NSColor.tertiaryLabelColor : NSColor.systemBlue
                )
            default:
                return nil
            }
        }
        guard row >= 0, row < displayedChats.count, let column = tableColumn else { return nil }
        let chat = displayedChats[row]
        switch column.identifier.rawValue {
        case "title":
            let field = reusedLabel(
                in: tableView,
                column: "title",
                text: chat.title,
                font: NSFont.systemFont(ofSize: 12, weight: .medium),
                color: NSColor.labelColor,
                alignment: .left
            )
            field.toolTip = chat.title
            return field
        case "members":
            return reusedLabel(
                in: tableView,
                column: "members",
                text: chat.members > 0 ? String(chat.members) : "—",
                font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
                color: NSColor.secondaryLabelColor
            )
        case "live":
            return reusedLamp(
                in: tableView,
                column: "live",
                on: chat.live || chat.catalog,
                color: NSColor.systemGreen,
                interactive: true,
                toolTip: "눌러서 이 방 동작을 켜거나 끕니다"
            )
        case "reply":
            return reusedLamp(
                in: tableView,
                column: "reply",
                on: chat.auto_reply,
                color: NSColor.systemBlue,
                interactive: true,
                toolTip: "눌러서 자동 답변을 켜거나 끕니다"
            )
        case "geek":
            return reusedLamp(
                in: tableView,
                column: "geek",
                on: chat.geeknews,
                color: NSColor.systemOrange,
                interactive: true,
                toolTip: "눌러서 긱뉴스를 켜거나 끕니다"
            )
        case "catalog":
            return reusedLamp(
                in: tableView,
                column: "catalog",
                on: chat.catalog,
                color: NSColor.controlAccentColor,
                interactive: true,
                toolTip: "눌러서 목록에 넣거나 뺍니다"
            )
        default:
            return nil
        }
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        guard let table = notification.object as? NSTableView else { return }
        if table === vectorTable {
            fillVectorFormFromSelection()
            return
        }
        if table === jobsTable {
            let row = jobsTable?.selectedRow ?? -1
            if row >= 0, row < displayedJobs.count {
                selectedJobEventId = displayedJobs[row].event_id
            }
            updateJobsActions()
            return
        }
        guard table === roomsTable else { return }
        let row = roomsTable?.selectedRow ?? -1
        guard row >= 0, row < displayedChats.count else { return }
        roomsSelectedChatId = displayedChats[row].chat_id
    }

    func controlTextDidChange(_ obj: Notification) {
        guard let field = obj.object as? NSTextField else { return }
        if field === roomsFilterField {
            applyRoomsFilter()
        }
    }


    func ensureVectorWindow() {
        if vectorWindow != nil {
            return
        }
        let window = Chrome.operatorWindow(title: "대화 기억", size: NSSize(width: 980, height: 720), autosave: "AutoReplyVector")
        window.title = "대화 기억"
        window.delegate = self
        let content = NSView()
        window.contentView = content

        let hint = Chrome.hint("원문과 128차원 해시 임베딩을 같이 저장합니다. 설명 자료는 여러 사람에게 자세히 설명한 순간(사진+텍스트)을 누가/무엇을/어떻게/왜로 묶어 둔 검색 기억입니다. 주제별 지식은 카테고리 묶음이고, 탐색 프롬프트는 그 기억을 찾아 답장을 만들 때 모델에 들어가는 지시입니다. 채팅방을 비우면 모든 방이 나옵니다.")
        vectorHint = hint

        let chatLabel = Chrome.label("보기", size: 11, color: .secondaryLabelColor, lines: 1)
        chatLabel.setContentHuggingPriority(.required, for: .horizontal)
        let source = NSPopUpButton(frame: .zero, pullsDown: false)
        source.translatesAutoresizingMaskIntoConstraints = false
        source.addItems(withTitles: ["최연우 기억", "모든 대화", "주제별 지식", "설명 자료", "답장 기록", "말투·반응 통계", "탐색 프롬프트"])
        source.selectItem(at: max(vectorSourceKeys.firstIndex(of: vectorSourceKind) ?? 0, 0))
        source.target = self
        source.action = #selector(vectorSourceChanged)
        vectorSourceButton = source

        let topic = NSPopUpButton(frame: .zero, pullsDown: false)
        topic.translatesAutoresizingMaskIntoConstraints = false
        topic.addItem(withTitle: "모든 주제")
        topic.target = self
        topic.action = #selector(vectorTopicChanged)
        vectorTopicButton = topic

        let chat = NSTextField()
        chat.translatesAutoresizingMaskIntoConstraints = false
        chat.stringValue = ""
        chat.placeholderString = "비우면 모든 채팅방"
        chat.delegate = self
        chat.target = self
        chat.action = #selector(vectorSearchClicked)
        chat.widthAnchor.constraint(equalToConstant: 150).isActive = true
        vectorChatField = chat

        let search = Chrome.searchField(
            placeholder: "이름 또는 내용 검색",
            target: self,
            action: #selector(vectorSearchClicked),
            delegate: self,
            immediate: false
        )
        vectorSearchField = search
        let findButton = NSButton(title: "찾기", target: self, action: #selector(vectorSearchClicked))
        findButton.bezelStyle = .rounded
        findButton.translatesAutoresizingMaskIntoConstraints = false
        let toolbar = Chrome.hstack([chatLabel, source, topic, chat, search, findButton], spacing: 8)

        let summary = Chrome.label("기록 없음", size: 11, color: .secondaryLabelColor, lines: 1)
        vectorSummary = summary
        let prevButton = NSButton(title: "이전", target: self, action: #selector(vectorPrevClicked))
        prevButton.bezelStyle = .rounded
        prevButton.translatesAutoresizingMaskIntoConstraints = false
        let nextButton = NSButton(title: "다음", target: self, action: #selector(vectorNextClicked))
        nextButton.bezelStyle = .rounded
        nextButton.translatesAutoresizingMaskIntoConstraints = false
        vectorPrevButton = prevButton
        vectorNextButton = nextButton
        let pager = Chrome.hstack([summary, Chrome.spacer(), prevButton, nextButton], spacing: 8)

        let (scroll, table) = Chrome.table()
        table.delegate = self
        table.dataSource = self
        for spec in [
            ("date", "시각", 126.0),
            ("chat", "채팅방", 108.0),
            ("user", "이름", 80.0),
            ("preview", "내용", 280.0),
            ("topics", "주제", 120.0),
            ("origin", "출처", 88.0),
        ] as [(String, String, CGFloat)] {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(spec.0))
            column.title = spec.1
            column.width = spec.2
            column.minWidth = 72
            column.headerCell.alignment = .center
            if let cell = column.dataCell as? NSTextFieldCell {
                cell.alignment = .center
            }
            table.addTableColumn(column)
        }
        vectorTable = table

        let userLabel = Chrome.label("이름", size: 11, color: .secondaryLabelColor, lines: 1)
        let user = NSTextField()
        user.translatesAutoresizingMaskIntoConstraints = false
        user.placeholderString = "최연우"
        user.widthAnchor.constraint(equalToConstant: 140).isActive = true
        vectorUserField = user
        let dateLabel = Chrome.label("시각", size: 11, color: .secondaryLabelColor, lines: 1)
        let date = NSTextField()
        date.translatesAutoresizingMaskIntoConstraints = false
        date.placeholderString = "2026-08-20 20:30:00"
        date.widthAnchor.constraint(equalToConstant: 180).isActive = true
        vectorDateField = date
        let embedLabel = Chrome.label("임베딩", size: 11, color: .secondaryLabelColor, lines: 1)
        let embed = Chrome.label("원문에서 만든 128차원 해시 벡터", size: 11, color: .secondaryLabelColor, lines: 1)
        embed.font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        embed.isSelectable = true
        embed.lineBreakMode = .byTruncatingTail
        embed.toolTip = "검색에 쓰는 임베딩입니다. 원문을 저장하면 다시 계산됩니다."
        vectorEmbeddingField = embed
        let topicLabel = Chrome.label("주제", size: 11, color: .secondaryLabelColor, lines: 1)
        let topicsField = NSTextField()
        topicsField.translatesAutoresizingMaskIntoConstraints = false
        topicsField.placeholderString = "코인, 주식"
        topicsField.toolTip = "쉼표로 주제를 넣거나 비우면 내용에서 자동으로 묶습니다."
        topicsField.widthAnchor.constraint(equalToConstant: 180).isActive = true
        vectorTopicsField = topicsField
        let meta = Chrome.hstack([userLabel, user, dateLabel, date, topicLabel, topicsField, embedLabel, embed], spacing: 8)

        let messageLabel = Chrome.label("내용", size: 11, color: .secondaryLabelColor, lines: 1)
        messageLabel.setContentHuggingPriority(.required, for: .horizontal)
        let messageScroll = NSScrollView()
        messageScroll.translatesAutoresizingMaskIntoConstraints = false
        messageScroll.hasVerticalScroller = true
        messageScroll.borderType = .bezelBorder
        messageScroll.heightAnchor.constraint(equalToConstant: 120).isActive = true
        let message = NSTextView(frame: .zero)
        message.isRichText = false
        message.isEditable = true
        message.usesFindBar = false
        message.font = NSFont.systemFont(ofSize: 12)
        message.minSize = NSSize(width: 0, height: 120)
        message.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        message.isVerticallyResizable = true
        message.isHorizontallyResizable = false
        message.textContainer?.widthTracksTextView = true
        messageScroll.documentView = message
        vectorMessageView = message
        let editor = Chrome.hstack([messageLabel, messageScroll], spacing: 8)
        editor.alignment = .top

        let restoreButton = NSButton(title: "기본값 복원", target: self, action: #selector(vectorRestoreClicked))
        restoreButton.bezelStyle = .rounded
        restoreButton.translatesAutoresizingMaskIntoConstraints = false
        restoreButton.isHidden = true
        vectorRestoreButton = restoreButton
        let addButton = NSButton(title: "새로 쓰기", target: self, action: #selector(vectorNewClicked))
        addButton.bezelStyle = .rounded
        addButton.translatesAutoresizingMaskIntoConstraints = false
        vectorAddButton = addButton
        let saveButton = NSButton(title: "저장", target: self, action: #selector(vectorSaveClicked))
        saveButton.bezelStyle = .rounded
        saveButton.translatesAutoresizingMaskIntoConstraints = false
        vectorSaveButton = saveButton
        let deleteButton = NSButton(title: "삭제", target: self, action: #selector(vectorDeleteClicked))
        deleteButton.bezelStyle = .rounded
        deleteButton.translatesAutoresizingMaskIntoConstraints = false
        vectorDeleteButton = deleteButton
        let formActions = Chrome.hstack([addButton, saveButton, deleteButton, restoreButton, Chrome.spacer()], spacing: 8)

        let stack = Chrome.vstack(
            [hint, toolbar, pager, scroll, meta, editor, formActions],
            spacing: 10
        )
        Chrome.fill(stack, in: content)
        NSLayoutConstraint.activate([
            hint.widthAnchor.constraint(equalTo: stack.widthAnchor),
            toolbar.widthAnchor.constraint(equalTo: stack.widthAnchor),
            pager.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            meta.widthAnchor.constraint(equalTo: stack.widthAnchor),
            editor.widthAnchor.constraint(equalTo: stack.widthAnchor),
            formActions.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 220),
            search.widthAnchor.constraint(greaterThanOrEqualToConstant: 180),
        ])
        window.initialFirstResponder = search
        vectorWindow = window
    }


    func vectorChatName() -> String {
        return (vectorChatField?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func vectorChatOrDefault() -> String {
        let value = vectorChatName()
        if value.isEmpty || value == "전체" || value == "*" {
            return "부자멘토멘티"
        }
        return value
    }

    func currentVectorSource() -> String {
        let index = vectorSourceButton?.indexOfSelectedItem ?? vectorSourceKeys.firstIndex(of: vectorSourceKind) ?? 0
        if index >= 0, index < vectorSourceKeys.count {
            return vectorSourceKeys[index]
        }
        return vectorSourceKind.isEmpty ? "style" : vectorSourceKind
    }

    func currentVectorSourceTitle() -> String {
        let key = currentVectorSource()
        if let index = vectorSourceKeys.firstIndex(of: key), index < vectorSourceTitles.count {
            return vectorSourceTitles[index]
        }
        return "대화 기억"
    }

    func rebuildVectorTopicPopup(_ catalog: [VectorTopicStat]) {
        guard let button = vectorTopicButton else { return }
        let previous = vectorTopicKey
        button.removeAllItems()
        button.addItem(withTitle: currentVectorSource() == "prompts" ? "모든 종류" : "모든 주제")
        button.lastItem?.representedObject = ""
        for item in catalog {
            let title = item.count > 0 ? "\(item.label) (\(item.count))" : item.label
            button.addItem(withTitle: title)
            button.lastItem?.representedObject = item.key
        }
        if previous.isEmpty {
            button.selectItem(at: 0)
            vectorTopicKey = ""
            return
        }
        if let index = catalog.firstIndex(where: { $0.key == previous }) {
            button.selectItem(at: index + 1)
            vectorTopicKey = previous
            return
        }
        button.selectItem(at: 0)
        vectorTopicKey = ""
    }

    func updateVectorEditorMode() {
        let source = currentVectorSource()
        let promptMode = source == "prompts"
        let canWrite = source == "style" || source == "messages" || source == "topics" || promptMode
        vectorAddButton?.isEnabled = canWrite
        vectorSaveButton?.isEnabled = canWrite
        vectorRestoreButton?.isHidden = !promptMode
        vectorRestoreButton?.isEnabled = promptMode
        vectorUserField?.isEditable = canWrite
        vectorDateField?.isEditable = canWrite
        vectorTopicsField?.isEditable = canWrite
        vectorMessageView?.isEditable = canWrite
        vectorTopicButton?.isEnabled = source != "replies" && source != "profiles"
        let row = vectorTable?.selectedRow ?? -1
        let item = (row >= 0 && row < displayedVectors.count) ? displayedVectors[row] : nil
        if let item {
            vectorDeleteButton?.isEnabled = item.canDelete
        } else {
            vectorDeleteButton?.isEnabled = source == "replies" || source == "profiles" ? false : canWrite
        }
        if promptMode {
            vectorUserField?.placeholderString = "검색 기억 사용"
            vectorDateField?.placeholderString = "사용"
            vectorTopicsField?.placeholderString = "지시"
            vectorEmbeddingField?.stringValue = "검색된 대화 기억과 함께 모델에 들어가는 지시입니다."
        } else if source == "references" {
            vectorUserField?.placeholderString = "설명한 사람"
            vectorDateField?.placeholderString = "2026-08-20 20:30:00"
            vectorTopicsField?.placeholderString = "코인, 주식"
            vectorEmbeddingField?.stringValue = "누가 무엇을 어떻게 왜 설명했는지 정리한 검색 기억입니다."
        } else {
            vectorUserField?.placeholderString = "최연우"
            vectorDateField?.placeholderString = "2026-08-20 20:30:00"
            vectorTopicsField?.placeholderString = "코인, 주식"
        }
    }

    func loadVectorReport(_ extra: [String]) -> VectorReport? {
        let timeout: TimeInterval = extra.contains("vector-list") ? (extra.contains("references") ? 45 : 20) : 8
        guard let data = runPython(extra, timeout: timeout) else { return nil }
        return try? JSONDecoder().decode(VectorReport.self, from: data)
    }

    func refreshVectorList() {
        ensureVectorWindow()
        let query = (vectorSearchField?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        vectorSourceKind = currentVectorSource()
        vectorSourceStyle = vectorSourceKind == "style"
        var extra = ["--action", "vector-list", "--vector-offset", String(max(vectorOffset, 0)), "--vector-source", vectorSourceKind]
        let chat = vectorChatName()
        if !chat.isEmpty && chat != "전체" && chat != "*" {
            extra.append(contentsOf: ["--vector-chat", chat])
        }
        if !query.isEmpty {
            extra.append(contentsOf: ["--vector-query", query])
        }
        if !vectorTopicKey.isEmpty && vectorSourceKind != "replies" && vectorSourceKind != "profiles" {
            extra.append(contentsOf: ["--vector-topic", vectorTopicKey])
        }
        vectorLoadToken += 1
        let token = vectorLoadToken
        if (vectorSummary?.stringValue ?? "").isEmpty {
            vectorSummary?.stringValue = "기억을 불러오는 중…"
        }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let report = self?.loadVectorReport(extra)
            DispatchQueue.main.async {
                guard let self, token == self.vectorLoadToken else { return }
                self.applyVectorReport(report)
            }
        }
    }

    func applyVectorReport(_ report: VectorReport?) {
        guard let report else {
            vectorSummary?.stringValue = "기억 저장소를 읽지 못했습니다."
            displayedVectors = []
            vectorPrevButton?.isEnabled = false
            vectorNextButton?.isEnabled = false
            vectorTable?.reloadData()
            updateVectorEditorMode()
            return
        }
        displayedVectors = report.rows
        let pageOffset = max(report.offset ?? vectorOffset, 0)
        vectorOffset = pageOffset
        let start = report.total == 0 ? 0 : pageOffset + (report.count == 0 ? 0 : 1)
        let end = pageOffset + report.count
        let room = report.chat.isEmpty ? "모든 채팅방" : report.chat
        let memory = report.memory
        if let memory {
            lastVectorFingerprint = memory.fingerprint
        }
        rebuildVectorTopicPopup(report.topics ?? [])
        let kind = currentVectorSourceTitle()
        let topicLabel: String
        if vectorSourceKind == "topics", vectorTopicKey.isEmpty {
            topicLabel = "주제 목록"
        } else if !vectorTopicKey.isEmpty {
            topicLabel = (report.topics ?? []).first(where: { $0.key == vectorTopicKey })?.label ?? vectorTopicKey
        } else {
            topicLabel = "전체 주제"
        }
        vectorSummary?.stringValue = "\(kind) · \(topicLabel) · \(room) · \(start)–\(end) / \(report.total)건"
        vectorPageSize = max(report.limit ?? 200, 1)
        vectorPrevButton?.isEnabled = pageOffset > 0
        vectorNextButton?.isEnabled = report.truncated
        vectorTable?.reloadData()
        if selectedVectorId > 0, let index = displayedVectors.firstIndex(where: { $0.id == selectedVectorId }) {
            vectorTable?.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false)
        }
        updateVectorEditorMode()
    }

    func applyVectorStatus(_ memory: VectorMemory?) {
        if let memory {
            lastVectorFingerprint = memory.fingerprint
        }
        let current = vectorSummary?.stringValue ?? ""
        if current.contains("기억을 불러오는") || current.isEmpty {
            vectorSummary?.stringValue = Self.vectorStatusLine(memory)
        }
    }

    static func vectorStatusLine(_ memory: VectorMemory?) -> String {
        guard let memory, memory.ok else {
            return "최연우 기억 없음"
        }
        let count = MenuPanelView.compact(memory.style_total ?? memory.total)
        switch memory.state {
        case "live":
            return "최연우 기억 \(count)건 · 방금 갱신"
        case "idle":
            let when = (memory.last_date ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if when.isEmpty {
                return "최연우 기억 \(count)건 · 최신"
            }
            return "최연우 기억 \(count)건 · 최신 \(when)"
        case "stale":
            return "최연우 기억 \(count)건 · 갱신 멈춤"
        default:
            return "최연우 기억 없음"
        }
    }

    static func vectorCompactStatusLine(_ memory: VectorMemory?) -> String {
        guard let memory, memory.ok else {
            return ""
        }
        return MenuPanelView.compact(memory.style_total ?? memory.total)
    }

    static func vectorStatusColor(_ memory: VectorMemory?) -> NSColor {
        switch memory?.state {
        case "live":
            return NSColor.systemGreen
        case "idle":
            return NSColor.secondaryLabelColor
        case "stale":
            return NSColor.systemOrange
        default:
            return NSColor.tertiaryLabelColor
        }
    }

    func fillVectorFormFromSelection() {
        let row = vectorTable?.selectedRow ?? -1
        guard row >= 0, row < displayedVectors.count else {
            selectedVectorId = 0
            selectedVectorChat = ""
            selectedVectorKey = ""
            updateVectorEditorMode()
            return
        }
        let item = displayedVectors[row]
        if item.kindValue == "topic" {
            let topic = (item.topics?.first ?? item.row_key ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !topic.isEmpty {
                vectorTopicKey = topic
                selectedVectorId = 0
                selectedVectorKey = ""
                vectorOffset = 0
                refreshVectorList()
                return
            }
        }
        selectedVectorId = item.id
        selectedVectorChat = item.chat
        selectedVectorKey = item.row_key ?? ""
        vectorUserField?.stringValue = item.user_name
        vectorDateField?.stringValue = item.date
        vectorMessageView?.string = item.message
        vectorTopicsField?.stringValue = item.topicsText
        let embedding = item.vector_preview?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if item.kindValue == "reply" {
            let bits = [item.status_label, item.decision_label, item.category_label, item.reason_label]
                .compactMap { value -> String? in
                    let text = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                    return text.isEmpty ? nil : text
                }
            vectorEmbeddingField?.stringValue = bits.isEmpty ? "답장 기록" : bits.joined(separator: " · ")
        } else if item.kindValue == "profile" {
            vectorEmbeddingField?.stringValue = item.origin_label
        } else if item.kindValue == "prompt" {
            let state = item.chat.isEmpty ? "사용" : item.chat
            vectorEmbeddingField?.stringValue = "탐색 프롬프트 · \(state) · \(item.origin_label)"
        } else if item.kindValue == "reference" {
            let why = (item.category_label ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let how = item.origin_label.trimmingCharacters(in: .whitespacesAndNewlines)
            let bits = [how, why, item.status_label].filter { !($0 ?? "").isEmpty }
            vectorEmbeddingField?.stringValue = bits.isEmpty ? "설명 자료" : bits.compactMap { $0 }.joined(separator: " · ")
        } else {
            vectorEmbeddingField?.stringValue = embedding.isEmpty ? "원문에서 만든 128차원 해시 벡터" : embedding
        }
        vectorEmbeddingField?.toolTip = embedding
        updateVectorEditorMode()
    }

    @objc func vectorSourceChanged() {
        vectorSourceKind = currentVectorSource()
        vectorSourceStyle = vectorSourceKind == "style"
        vectorTopicKey = ""
        selectedVectorId = 0
        selectedVectorKey = ""
        vectorOffset = 0
        refreshVectorList()
    }

    @objc func vectorTopicChanged() {
        let selected = vectorTopicButton?.selectedItem?.representedObject as? String ?? ""
        vectorTopicKey = selected
        selectedVectorId = 0
        vectorOffset = 0
        refreshVectorList()
    }

    @objc func vectorSearchClicked() {
        selectedVectorId = 0
        vectorOffset = 0
        refreshVectorList()
    }

    @objc func vectorPrevClicked() {
        vectorOffset = max(0, vectorOffset - vectorPageSize)
        refreshVectorList()
    }

    @objc func vectorNextClicked() {
        vectorOffset += vectorPageSize
        refreshVectorList()
    }

    @objc func vectorNewClicked() {
        let source = currentVectorSource()
        guard source == "style" || source == "messages" || source == "topics" || source == "prompts" else {
            vectorSummary?.stringValue = "이 목록은 새로 쓸 수 없습니다."
            return
        }
        selectedVectorId = 0
        selectedVectorKey = ""
        vectorTable?.deselectAll(nil)
        if source == "prompts" {
            vectorUserField?.stringValue = "새 지시"
            vectorDateField?.stringValue = "사용"
            vectorTopicsField?.stringValue = "지시"
            vectorMessageView?.string = ""
            selectedVectorChat = "사용"
            vectorEmbeddingField?.stringValue = "검색된 대화 기억과 함께 모델에 들어가는 지시입니다."
        } else {
            vectorUserField?.stringValue = "최연우"
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "ko_KR")
            formatter.timeZone = TimeZone(identifier: "Asia/Seoul")
            formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
            vectorDateField?.stringValue = formatter.string(from: Date())
            vectorMessageView?.string = ""
            vectorTopicsField?.stringValue = ""
            selectedVectorChat = vectorChatOrDefault()
            vectorEmbeddingField?.stringValue = "저장하면 원문에서 128차원 해시 임베딩을 다시 만듭니다."
        }
        vectorWindow?.makeFirstResponder(vectorMessageView)
        updateVectorEditorMode()
    }

    @objc func vectorRestoreClicked() {
        guard currentVectorSource() == "prompts" else { return }
        let payload: [String: Any] = ["source": "prompts", "restore_prompts": true]
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: data, encoding: .utf8) else { return }
        vectorSummary?.stringValue = "복원하는 중…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let ok = self?.loadVectorReport(["--vector-upsert", json]) != nil
            DispatchQueue.main.async {
                guard let self else { return }
                guard ok else {
                    self.vectorSummary?.stringValue = "기본값을 복원하지 못했습니다."
                    return
                }
                self.selectedVectorId = 0
                self.selectedVectorKey = ""
                self.refreshVectorList()
                self.vectorSummary?.stringValue = "탐색 프롬프트 기본값을 복원했습니다."
            }
        }
    }

    @objc func vectorSaveClicked() {
        let source = currentVectorSource()
        guard source == "style" || source == "messages" || source == "topics" || source == "prompts" else {
            vectorSummary?.stringValue = "이 목록은 저장할 수 없습니다."
            return
        }
        let user = (vectorUserField?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let date = (vectorDateField?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let message = (vectorMessageView?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let topics = (vectorTopicsField?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !user.isEmpty, !message.isEmpty else {
            vectorSummary?.stringValue = source == "prompts" ? "이름과 프롬프트를 입력하세요." : "이름과 내용을 입력하세요."
            return
        }
        var payload: [String: Any] = [
            "source": source,
            "chat": source == "prompts" ? (date.isEmpty ? "사용" : date) : (selectedVectorChat.isEmpty ? vectorChatOrDefault() : selectedVectorChat),
            "user_name": user,
            "message": message,
            "date": date,
        ]
        if selectedVectorId > 0 {
            payload["id"] = selectedVectorId
        }
        if source == "prompts", !selectedVectorKey.isEmpty {
            payload["row_key"] = selectedVectorKey
        }
        payload["topics"] = topics
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: data, encoding: .utf8) else { return }
        vectorSummary?.stringValue = "저장하는 중…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let report = self?.loadVectorReport(["--vector-upsert", json])
            DispatchQueue.main.async {
                guard let self else { return }
                guard let report else {
                    self.vectorSummary?.stringValue = "저장하지 못했습니다."
                    return
                }
                if let ident = report.id {
                    self.selectedVectorId = ident
                }
                self.vectorSearchField?.stringValue = ""
                self.refreshVectorList()
                self.vectorSummary?.stringValue = self.selectedVectorId > 0 ? "저장했습니다." : (self.vectorSummary?.stringValue ?? "저장했습니다.")
            }
        }
    }

    @objc func vectorDeleteClicked() {
        let row = vectorTable?.selectedRow ?? -1
        let item = row >= 0 && row < displayedVectors.count ? displayedVectors[row] : nil
        let ident = item?.id ?? selectedVectorId
        let key = item?.row_key ?? selectedVectorKey
        let kind = item?.kindValue ?? currentVectorSource()
        guard ident > 0 || !key.isEmpty else {
            vectorSummary?.stringValue = "삭제할 줄을 선택하세요."
            return
        }
        if kind == "topic" || kind == "profile" || kind == "prompt" && item?.canDelete == false || item?.canDelete == false {
            vectorSummary?.stringValue = "이 줄은 삭제할 수 없습니다."
            return
        }
        var deleteArgs = ["--vector-source", currentVectorSource()]
        if kind == "reply" {
            guard !key.isEmpty else {
                vectorSummary?.stringValue = "삭제할 줄을 선택하세요."
                return
            }
            deleteArgs.append(contentsOf: ["--vector-delete-key", key])
        } else {
            guard ident > 0 else {
                vectorSummary?.stringValue = "삭제할 줄을 선택하세요."
                return
            }
            deleteArgs.append(contentsOf: ["--vector-delete", String(ident)])
        }
        let chat = vectorChatName()
        if !chat.isEmpty && chat != "전체" && chat != "*" {
            deleteArgs.append(contentsOf: ["--vector-chat", chat])
        }
        if !vectorTopicKey.isEmpty {
            deleteArgs.append(contentsOf: ["--vector-topic", vectorTopicKey])
        }
        vectorSummary?.stringValue = "삭제하는 중…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let ok = self?.loadVectorReport(deleteArgs) != nil
            DispatchQueue.main.async {
                guard let self else { return }
                guard ok else {
                    self.vectorSummary?.stringValue = "삭제하지 못했습니다."
                    return
                }
                self.selectedVectorId = 0
                self.selectedVectorKey = ""
                self.vectorUserField?.stringValue = ""
                self.vectorDateField?.stringValue = ""
                self.vectorTopicsField?.stringValue = ""
                self.vectorMessageView?.string = ""
                self.refreshVectorList()
                self.vectorSummary?.stringValue = "삭제했습니다."
            }
        }
    }

    @objc func addRoomClicked() {
        let row = roomsTable?.selectedRow ?? -1
        guard row >= 0, row < displayedChats.count else { return }
        let chat = displayedChats[row]
        upsertRoomFlags(
            chat,
            autoReply: chat.catalog ? chat.auto_reply : true,
            geeknews: chat.catalog ? chat.geeknews : true
        )
    }

    @objc func removeRoomClicked() {
        let row = roomsTable?.selectedRow ?? -1
        guard row >= 0, row < displayedChats.count else { return }
        removeRoomFromCatalog(displayedChats[row])
    }

    @objc func roomsTableClicked(_ sender: Any) {
        guard let table = roomsTable else { return }
        let row = table.clickedRow
        let col = table.clickedColumn
        guard row >= 0, row < displayedChats.count, col >= 0, col < table.tableColumns.count else { return }
        let ident = table.tableColumns[col].identifier.rawValue
        let chat = displayedChats[row]
        switch ident {
        case "live":
            toggleRoomLive(chat)
        case "catalog":
            toggleRoomCatalog(chat)
        case "reply":
            upsertRoomFlags(chat, autoReply: !chat.auto_reply, geeknews: chat.geeknews)
        case "geek":
            upsertRoomFlags(chat, autoReply: chat.auto_reply, geeknews: !chat.geeknews)
        default:
            break
        }
    }

    func applyOptimisticChat(_ chat: AvailableChat) {
        if let index = allChats.firstIndex(where: { $0.chat_id == chat.chat_id }) {
            allChats[index] = chat
        }
        if let index = displayedChats.firstIndex(where: { $0.chat_id == chat.chat_id }) {
            displayedChats[index] = chat
        }
        roomsSelectedChatId = chat.chat_id
        lastRoomsFingerprint = roomsFingerprint(allChats)
        roomsTable?.reloadData()
        restoreRoomsSelection()
    }

    func applyCatalogSnapshot(_ data: Data?) {
        if let data, let model = try? JSONDecoder().decode(MenubarModel.self, from: data) {
            apply(model)
            return
        }
        refresh()
    }

    func removeRoomFromCatalog(_ chat: AvailableChat) {
        rememberRoomsSelection()
        applyOptimisticChat(chat.updating(catalog: false, autoReply: false, geeknews: false))
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--catalog-delete", String(chat.chat_id)], timeout: 12)
            DispatchQueue.main.async { self?.applyCatalogSnapshot(data) }
        }
    }

    func toggleRoomCatalog(_ chat: AvailableChat) {
        if chat.catalog {
            removeRoomFromCatalog(chat)
            return
        }
        upsertRoomFlags(chat, autoReply: chat.auto_reply, geeknews: chat.geeknews)
    }

    func toggleRoomLive(_ chat: AvailableChat) {
        if chat.live {
            return
        }
        if chat.catalog {
            removeRoomFromCatalog(chat)
            return
        }
        upsertRoomFlags(
            chat,
            autoReply: chat.auto_reply || !chat.geeknews,
            geeknews: chat.geeknews
        )
    }

    func upsertRoomFlags(_ chat: AvailableChat, autoReply: Bool, geeknews: Bool) {
        let payload: [String: Any] = [
            "chat_id": chat.chat_id,
            "auto_reply": autoReply,
            "geeknews": geeknews,
        ]
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: data, encoding: .utf8) else { return }
        rememberRoomsSelection()
        applyOptimisticChat(chat.updating(catalog: true, autoReply: autoReply, geeknews: geeknews))
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--catalog-upsert", json], timeout: 12)
            DispatchQueue.main.async { self?.applyCatalogSnapshot(data) }
        }
    }

    static func unavailableDoctor() -> DoctorReport {
        DoctorReport(
            ok: false,
            action: "doctor",
            privacy: "content_redacted",
            level: "red",
            primary_code: "snapshot_unavailable",
            healable: [],
            healed: [],
            checks: [
                DoctorCheck(
                    code: "snapshot_unavailable",
                    level: "fail",
                    title: "상태 스냅샷",
                    detail: "code=snapshot_unavailable",
                    advice: "상태를 읽지 못했습니다. 메뉴바는 자동 실행을 재시작하지 않습니다. 잠시 후 다시 점검해 보세요.",
                    heal: ""
                )
            ],
            health: nil
        )
    }

    static func unavailableModel() -> MenubarModel {
        MenubarModel(
            schema_version: 3,
            privacy: "content_redacted",
            level: "red",
            primary_code: "snapshot_unavailable",
            codes: ["snapshot_unavailable"],
            menu_lines: ["level=red", "codes=snapshot_unavailable"],
            notifications: [],
            watermark: nil,
            open_jobs: 0,
            sent: 0,
            skipped: 0,
            delivery_unknown: 0,
            geeknews_slots: [],
            geeknews_newest_id: nil,
            skip_reasons: [],
            journal: [],
            log_lines: [],
            log_summary: "문제 — 상태를 읽지 못했습니다. 잠시 후 다시 열어 보세요.",
            log_display: ["문제 — 상태를 읽지 못했습니다. 잠시 후 다시 열어 보세요."],
            pipeline: PipelineModel(active_index: nil, event_id: "none", outcome: "none", stages: []),
            rooms: [],
            available_chats: [],
            health: [
                "watchdog": "err",
                "supervisor": "err",
                "ax": "err",
                "worker": "err",
                "model": "err",
            ],
            vector_memory: nil,
            reply_model: nil,
            image_reply_model: nil,
            reply_model_providers: nil
        )
    }

    static func statusImage(level: String, stages: [PipelineStage]) -> NSImage {
        let size = NSSize(width: 18, height: 14)
        let image = NSImage(size: size, flipped: false) { _ in
            let states = Dictionary(uniqueKeysWithValues: stages.map { ($0.id, $0.state) })
            let count = CGFloat(max(PipelineView.labels.count, 1))
            let padX: CGFloat = 0.4
            let padY: CGFloat = 2
            let gap: CGFloat = 1.05
            let usable = size.width - padX * 2
            let barW = max((usable - gap * (count - 1)) / count, 1.15)
            let barH = size.height - padY * 2
            _ = level
            for (index, spec) in PipelineView.labels.enumerated() {
                let state = states[spec.id] ?? "idle"
                let fill = Palette.stage(state)
                fill.setFill()
                let x = padX + CGFloat(index) * (barW + gap)
                let tick = NSRect(x: x, y: padY, width: barW, height: barH)
                NSBezierPath(roundedRect: tick, xRadius: 0.7, yRadius: 0.7).fill()
            }
            return true
        }
        image.isTemplate = false
        return image
    }
}

let config = parseConfig(CommandLine.arguments)
let app = NSApplication.shared
let delegate = AppDelegate(config: config)
app.setActivationPolicy(.accessory)
app.delegate = delegate
app.run()
