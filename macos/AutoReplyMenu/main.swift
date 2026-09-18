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
    // 자동 선택은 문자열 추정이 아니라 이 필드로 판단한다.
    var auto_selected: Bool? = nil
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
    // 저장(stored)과 준비(prepared)를 분리해 알려 준다.
    let stored: Bool?
    let prepared: Bool?
    let needs_prepare: Bool?
    // 이미지 변경을 확인할 때 쓰는 저장된 이미지 모델. 답변 목록(model)과 구분한다.
    let image_model: String?
    let image_label: String?
    let image_provider: String?
    // 폴백 사슬 저장 응답. 구버전 파이썬은 이 필드를 보내지 않는다.
    let fallback_models: [String]?
    let fallback_source: String?
    let fallback_defaults: [String]?
    let fallback_max: Int?
    let fallback_revision: Int64?
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


struct OnDeviceHardware: Decodable {
    struct Spec: Decodable {
        let chip: String
        let cores: Int
        let memory_gb: Double
        let is_apple_silicon: Bool
    }
    struct Recommendation: Decodable {
        let primary_engine: String
        let available_engines: [String]
        let recommended_model: String
        let recommended_quant: String
        let reason: String
    }
    let hardware: Spec
    let recommendation: Recommendation
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
    // 폴백 사슬: 코어가 계산한 목록/출처/기본값/상한. 구버전은 보내지 않는다.
    let reply_model_fallbacks: ReplyModelFallbacks?
    // 답변 기록 창: 코어(CLI)가 만든 턴 기록. 훅이 없는 구버전 스냅샷에는 없다.
    let reply_receipts: ReplyReceipts?
    let ondevice_hardware: OnDeviceHardware?
}

/// 모델 설정 창의 폴백 섹션이 그대로 그리는 값. 판단은 코어(파이썬)가 한다.
struct ReplyModelFallbacks: Decodable {
    let models: [String]?
    let source: String?
    let defaults: [String]?
    let max: Int?
    // 저장 파일의 mtime(초). 화면은 이 값으로 "내 저장보다 먼저 읽은 조회"와
    // "그 뒤 다른 곳에서 바뀐 값"을 가른다(2026-09-15).
    let revision: Int64?
    // 저장된 모델 중 지금 카탈로그에 없는 것과, 지금 답변 모델. 화면은 표시만
    // 하고 판단하지 않는다 — 저장이 끝난 뒤 목록이 바뀌면 사용자가 고칠 수
    // 없는 줄이 남기 때문이다(2026-09-15).
    let unknown: [String]?
    let primary: String?
}

/// 답변 기록 창이 그대로 그리는 턴 기록. 문구와 판단은 코어가 만든다.
struct ReplyReceipts: Decodable {
    let rooms: [ReplyReceiptRoom]?
    let limit: Int?
    let read_at: Double?
    let missing: Bool?

    /// 서명용 지문. 읽은 시각은 2초마다 바뀌므로 쓰지 않는다.
    var fingerprint: String {
        (rooms ?? []).map { room in
            let turns = (room.receipts ?? []).map {
                "\($0.event_id ?? ""):\($0.outcome ?? ""):\($0.needs_attention == true ? 1 : 0)"
            }.joined(separator: ",")
            return "\(room.chat_id ?? ""):\(room.count ?? 0):\(turns)"
        }.joined(separator: ";")
    }
}

struct ReplyReceiptRoom: Decodable {
    let chat_id: String?
    let chat: String?
    let count: Int?
    let needs_attention: Int?
    let lines: [String]?
    let receipts: [TurnReceipt]?
    let missing: Bool?
}

struct TurnReceipt: Decodable {
    let event_id: String?
    let recorded_at: String?
    let display_time: String?
    let clock: String?
    let chat: String?
    let outcome: String?
    let outcome_text: String?
    let decision: String?
    let reason_code: String?
    let reason_text: String?
    let retrieval_state: String?
    let retrieval_text: String?
    let model: String?
    let model_attempts: [TurnAttempt]?
    let fallback_used: Bool?
    let fallback_code: String?
    // 코어가 48자로 줄인 미리보기만 쓴다. 원문(preview)은 창에서 그리지 않는다.
    let preview_text: String?
    let needs_attention: Bool?
    let summary: String?
    let detail: [String]?
}

struct TurnAttempt: Decodable {
    let order: Int?
    let model: String?
    let result: String?
    let result_text: String?
    let reason_code: String?
    let reason_text: String?
    let included: Int?
}

/// 기록 창의 한 행. 방 정보를 함께 들고 있어야 새로고침 뒤에도 같은 턴을
/// 계속 고른 채로 있을 수 있다.
struct ReceiptRow {
    let key: String
    let roomId: String
    let room: String
    let time: String
    let outcome: String
    let outcomeText: String
    let reasonText: String
    let retrievalText: String
    let reply: String
    let attention: Bool
    let summary: String
    let detail: [String]
    let sortKey: String
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
    /// 비어 있지 않으면 창을 그리지 않고 레이아웃 감사만 하고 끝난다.
    var layoutAudit: String = ""
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
        case "green": return "정상 작동"
        case "yellow": return "처리 중"
        case "red": return "확인 필요"
        case "off": return "꺼짐"
        default: return "대기"
        }
    }

    static func caption(code: String) -> String {
        switch code {
        case "ready": return "자동 답변 대기 중"
        case "processing": return "답변 작성 중…"
        case "ax_window_missing": return "카카오톡 창 열기 필요"
        case "leftover_occupancy": return "이전 작업 정리 중"
        case "worker_unhealthy": return "답변 프로그램 점검 중"
        case "supervisor_unhealthy": return "서비스 점검 중"
        case "watchdog_unhealthy": return "감시 서비스 점검 중"
        case "fenced": return "일시 대기 중"
        case "delivery_unknown": return "전송 상태 확인 중"
        case "bake_digest_mismatch": return "업데이트 적용 중"
        case "snapshot_unavailable": return "연결 확인 중"
        case "model_temporarily_unavailable": return "AI 모델 연결 중"
        case "auto_reply_off": return "자동 답변 꺼짐"
        case "service_off": return "서비스 꺼짐"
        case "journal_error": return "기록 점검 중"
        case "identity_mismatch": return "계정 확인 중"
        case "circuit_open": return "잠시 후 자동 재시도"
        case "preflight_failed": return "연결 상태 점검 중"
        case "stopped_unclean": return "정상 재시작 대기 중"
        case "db_watch_exited": return "대화 감시 대기 중"
        case "python_pin_missing": return "필수 구성 요소 확인 필요"
        case "kakaotalk_stopped": return "카카오톡 실행 필요"
        case "launch_agent_missing": return "백그라운드 설정 확인"
        case "scheduled_waiting": return "자연스러운 전송 대기 중"
        case "session_not_ready": return "준비 중…"
        case "session_unenrolled": return "채팅방 등록 대기"
        case "session_bake_stale": return "새 버전 적용 대기"
        case "session_bake_current": return "최신 상태"
        case "reply_model_default": return "기본 AI 모델 사용"
        default: return code
        }
    }
}

enum Chrome {
    static func operatorWindow(title: String, size: NSSize, autosave: String) -> NSWindow {
        operatorWindow(title: title, size: size, autosave: autosave, minimum: nil)
    }

    /// 창 하나를 만든다. ``minimum``은 내용이 담기려면 필요한 최소 크기다.
    ///
    /// 지금까지 모든 창이 560x380으로 줄어들 수 있었는데, 그 값은 기록 창에
    /// 너무 작다: 여섯 열의 최소 너비 합이 624pt라 가로 스크롤 없이 마지막
    /// 열을 볼 수 없었고, 스크롤 260 + 상세 208에 머리말까지 더하면 세로도
    /// 690pt가 필요했다. 창마다 실제로 필요한 값을 받아 그 아래로는 줄어들지
    /// 않게 한다 (2026-09-16).
    static func operatorWindow(
        title: String,
        size: NSSize,
        autosave: String,
        minimum: NSSize?
    ) -> NSWindow {
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
        let floor = NSSize(width: 560, height: 380)
        window.minSize = NSSize(
            width: min(max(minimum?.width ?? floor.width, floor.width), size.width),
            height: min(max(minimum?.height ?? floor.height, floor.height), size.height)
        )
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

    static func hint(_ text: String, size: CGFloat = 11) -> NSTextField {
        label(text, size: size, color: .secondaryLabelColor)
    }

    /// 값이 비면 스스로 자리를 비우는 상태 줄. 스택에 빈 줄이 남아 창 아래에
    /// 이유 없는 여백이 생기는 것을 막는다 (2026-09-16).
    static func statusLabel(size: CGFloat = 12, lines: Int = 2) -> AutoHidingLabel {
        let field = AutoHidingLabel(labelWithString: "")
        field.font = NSFont.systemFont(ofSize: size)
        field.textColor = .secondaryLabelColor
        field.translatesAutoresizingMaskIntoConstraints = false
        field.maximumNumberOfLines = lines
        field.cell?.wraps = true
        field.lineBreakMode = .byWordWrapping
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        field.isHidden = true
        return field
    }

    static func summary(_ text: String) -> NSTextField {
        // 창의 머리말은 코어가 만든 문장이라 길이가 가변적이다. 한 줄로
        // 고정하면 "…건너뜀 4013건"처럼 끝이 잘려 무슨 상태인지 못 읽는다
        // (2026-09-16).
        label(text, size: 15, weight: .semibold, lines: 2)
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
        let scroll = TableScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .noBorder
        scroll.drawsBackground = false
        scroll.hasHorizontalScroller = false
        let table = NSTableView()
        table.rowHeight = 32
        // 줄무늬는 행이 있는 자리에만 그린다. AppKit의 교차 배경은 표의
        // 전체 프레임을 칠하므로, 행이 두 개뿐인 창에서 남은 300pt가 회색
        // 줄무늬 여섯 줄로 채워졌다. 표가 아니라 행이 자기 배경을 칠하면
        // 없는 줄은 아무것도 그리지 않는다 (2026-09-17).
        table.usesAlternatingRowBackgroundColors = false
        table.backgroundColor = .clear
        table.allowsMultipleSelection = false
        table.allowsEmptySelection = true
        table.gridStyleMask = []
        table.intercellSpacing = NSSize(width: 8, height: 2)
        table.headerView = NSTableHeaderView()
        // 열 폭은 TableScrollView가 창 폭에 맞춰 정한다. AppKit의 자동 배분을
        // 켜 두면 둘이 서로 다른 값을 밀어 넣어 열이 매번 조금씩 달라진다
        // (2026-09-16).
        table.columnAutoresizingStyle = .noColumnAutoresizing
        if #available(macOS 11.0, *) {
            table.style = .inset
        }
        scroll.documentView = table
        // 카드와 같은 둥근 모서리를 쓴다. 표의 줄무늬가 각진 사각형으로
        // 창 끝까지 차면 카드 사이에서 혼자 튄다 (2026-09-16).
        scroll.wantsLayer = true
        scroll.layer?.cornerRadius = 8
        scroll.layer?.masksToBounds = true
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
        alignment: NSTextAlignment = .left
    ) {
        let column = DesignedColumn(identifier: NSUserInterfaceItemIdentifier(id))
        column.title = title
        column.width = width
        column.minWidth = minWidth
        column.designedWidth = width
        column.resizingMask = [.autoresizingMask, .userResizingMask]
        // 머리글은 본문과 같은 쪽에 붙는다. 머리글만 가운데로 두면 왼쪽
        // 정렬된 본문 위에서 제목이 칸 가운데에 떠 보인다 (2026-09-16).
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
        // 가로 줄은 세로로 늘어나지 않는다. 기본 허깅(250)으로 두면 바깥
        // 세로 스택이 남는 높이를 이 줄에 몰아 주어, 작업 목록 창의 필터
        // 줄이 216pt로 늘어나고 그 아래 96pt가 빈 띠로 남았다. 남는 높이는
        // 표(스크롤)가 먹어야 한다 (2026-09-16).
        stack.setContentHuggingPriority(.defaultHigh, for: .vertical)
        return stack
    }

    /// 창 아래 동작 단추 줄.
    ///
    /// 한때 이 줄은 단추를 같은 폭으로 나눠 창 끝까지 채웠다. 왼쪽 절반만
    /// 차지하던 시절을 고치려던 것인데, 그러면 드물게 쓰는 관리 단추가
    /// 자주 쓰는 단추와 같은 무게로 보인다. 지금은 단추가 제 글자만큼만
    /// 차지하고 줄은 왼쪽 정렬한다. 뒤의 늘어나는 칸이 남은 폭을 먹어
    /// 줄 자체는 창 폭을 그대로 쓴다 (2026-09-16, 6 Pro 지적).
    ///
    /// 압축 저항은 낮게 둔다. 창을 최소 크기까지 줄였을 때 단추가 잘리는
    /// 대신 글자가 줄어드는 편이 낫다.
    static func actionRow(_ views: [NSView], spacing: CGFloat = 8) -> NSStackView {
        for view in views {
            view.setContentHuggingPriority(.defaultHigh, for: .horizontal)
            view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        }
        let stack = hstack(views + [spacer()], spacing: spacing)
        stack.distribution = .fill
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

    static func card(_ child: NSView, padding: CGFloat = 12) -> CardView {
        CardView(content: child, padding: padding)
    }

    /// 내용이 창보다 길어질 수 있는 창을 스크롤 가능하게 감싼다. 폴백 모델을
    /// 여러 개 넣으면 세로로 길어지므로 잘려 보이지 않게 한다.
    @discardableResult
    static func scrollable(
        _ child: NSView,
        in parent: NSView,
        insets: NSEdgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
    ) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        scroll.borderType = .noBorder
        let document = FlippedContainerView()
        document.translatesAutoresizingMaskIntoConstraints = false
        document.addSubview(child)
        scroll.documentView = document
        parent.addSubview(scroll)
        NSLayoutConstraint.activate([
            scroll.leadingAnchor.constraint(equalTo: parent.leadingAnchor, constant: insets.left),
            scroll.trailingAnchor.constraint(equalTo: parent.trailingAnchor, constant: -insets.right),
            scroll.topAnchor.constraint(equalTo: parent.topAnchor, constant: insets.top),
            scroll.bottomAnchor.constraint(equalTo: parent.bottomAnchor, constant: -insets.bottom),
            document.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
            child.leadingAnchor.constraint(equalTo: document.leadingAnchor),
            child.trailingAnchor.constraint(equalTo: document.trailingAnchor),
            child.topAnchor.constraint(equalTo: document.topAnchor),
            child.bottomAnchor.constraint(equalTo: document.bottomAnchor),
        ])
        return scroll
    }
}

/// 스크롤 문서 뷰. 위에서 아래로 쌓이도록 좌표계를 뒤집는다.
final class FlippedContainerView: NSView {
    override var isFlipped: Bool { true }
}

/// 설계할 때 정한 폭을 기억하는 열.
///
/// 창 폭이 바뀔 때마다 이 값에서 비율을 다시 계산한다. 지금 폭을 기준으로
/// 삼으면 이미 줄어든 폭을 또 기준으로 삼아 열이 매번 조금씩 더 줄어들고,
/// 결국 모두 최소 폭에 붙어 버린다 (2026-09-16).
final class DesignedColumn: NSTableColumn {
    var designedWidth: CGFloat = 0
}

/// 표의 한 행. 짝수 행에만 옅은 배경을 칠한다.
///
/// 표의 내장 교차 배경(`usesAlternatingRowBackgroundColors`)은 표의 프레임
/// 전체를 칠한다. 행이 두 개뿐인 창에서는 남은 300pt가 회색 줄무늬 여섯 줄로
/// 채워져, 목록이 비었다는 안내보다 그 줄무늬가 먼저 눈에 들어왔다. 행이
/// 자기 배경을 칠하면 없는 줄은 아무것도 그리지 않는다 (2026-09-17).
final class StripedRowView: NSTableRowView {
    var isOdd = false

    override func drawBackground(in dirtyRect: NSRect) {
        super.drawBackground(in: dirtyRect)
        // 짝수 줄을 비워 두면 그 줄은 창 배경과 구분되지 않는다. 줄이 하나뿐인
        // 창에서는 마지막 줄 아래가 통째로 빈 띠가 되어, 감사가 작업 목록
        // 창에서 25pt를 재고 실패했다. 모든 줄에 옅은 바탕을 깔고 홀수 줄만
        // 조금 더 진하게 해 줄무늬는 그대로 남긴다 (2026-09-18).
        NSColor.labelColor.withAlphaComponent(isOdd ? 0.07 : 0.04).setFill()
        dirtyRect.fill()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }
}

/// 표를 담는 스크롤 뷰. 문서 뷰(표)의 폭을 자기 폭에 맞춘다.
///
/// AppKit은 문서 뷰가 "폭을 따라가겠다"고 말했을 때만 폭을 맞춰 준다. 그
/// 표시가 없으면 창을 넓혀도 표가 처음 폭에 머물러 오른쪽에 빈 자리가 남고,
/// 창을 좁히면 마지막 열이 클립 뷰 밖으로 밀려나 영영 보이지 않는다. 기록
/// 창에서 "답변" 열이 사라지던 것이 이것이었다: 표는 976pt인데 클립 뷰는
/// 655pt였다 (2026-09-16).
final class TableScrollView: NSScrollView {
    override func tile() {
        super.tile()
        guard !hasHorizontalScroller, let table = documentView as? NSTableView else { return }
        let width = contentView.bounds.width
        guard width > 0 else { return }
        if abs(table.frame.width - width) > 0.5 {
            var frame = table.frame
            frame.size.width = width
            table.frame = frame
        }
        fitColumns(of: table, to: width)
    }

    /// 열 폭을 창 폭에 맞춘다.
    ///
    /// 표가 넓어지거나 좁아져도 열은 처음 정한 폭에 머물러 있었다. 그래서
    /// 창을 넓히면 오른쪽에 아무것도 없는 자리가 남고, 창을 좁히면 마지막
    /// 열이 클립 뷰 밖으로 밀려나 잘렸다(기록 창의 "답변" 열).
    ///
    /// AppKit의 균등 자동 배분은 표의 폭이 바뀔 때만 도는데, 문서 뷰의
    /// 폭을 바꾸는 것은 우리쪽이라 여기서 직접 나눈다. 열의 최소 폭은 지키고
    /// 남는 폭은 정한 비율대로 나눈다 (2026-09-16).
    private func fitColumns(of table: NSTableView, to width: CGFloat) {
        let columns = table.tableColumns.compactMap { $0 as? DesignedColumn }
        guard !columns.isEmpty, columns.count == table.tableColumns.count else { return }
        let spacing = table.intercellSpacing.width * CGFloat(max(columns.count - 1, 0))
        // 표는 열 폭과 별개로 좌우에 자기 여백을 둔다(현재 macOS의 inset
        // 양식에서 20pt). 이 값을 빼지 않으면 열을 표 폭에 꽉 채울 때마다
        // 표가 그만큼 넓어져 클립 뷰 밖으로 나간다. 여백은 첫 열이 시작하는
        // 자리에서 읽는다: 열 폭이 아니라 표 양식이 정하는 값이라 언제 재도
        // 같다 (2026-09-16).
        let padding = max(table.rect(ofColumn: 0).minX, 0) * 2
        let designed = columns.map { max($0.designedWidth, $0.minWidth, 1) }
        let minimums = columns.map { max($0.minWidth, 1) }
        let floor = minimums.reduce(0, +)
        let room = max(width - spacing - padding, floor)
        let natural = designed.reduce(0, +)
        var widths = designed.map { max($0 / natural * room, 0) }
        // 최소 폭보다 작아진 열은 최소 폭으로 올리고, 올린 만큼을 나머지
        // 열에서 덜어 낸다. 한 번에 안 맞으면 몇 번 더 돈다.
        for _ in 0..<4 {
            for index in widths.indices {
                widths[index] = max(widths[index], minimums[index])
            }
            let extra = widths.reduce(0, +) - room
            if extra <= 0.5 { break }
            let pool = zip(widths, minimums).reduce(0) { $0 + max($1.0 - $1.1, 0) }
            if pool <= 0 { break }
            for index in widths.indices {
                widths[index] -= extra * max(widths[index] - minimums[index], 0) / pool
            }
        }
        for index in widths.indices {
            widths[index] = max(widths[index], minimums[index])
        }
        if let last = widths.indices.last {
            widths[last] = max(minimums[last], widths[last] + (room - widths.reduce(0, +)))
        }
        guard widths.reduce(0, +) <= room + 1 else { return }
        for (column, value) in zip(columns, widths) where abs(column.width - value) > 0.5 {
            column.width = value
        }
        trimColumnOverflow(of: table, columns: columns, minimums: minimums, to: width)
        // 열 폭을 바꾸면 표가 열 합에 맞춰 다시 자라난다. 클립 뷰 폭은 창이
        // 정한 값이므로 마지막에 되돌린다 (2026-09-16).
        if abs(table.frame.width - width) > 0.5 {
            var frame = table.frame
            frame.size.width = width
            table.frame = frame
        }
    }

    /// 열이 표의 오른쪽 끝을 넘어간 만큼을 덜어 낸다.
    ///
    /// 폭을 계산으로 정해도 표는 그대로 그리지 않는다: AppKit이 열마다 시작
    /// 자리를 픽셀에 맞춰 반올림해서, 여섯 열이면 마지막 열이 3pt까지 오른쪽
    /// 으로 밀린다. 클립 뷰가 그만큼 잘라 내므로 "답변" 열의 오른쪽 끝이
    /// 조금씩 사라졌다. 표에게 마지막 열이 어디서 끝나는지 물어보고 넘친
    /// 만큼을 덜어 내면, 열 수가 달라져도 계산이 아니라 실제 눈금으로 맞춘
    /// 셈이 된다 (2026-09-16).
    private func trimColumnOverflow(
        of table: NSTableView,
        columns: [DesignedColumn],
        minimums: [CGFloat],
        to width: CGFloat
    ) {
        guard columns.count == minimums.count, let lastIndex = columns.indices.last else { return }
        // 반올림은 열마다 다시 일어나므로 한 번 덜어 내고 끝내지 않고 확인한다.
        for _ in 0..<2 {
            let overshoot = table.rect(ofColumn: lastIndex).maxX - width
            if overshoot <= 0.5 { return }
            var left = overshoot
            // 마지막 열부터 덜어 내고, 거기서 더 못 덜면 여유가 있는 열에서
            // 가져온다. 어느 열도 최소 폭 아래로는 내려가지 않는다.
            for index in columns.indices.reversed() {
                if left <= 0.5 { break }
                let slack = columns[index].width - minimums[index]
                if slack <= 0.5 { continue }
                let taken = min(slack, left)
                columns[index].width -= taken
                left -= taken
            }
            if left > 0.5 { return }
        }
    }
}

/// 빈 글자면 스스로 숨는 라벨. 스택 안에서 빈 줄이 자리를 차지해 창 아래에
/// 쓸모 없는 여백을 만드는 것을 막는다.
final class AutoHidingLabel: NSTextField {
    override var stringValue: String {
        didSet { updateVisibility() }
    }

    override var attributedStringValue: NSAttributedString {
        didSet { updateVisibility() }
    }

    private func updateVisibility() {
        let empty = stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        if isHidden != empty {
            isHidden = empty
            superview?.needsLayout = true
        }
    }
}

/// 목록이 비었을 때 표 자리를 대신 채우는 판. 표를 숨기기만 하면 그 자리가
/// 빈 띠로 남아 창 아래가 통째로 비어 보인다. 같은 자리를 차지하면서 왜
/// 비었는지 적는다 (2026-09-16).
///
/// ``compact``는 아이콘 없이 한 줄만 두는 모양이다. 540pt 창에서 아이콘과
/// 설명까지 갖춘 판이 403pt, 창 높이의 4분의 3을 차지해 화면이 통째로
/// 안내문이 되었다. 목록이 비었다는 사실은 한 줄이면 충분하다
/// (2026-09-16, 6 Pro 지적).
final class EmptyStateView: NSView {
    private let symbol = NSImageView()
    private let title = NSTextField(labelWithString: "")
    private let detail = NSTextField(labelWithString: "")

    var titleText: String {
        get { title.stringValue }
        set { title.stringValue = newValue }
    }

    var detailText: String {
        get { detail.stringValue }
        set {
            detail.stringValue = newValue
            detail.isHidden = newValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    init(symbolName: String, compact: Bool = false) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 8
        applyColors()

        symbol.translatesAutoresizingMaskIntoConstraints = false
        symbol.image = NSImage(systemSymbolName: symbolName, accessibilityDescription: nil)
        symbol.symbolConfiguration = NSImage.SymbolConfiguration(
            pointSize: compact ? 15 : 26,
            weight: .regular
        )
        symbol.contentTintColor = NSColor.tertiaryLabelColor
        symbol.imageScaling = .scaleProportionallyUpOrDown
        symbol.isHidden = compact

        title.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        title.textColor = NSColor.secondaryLabelColor
        title.alignment = .center
        title.maximumNumberOfLines = 2
        title.lineBreakMode = .byWordWrapping
        title.translatesAutoresizingMaskIntoConstraints = false

        detail.font = NSFont.systemFont(ofSize: 11)
        detail.textColor = NSColor.tertiaryLabelColor
        detail.alignment = .center
        detail.maximumNumberOfLines = 3
        detail.lineBreakMode = .byWordWrapping
        detail.translatesAutoresizingMaskIntoConstraints = false

        let column = NSStackView(views: [symbol, title, detail])
        column.orientation = .vertical
        column.alignment = .centerX
        column.spacing = compact ? 4 : 8
        column.translatesAutoresizingMaskIntoConstraints = false
        addSubview(column)
        NSLayoutConstraint.activate([
            column.centerXAnchor.constraint(equalTo: centerXAnchor),
            column.centerYAnchor.constraint(equalTo: centerYAnchor),
            column.leadingAnchor.constraint(greaterThanOrEqualTo: leadingAnchor, constant: 24),
            column.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor, constant: -24),
            symbol.heightAnchor.constraint(equalToConstant: compact ? 0 : 30),
            // 한 줄짜리 판은 내용 높이만 차지한다. 늘리면 아이콘을 뺀 자리가
            // 그대로 빈 여백으로 남아 없앤 것과 같아진다 (2026-09-16).
            heightAnchor.constraint(
                greaterThanOrEqualToConstant: compact ? 0 : 120
            ),
        ])
        if compact {
            // 세로 스택이 남는 높이를 이 판에 몰아 주지 않게 한다.
            setContentHuggingPriority(.defaultHigh, for: .vertical)
        }
    }

    required init?(coder: NSCoder) {
        return nil
    }

    private func applyColors() {
        layer?.backgroundColor = NSColor.labelColor.withAlphaComponent(0.04).cgColor
        layer?.borderWidth = 1
        layer?.borderColor = NSColor.separatorColor.withAlphaComponent(0.4).cgColor
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        applyColors()
    }
}

/// 카드 한 장. 내용을 padding만큼 안쪽에 두고, 카드 자신은 내용 높이에 맞춰
/// 늘어난다.
///
/// 예전에는 NSBox(.custom)를 썼는데, 세로 스택 안에서 NSBox는 내용이 아니라
/// 자기 intrinsic 높이(0)를 주장해 카드가 납작하게 붕괴했다. 그러면 배경과
/// 테두리가 안 보이고, 내용이 카드 밖으로 흘러넘쳐 창마다 위아래 간격이
/// 들쭉날쭉해진다 (2026-09-16).
final class CardView: NSView {
    let padding: CGFloat
    private let content: NSView

    init(content: NSView, padding: CGFloat) {
        self.content = content
        self.padding = padding
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 10
        layer?.borderWidth = 1
        applyColors()
        content.translatesAutoresizingMaskIntoConstraints = false
        addSubview(content)
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: leadingAnchor, constant: padding),
            content.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -padding),
            content.topAnchor.constraint(equalTo: topAnchor, constant: padding),
            content.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -padding),
        ])
    }

    required init?(coder: NSCoder) {
        return nil
    }

    override var isFlipped: Bool { true }

    private func applyColors() {
        let isDark = effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        if isDark {
            // 다크 모드: 윈도우 배경(#171717)과 대비를 이루는 부드러운 다크 서피스와 은은한 분리선
            layer?.backgroundColor = NSColor(white: 0.16, alpha: 0.75).cgColor
            layer?.borderColor = NSColor.separatorColor.withAlphaComponent(0.25).cgColor
        } else {
            // 라이트 모드: 순백 배경 위 은은한 컨트롤 배경과 얇고 정돈된 테두리
            layer?.backgroundColor = NSColor.controlBackgroundColor.withAlphaComponent(0.70).cgColor
            layer?.borderColor = NSColor.separatorColor.withAlphaComponent(0.35).cgColor
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        applyColors()
    }
}

/// 레이아웃 감사: 창 안의 모든 뷰를 돌며 프레임과 빈 여백을 수치로 남긴다.
/// 창을 눈으로 볼 수 없는 환경에서 "불필요한 간격"을 찾기 위한 도구다.
enum LayoutAudit {
    static let visibleTextLimit = 400

    /// 크기를 바꾼 뒤 실제 배치가 끝날 때까지 잠깐 런루프를 돌린다.
    ///
    /// 감사는 창 크기를 프로그램으로 바꾸므로, 실제 창에서 일어나는 마무리
    /// 단계가 통째로 빠진다. 표의 열 폭이 새 폭에 맞춰 다시 나뉘는 일이
    /// 그중 하나다. 런루프를 한 바퀴 돌리지 않으면 표가 예전 폭을 그대로
    /// 들고 있어, 열이 넘치는지 아닌지를 잘못 재게 된다 (2026-09-16).
    static func settle(_ seconds: TimeInterval = 0.05) {
        RunLoop.current.run(until: Date().addingTimeInterval(seconds))
    }

    /// 캡처를 불투명 배경 위에 합성해 저장한다.
    ///
    /// cacheDisplay는 뷰가 스스로 그리지 않은 자리를 투명하게 남긴다. 그대로
    /// 저장하면 창 여백과 스택 사이가 알파 0이 되어, 여는 프로그램에 따라
    /// 흰 판이나 검은 판으로 보인다. 그러면 글자 대비와 색을 판단할 수 없어
    /// 정상 UI를 결함으로 오해한다 (2026-09-16, 6 Pro 지적).
    ///
    /// 배경색은 인자로 받은 appearance로 해석한다. 라이트 캡처에 어두운
    /// 배경을 깔면 대비를 잘못 재게 된다.
    @discardableResult
    static func writeCapture(
        _ view: NSView,
        appearance: NSAppearance,
        to url: URL
    ) -> Bool {
        let bounds = view.bounds
        guard bounds.width > 1, bounds.height > 1 else { return false }
        view.appearance = appearance
        view.layoutSubtreeIfNeeded()
        settle()
        guard let rep = view.bitmapImageRepForCachingDisplay(in: bounds) else {
            return false
        }
        view.cacheDisplay(in: bounds, to: rep)
        // 배경을 먼저 깔고 그 위에 그리면 안 된다. cacheDisplay가 만든
        // 비트맵은 뷰가 스스로 그리지 않은 자리가 알파 0이라, 그리는 순간
        // 깔아 둔 배경까지 지워진다. 그러면 캡처가 통째로 투명해져 여는
        // 프로그램에 따라 흰 판으로 보이고, 감사가 정상 UI를 결함으로
        // 읽는다. 그래서 뷰를 먼저 그리고 배경을 destinationOver로 덮는다.
        // 이 합성은 이미 칠해진 픽셀은 그대로 두고 빈 자리만 채운다
        // (2026-09-16, 6 Pro 지적).
        guard let out = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: rep.pixelsWide,
            pixelsHigh: rep.pixelsHigh,
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .calibratedRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ) else { return false }
        out.size = bounds.size
        guard let context = NSGraphicsContext(bitmapImageRep: out) else { return false }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        rep.draw(in: NSRect(origin: .zero, size: bounds.size))
        context.compositingOperation = .destinationOver
        appearance.performAsCurrentDrawingAppearance {
            NSColor.windowBackgroundColor.setFill()
            NSRect(origin: .zero, size: bounds.size).fill()
        }
        context.compositingOperation = .sourceOver
        NSGraphicsContext.restoreGraphicsState()
        guard let data = out.representation(using: .png, properties: [:]) else {
            return false
        }
        return (try? data.write(to: url)) != nil
    }

    /// 이 모드에서 창 배경이 실제로 어떤 색으로 풀리는지 잰다.
    ///
    /// 라이트와 다크가 같은 픽셀로 저장되면 감사가 두 모드를 재지 못한
    /// 것이다. 그 사실을 JSON이 스스로 말하게 한다 (2026-09-16).
    static func mode(name: String, appearance: NSAppearance?) -> [String: Any] {
        guard let appearance else { return ["requested": name, "resolved": "없음"] }
        var rgb = "?"
        appearance.performAsCurrentDrawingAppearance {
            if let color = NSColor.windowBackgroundColor.usingColorSpace(.sRGB) {
                rgb = String(
                    format: "#%02X%02X%02X",
                    Int((color.redComponent * 255).rounded()),
                    Int((color.greenComponent * 255).rounded()),
                    Int((color.blueComponent * 255).rounded())
                )
            }
        }
        return ["requested": name, "resolved": rgb]
    }

    static func collect(
        from root: NSView,
        window: String,
        path: String,
        windowSize: NSSize,
        into rows: inout [[String: Any]]
    ) {
        for (index, child) in root.subviews.enumerated() {
            let frame = child.frame
            let name = child.className
            var entry: [String: Any] = [
                "window": window,
                "path": "\(path)/\(name)#\(index)",
                "kind": name,
                "x": round(frame.origin.x * 100) / 100,
                "y": round(frame.origin.y * 100) / 100,
                "w": round(frame.width * 100) / 100,
                "h": round(frame.height * 100) / 100,
                "hidden": child.isHidden,
                "windowW": round(windowSize.width * 100) / 100,
                "windowH": round(windowSize.height * 100) / 100,
            ]
            // 창 기준 좌표(위에서부터). 뷰마다 좌표계가 뒤집혀 있어 로컬
            // 프레임만으로는 실제 순서를 알 수 없다.
            let inWindow = child.convert(child.bounds, to: nil)
            entry["winTop"] = round((windowSize.height - inWindow.maxY) * 100) / 100
            entry["winLeft"] = round(inWindow.minX * 100) / 100
            // 정렬 사각형도 함께 담는다. AppKit은 스택 안에서 프레임이 아니라
            // 정렬 사각형으로 자리를 잡는다. 프레임끼리 비교하면 단추가 옆
            // 단추와 6pt 겹치고 스택 밖으로 7pt 나간 것처럼 보인다. macOS 14
            // 러너에서만 나오던 그 오탐이 이것이었다 (2026-09-16).
            if let parent = child.superview {
                let align = parent.convert(child.alignmentRect(forFrame: frame), to: nil)
                entry["alignLeft"] = round(align.minX * 100) / 100
                entry["alignTop"] = round((windowSize.height - align.maxY) * 100) / 100
                entry["alignW"] = round(align.width * 100) / 100
                entry["alignH"] = round(align.height * 100) / 100
            }
            // 오른쪽·아래로 창을 넘는지, 좌상단이 창 밖인지 표시한다.
            // 부모 기준 frame을 창 크기와 비교하면 중첩 뷰에서 오탐이 난다.
            // 창 기준 사각형 하나만 써서 네 방향을 모두 판단한다.
            if !child.isHidden {
                let overshootRight = inWindow.maxX - windowSize.width
                let overshootBottom = -inWindow.minY
                if overshootRight > 1 { entry["overRight"] = round(overshootRight * 100) / 100 }
                if overshootBottom > 1 { entry["overBottom"] = round(overshootBottom * 100) / 100 }
                if inWindow.minX < -1 { entry["underLeft"] = round(-inWindow.minX * 100) / 100 }
                if inWindow.maxY > windowSize.height + 1 {
                    entry["underTop"] = round((inWindow.maxY - windowSize.height) * 100) / 100
                }
            }
            if let field = child as? NSTextField {
                entry["text"] = String(field.stringValue.prefix(visibleTextLimit))
                entry["editable"] = field.isEditable
                // 글자가 잘리는지 보려면 실제로 필요한 폭과 가진 폭을 비교한다.
                let needed = field.attributedStringValue.size().width
                entry["needW"] = round(needed * 100) / 100
                entry["maxLines"] = field.maximumNumberOfLines
                if !field.stringValue.isEmpty {
                    // 여러 줄 라벨도 마지막 줄이 잘릴 수 있다. 줄 수만큼 나눠
                    // 담을 수 있는지 본다. 예전에는 한 줄짜리만 검사해서
                    // 머리말이 잘린 채로 통과했다 (2026-09-16).
                    let lines = CGFloat(max(field.maximumNumberOfLines, 1))
                    let capacity = frame.width * lines
                    let slack = frame.width - needed
                    if capacity - needed < 0 {
                        entry["clipped"] = round((needed - capacity) * 100) / 100
                    } else if slack < 0, field.maximumNumberOfLines <= 1 {
                        entry["clipped"] = round(-slack * 100) / 100
                    }
                }
            }
            if let button = child as? NSButton {
                // 어떤 단추가 잘렸는지 로그만 보고 알 수 있도록 제목을
                // 함께 담는다. 예전에는 빈 문자열만 남아 어느 단추인지
                // 구분할 수 없었다 (2026-09-16).
                entry["text"] = String(button.title.prefix(visibleTextLimit))
                let needed = button.attributedTitle.size().width + 24
                entry["needW"] = round(needed * 100) / 100
                if !button.title.isEmpty, frame.width < needed {
                    entry["clipped"] = round((needed - frame.width) * 100) / 100
                }
            }
            if let stack = child as? NSStackView {
                entry["spacing"] = stack.spacing
                entry["orientation"] = stack.orientation == .horizontal ? "h" : "v"
                entry["arranged"] = stack.arrangedSubviews.count
            }
            // 표의 열 폭을 담는다. 열 최소 너비의 합이 표에 주어진 폭보다
            // 크면 마지막 열이 표 밖으로 밀려나고, 가로 스크롤러가 없으면
            // 값을 읽을 방법이 아예 없다. 글자 잘림과 달리 툴팁으로도 복구할
            // 수 없어 자동 검사가 직접 봐야 한다 (2026-09-16).
            if let table = child as? NSTableView {
                entry["columns"] = table.tableColumns.map { column in
                    [
                        "id": column.identifier.rawValue,
                        "width": round(column.width * 100) / 100,
                        "minWidth": round(column.minWidth * 100) / 100,
                    ]
                }
                entry["intercellSpacing"] = table.intercellSpacing.width
                // 마지막 열의 오른쪽 끝. 표의 프레임은 열을 다 담고도 남는
                // 여백이 있어 클립 뷰보다 넓을 수 있다. 넓다는 것 자체는
                // 결함이 아니다(스크롤 뷰가 잘라 낸다). 결함은 열이 잘리는
                // 것이므로, 정말 잘리는지는 이 값으로 판단해야 한다
                // (2026-09-16).
                if let last = table.tableColumns.last {
                    entry["columnsLeft"] = round(table.rect(ofColumn: 0).minX * 100) / 100
                    entry["columnsRight"] = round(table.rect(ofColumn: table.tableColumns.count - 1).maxX * 100) / 100
                    entry["lastColumn"] = last.identifier.rawValue
                }
            }
            // 스크롤 뷰가 가로로 굴러가는지 담는다. 열 최소 너비의 합이 표에
            // 주어진 폭보다 클 때, 가로 스크롤이 있으면 마지막 열까지 볼 수
            // 있고 없으면 영영 못 본다. 검사가 그 둘을 가르려면 이 값이
            // 필요하다. 스크롤러의 프레임 모양으로 추측하면 감출 때
            // (autohidesScrollers) 크기가 0이 되어 방향을 알 수 없다
            // (2026-09-16).
            if let scroll = child as? NSScrollView {
                entry["hScroller"] = scroll.hasHorizontalScroller
            }
            // 신경망 보기가 실제로 뉴런을 그렸는지 잰다. 힘 배치가 뭉치면
            // 창이 비어 보이는데, 프레임만 봐서는 알 수 없다 (2026-09-16).
            if let graph = child as? KnowledgeGraphView {
                entry["graphNodes"] = graph.nodes.count
                entry["graphEdges"] = graph.edges.count
                if let spread = graph.drawnSpread {
                    entry["graphInk"] = [
                        "w": round(spread.width * 100) / 100,
                        "h": round(spread.height * 100) / 100,
                    ]
                }
            }
            if let box = child as? NSBox {
                entry["boxType"] = box.boxType.rawValue
                entry["corner"] = box.cornerRadius
                if let inner = box.contentView {
                    let padTop = inner.subviews.first.map { $0.frame.minY } ?? 0
                    let padLeft = inner.subviews.first.map { $0.frame.minX } ?? 0
                    let padBottom = inner.subviews.first.map { inner.bounds.height - $0.frame.maxY } ?? 0
                    let padRight = inner.subviews.first.map { inner.bounds.width - $0.frame.maxX } ?? 0
                    entry["innerPad"] = [
                        "top": round(padTop * 100) / 100,
                        "left": round(padLeft * 100) / 100,
                        "bottom": round(padBottom * 100) / 100,
                        "right": round(padRight * 100) / 100,
                    ]
                    entry["innerSize"] = [
                        "w": round(inner.bounds.width * 100) / 100,
                        "h": round(inner.bounds.height * 100) / 100,
                    ]
                }
            }
            if let card = child as? CardView {
                entry["card"] = true
                entry["padding"] = card.padding
                if let inner = card.subviews.first {
                    entry["innerPad"] = [
                        "top": round(inner.frame.minY * 100) / 100,
                        "left": round(inner.frame.minX * 100) / 100,
                        "bottom": round((card.bounds.height - inner.frame.maxY) * 100) / 100,
                        "right": round((card.bounds.width - inner.frame.maxX) * 100) / 100,
                    ]
                }
            }
            rows.append(entry)
            collect(
                from: child,
                window: window,
                path: "\(path)/\(name)#\(index)",
                windowSize: windowSize,
                into: &rows
            )
        }
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
        case "--layout-audit": config.layoutAudit = take()
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

    /// 점과 이름이 실제로 차지하는 높이.
    ///
    /// 예전에는 이 높이로 띠 높이(stripHeight)까지 계산해 메뉴 패널에
    /// 붙였다. 메뉴 패널이 자비스 코어로 바뀌면서 띠를 쓰는 창이 없어져
    /// 그 상수는 없앴다. 지금은 그리는 쪽이 높이를 직접 계산한다
    /// (2026-09-17).
    static let nodeRadius: CGFloat = 7
    static let nodeLabelHeight: CGFloat = 12
    static let contentHeight: CGFloat = nodeRadius * 2 + 5 + nodeLabelHeight

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
        let radius = Self.nodeRadius
        // 점과 이름을 세로 가운데에 둔다. 예전에는 높이의 40% 지점에 점을 두어
        // 위쪽에 아무것도 없는 띠가 남았다 (2026-09-16).
        let contentHeight = Self.contentHeight
        let nodeY = max((bounds.height - contentHeight) / 2 + radius, radius + 2)
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

/// One knowledge-graph node as the graph command reports it.
struct KnowledgeNode: Decodable {
    let id: String
    let label: String
    let category: String
    let description: String
    let facts: [String]
    let importance: Int
    let evidence: KnowledgeEvidence
    let updated_at: Int
}

/// Where a node or edge came from. A seed node has no message behind it yet.
struct KnowledgeEvidence: Decodable {
    let kind: String
    let source_event_ids: [String]
    let chat_id: String
    let confirmed_at: String?
    let retracted: Bool

    var grounded: Bool { kind == "ledger" }
}

struct KnowledgeEdge: Decodable {
    let source: String
    let relation: String
    let target: String
    let context: String
    let weight: Int
    let evidence: KnowledgeEvidence
}

struct KnowledgeGraphReport: Decodable {
    let ok: Bool
    let nodes: [KnowledgeNode]
    let edges: [KnowledgeEdge]
    let node_count: Int
    let edge_count: Int
    let grounded_nodes: Int
    /// 색인이 마지막으로 끝난 시각(유닉스 초). 화면이 "언제 기준인지"를
    /// 말할 수 있어야 한다 (2026-09-17).
    let indexed_at: Int?
    /// 색인된 방·주제 뉴런 수.
    let indexed_count: Int?
    /// 이 응답이 저장된 그림인지(=재색인이 뒤에서 도는 중인지).
    let stale: Bool?
}

/// Draws the knowledge graph the way a brain scan shows neurons and synapses:
/// a node is a cell body whose radius follows its importance, an edge is a
/// synapse whose thickness follows its weight, and the whole thing settles
/// with a small deterministic force simulation so the layout is stable across
/// refreshes instead of jumping every poll.
///
/// Deterministic matters here: the window redraws on a timer, and a random
/// layout would make every redraw look like a different graph.
final class KnowledgeGraphView: NSView {
    var nodes: [KnowledgeNode] = [] {
        didSet { rebuildLayoutUnlessApplyingSnapshot() }
    }
    var edges: [KnowledgeEdge] = [] {
        didSet { rebuildLayoutUnlessApplyingSnapshot() }
    }
    /// 스냅샷을 한 번에 넣는 동안에는 배치를 미룬다.
    ///
    /// nodes 와 edges 는 각자 didSet 으로 배치를 다시 계산한다. 스냅샷처럼
    /// 둘을 함께 바꾸면 settle() 이 두 번 돌아 341개 시냅스의 힘 계산이
    /// 그대로 두 배가 된다. 주석은 처음부터 "한 번에 적용한다"고 적혀
    /// 있었지만 실제로는 그렇지 않았다 (2026-09-17, 6 Pro 지적).
    private var applyingSnapshot = false

    private func rebuildLayoutUnlessApplyingSnapshot() {
        guard !applyingSnapshot else { return }
        rebuildLayout()
    }

    /// 뉴런과 시냅스를 한 번에 바꾼다.
    ///
    /// 따로 대입하면 didSet이 두 번 돌아 힘 배치가 두 번 계산되고,
    /// 그 사이 한 프레임 동안 새 뉴런과 옛 시냅스가 섞여 그려진다. 읽어
    /// 온 그래프는 언제나 통째로 바뀌므로 한 번에 적용한다
    /// (2026-09-17, 6 Pro 지적).
    func applySnapshot(nodes newNodes: [KnowledgeNode], edges newEdges: [KnowledgeEdge]) {
        let nodesChanged = newNodes.count != nodes.count
            || zip(newNodes, nodes).contains { $0.id != $1.id }
        let edgesChanged = newEdges.count != edges.count
            || zip(newEdges, edges).contains {
                $0.source != $1.source || $0.target != $1.target || $0.weight != $1.weight
            }
        guard nodesChanged || edgesChanged else { return }
        applyingSnapshot = true
        nodes = newNodes
        edges = newEdges
        applyingSnapshot = false
        // 두 값을 다 넣은 뒤 한 번만 배치한다.
        rebuildLayout()
    }
    var selectedNodeId: String?
    var onSelect: ((KnowledgeNode?) -> Void)?

    /// 드릴다운으로 확대해 들여다보는 뉴런.
    ///
    /// 뉴런을 누르면 그 뉴런과 이웃만 남기고 카메라가 부드럽게 다가간다.
    /// 341개 시냅스가 한 화면에 겹쳐 있으면 눌러도 무엇과 이어졌는지 읽을
    /// 수 없어, 고른 뉴런의 이웃을 화면 가득 펼치는 편이 훨씬 잘 읽힌다
    /// (2026-09-17, 사용자 지시).
    private var focusNodeId: String?
    /// 0이면 전체 그림, 1이면 포커스에 완전히 다가간 상태.
    private var focusProgress: Double = 0
    private var focusTimer: Timer?
    private var focusAnimationStart: Double = 0
    /// 확대 전환에 걸리는 시간(초).
    static let focusDuration: Double = 0.32
    /// 포커스 애니메이션의 프레임 상한. 코어와 같은 이유로 60fps를 쓰지 않는다.
    static let focusFramesPerSecond: Double = 60

    /// 자비스 코어와 같은 금색 계열 팔레트.
    ///
    /// 예전에는 이 창만 청록(teal)이었다. 같은 앱의 코어가 주황·금색인데
    /// 지식 그래프만 다른 색이면 한 앱이 아니라 두 앱처럼 보인다
    /// (2026-09-17, 사용자 지시).
    /// 자비스 코어가 쓰는 값과 같은 계열로 맞춘다. 코어의 밝은 금색은
    /// (1.0, 0.70, 0.26)이고 어두운 쪽은 (1.0, 0.62, 0.18)이다. 같은 앱
    /// 안에서 두 화면이 같은 빛을 쓰도록 그 값을 그대로 가져온다
    /// (2026-09-17, 사용자 지시).
    static let neuronGold = NSColor(calibratedRed: 1.0, green: 0.70, blue: 0.26, alpha: 1)
    static let neuronBrass = NSColor(calibratedRed: 0.72, green: 0.52, blue: 0.24, alpha: 1)
    static let synapseGold = NSColor(calibratedRed: 1.0, green: 0.78, blue: 0.36, alpha: 1)
    static let synapseBrass = NSColor(calibratedRed: 0.62, green: 0.48, blue: 0.26, alpha: 1)

    /// 뉴런마다 그릴 시냅스의 개수.
    ///
    /// 관계를 전부 그리면 가운데가 선밭이 되어 무엇이 무엇과 이어졌는지
    /// 읽을 수 없다. 자비스 코어가 뉴런마다 가까운 시냅스 셋만 그리는 것과
    /// 같은 이유다. 고른 뉴런에 붙은 시냅스는 하나도 빠뜨리지 않는다 —
    /// 눌러 놓고 이어진 것을 못 보면 그래프를 볼 이유가 없다
    /// (2026-09-17, 6 Pro 지적).
    static let synapsesPerNeuron = 4

    /// Settled positions, keyed by node id, so the same node keeps its place.
    private var positions: [String: CGPoint] = [:]
    private var velocities: [String: CGPoint] = [:]
    private var settled = false
    private var trackingArea: NSTrackingArea?
    private var hoveredId: String?

    override var isFlipped: Bool { true }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        wantsLayer = true
    }

    deinit {
        // 포커스 타이머는 뷰가 사라질 때 반드시 멈춘다. RunLoop가 타이머를
        // 붙들고 있으면 뷰가 해제된 뒤에도 콜백이 돌아 크래시로 이어진다
        // (2026-09-17).
        focusTimer?.invalidate()
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let area = trackingArea { removeTrackingArea(area) }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.mouseMoved, .mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        trackingArea = area
    }

    /// Radius from importance: an important concept is a bigger cell body.
    private func radius(_ node: KnowledgeNode) -> CGFloat {
        let scaled = 12.0 + CGFloat(max(0, min(100, node.importance))) * 0.16
        return scaled
    }

    /// Place new nodes on a ring and keep known nodes where they were, then
    /// relax the springs a bounded number of steps.
    private func rebuildLayout() {
        let width = max(bounds.width, 480)
        let height = max(bounds.height, 320)
        let center = CGPoint(x: width / 2, y: height / 2)
        let known = Set(nodes.map { $0.id })
        positions = positions.filter { known.contains($0.key) }
        velocities = velocities.filter { known.contains($0.key) }
        let missing = nodes.filter { positions[$0.id] == nil }
        if !missing.isEmpty {
            // Spread newcomers on a ring sized to the node count, ordered by
            // importance so the biggest bodies land near the middle ring.
            let ring = min(width, height) * 0.32
            let count = max(missing.count, 1)
            for (index, node) in missing.enumerated() {
                let angle = (2 * Double.pi * Double(index)) / Double(count)
                positions[node.id] = CGPoint(
                    x: center.x + CGFloat(cos(angle)) * ring,
                    y: center.y + CGFloat(sin(angle)) * ring
                )
                velocities[node.id] = .zero
            }
        }
        settled = false
        settle(width: width, height: height)
        needsDisplay = true
    }

    /// 결과를 캔버스에 펼친다.
    ///
    /// 힘 배치는 동그랗게 뭉치려는 성질이 있어, 넓고 낮은 캔버스에서는
    /// 가운데 작은 원만 그리고 좌우가 텅 빈 채로 남았다(잉크가 948pt 중
    /// 202pt). 시뮬레이션 좌표는 그대로 두고 그릴 때만 늘린다. 좌표를 직접
    /// 고치면 배치가 다시 돌 때마다 배율이 겹곱해져 뉴런이 창 밖으로 밀려난다
    /// (2026-09-16).
    /// 드릴다운에서 함께 보여 줄 이웃의 최대 개수.
    ///
    /// 허브 뉴런 하나는 이웃이 서른 개가 넘는다. 전부 남기면 확대해도
    /// 화면이 처음과 똑같아, 눌러도 달라지는 것이 없다. 굵은 시냅스부터
    /// 이만큼만 남기면 "이 뉴런은 무엇과 가까운가"가 한눈에 읽힌다
    /// (2026-09-17, 사용자 지시).
    static let focusNeighborLimit = 10

    /// 드릴다운 대상과 그 이웃. 확대했을 때 화면에 남길 뉴런들이다.
    ///
    /// 이웃은 양쪽 방향을 모두 본다. 한 방향만 보면 "A가 B를 가리킨다"만
    /// 있는 뉴런에서 B를 눌렀을 때 A가 사라져, 정작 이어져 있던 상대가
    /// 화면에서 빠진다 (2026-09-17).
    func focusGroup(around nodeId: String) -> [KnowledgeNode] {
        // 굵은 시냅스부터 센다. 같은 뉴런으로 가는 시냅스가 여럿이면
        // 가장 굵은 것 하나만 그 뉴런의 세기로 본다.
        var strongest: [String: Int] = [:]
        for edge in edges {
            let other: String
            if edge.source == nodeId {
                other = edge.target
            } else if edge.target == nodeId {
                other = edge.source
            } else {
                continue
            }
            strongest[other] = max(strongest[other] ?? 0, edge.weight)
        }
        var keep: Set<String> = [nodeId]
        let ranked = strongest.sorted { left, right in
            if left.value != right.value { return left.value > right.value }
            return left.key < right.key
        }
        for (id, _) in ranked.prefix(Self.focusNeighborLimit) {
            keep.insert(id)
        }
        return nodes.filter { keep.contains($0.id) }
    }

    private func fitTransform(
        nodes subset: [KnowledgeNode]
    ) -> (scaleX: CGFloat, scaleY: CGFloat, offsetX: CGFloat, offsetY: CGFloat) {
        // 실제 크기를 쓴다. 시뮬레이션의 최소 크기(480x320)를 그대로 쓰면
        // 260pt 높이의 창에서 배율이 그만큼 커져 뉴런이 창 밖으로 밀려난다
        // (2026-09-16).
        let width = bounds.width
        let height = bounds.height
        let identity = (scaleX: CGFloat(1), scaleY: CGFloat(1), offsetX: CGFloat(0), offsetY: CGFloat(0))
        guard subset.count > 1, width > 1, height > 1 else { return identity }
        // 그려지는 것은 중심이 아니라 그 둘레의 장식이다. 세포체 둘레에
        // 1.7배 광륜이 깔리고 이름표가 아래에 붙는데, 둘 다 화면 좌표에서
        // 고정 크기라 배율을 타지 않는다. 그래서 배율은 "장식을 뺀 나머지
        // 폭"으로 정하고, 장식은 노드마다 제 값을 따로 뺀다.
        //
        // 예전에는 가장자리 노드의 반지름만 세었다. 이름표와 광륜이 빠져
        // 가장자리 이름표가 잘렸고, 모든 노드의 장식 중 가장 큰 값 하나를
        // 공통으로 빼던 때는 위아래에 25~29pt짜리 빈 띠가 남았다
        // (2026-09-16, 6 Pro 지적).
        var minCx = CGFloat.greatestFiniteMagnitude
        var maxCx = -CGFloat.greatestFiniteMagnitude
        var minCy = CGFloat.greatestFiniteMagnitude
        var maxCy = -CGFloat.greatestFiniteMagnitude
        var leftEdge = 0.0, rightEdge = 0.0, topEdge = 0.0, bottomEdge = 0.0
        for node in subset {
            guard let pos = positions[node.id] else { continue }
            let r = radius(node)
            // 몸통에 고리(1pt)와 숨을 조금 더한다. 예전에는 1.7배 광륜을
            // 미리 빼 두었는데, 광륜은 이제 고르거나 마우스를 올린 뉴런에만
            // 그린다. 그리지 않는 장식을 자리로 남겨 두면 맨 위 뉴런 위에
            // 28pt짜리 빈 띠가 남는다 (2026-09-17, 감사 지적).
            let body = r + 2
            let label = labelExtent(node)
            // 좌우는 이름표가 몸통 옆으로 붙을 수 있어 그만큼 넓게 잡는다.
            // 위아래는 몸통만 잡는다. 이름표는 캔버스 안에 들어가는 자리로만
            // 놓이므로(아래 drawLabels 참고) 미리 자리를 비워 둘 필요가 없다.
            // 비워 두면 맨 아래 뉴런 아래로 25pt짜리 빈 띠가 남는다
            // (2026-09-17, 감사 지적).
            let side = max(body, label.width / 2)
            let bottom = body
            if pos.x < minCx { minCx = pos.x; leftEdge = side }
            if pos.x > maxCx { maxCx = pos.x; rightEdge = side }
            if pos.y < minCy { minCy = pos.y; topEdge = body }
            if pos.y > maxCy { maxCy = pos.y; bottomEdge = bottom }
        }
        guard maxCx >= minCx, maxCy >= minCy else { return identity }
        // 캔버스 가장자리에 남기는 숨. 창 자체가 아래쪽에 16pt 여백을 두므로
        // 여기서 더 크게 잡으면 감사가 24pt 넘는 빈 띠로 본다. 14pt로 두었더니
        // 아래 여백과 합쳐 31pt가 되었다 (2026-09-17, 감사 지적).
        let pad: CGFloat = 2
        let spanX = max(maxCx - minCx, 0.001)
        let spanY = max(maxCy - minCy, 0.001)
        // 한 줄로 늘어선 뉴런은 폭이 0에 가깝다. 배율을 그대로 두면 한 없이
        // 커지므로 상한을 두고, 남는 자리는 가운데로 민다.
        let scaleX = min(max(width - pad * 2 - leftEdge - rightEdge, 40) / spanX, 6)
        let scaleY = min(max(height - pad * 2 - topEdge - bottomEdge, 40) / spanY, 6)
        let usedX = spanX * scaleX + leftEdge + rightEdge
        let usedY = spanY * scaleY + topEdge + bottomEdge
        return (
            scaleX: scaleX,
            scaleY: scaleY,
            offsetX: (width - usedX) / 2 + leftEdge - minCx * scaleX,
            offsetY: (height - usedY) / 2 + topEdge - minCy * scaleY
        )
    }

    /// 이름표가 차지하는 크기. 그리는 쪽과 같은 글꼴로 재야 배율이 맞는다.
    private func labelExtent(_ node: KnowledgeNode) -> CGSize {
        let text = node.label as NSString
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 10, weight: node.id == selectedNodeId ? .semibold : .regular),
        ]
        let size = text.size(withAttributes: attrs)
        // 이름 뒤에 까는 판이 좌우로 3pt씩 넓다 (draw 참고).
        return CGSize(width: size.width + 6, height: size.height + 2)
    }

    private func fitted(_ point: CGPoint) -> CGPoint {
        let fit = fitTransform(nodes: nodes)
        return CGPoint(x: point.x * fit.scaleX + fit.offsetX, y: point.y * fit.scaleY + fit.offsetY)
    }

    /// 포커스가 걸렸을 때 그 뉴런이 앉을 자리.
    ///
    /// 고른 뉴런을 화면 가운데로 옮기고 이웃을 둘레에 고르게 편다. 전체
    /// 배치를 그대로 확대하면 이웃이 서로 멀리 떨어져 있을 때 배율이 한없이
    /// 커져 노드가 화면 밖으로 밀려난다. 자리를 새로 잡으면 어떤 뉴런을
    /// 눌러도 같은 크기로 펼쳐진다 (2026-09-17, 사용자 지시).
    private func focusPoint(for nodeId: String, ring: [String]) -> CGPoint? {
        guard let focusId = focusNodeId else { return nil }
        let center = CGPoint(x: bounds.width / 2, y: bounds.height / 2)
        if nodeId == focusId { return center }
        guard let index = ring.firstIndex(of: nodeId) else { return nil }
        // 가장자리 이름표와 광륜이 잘리지 않을 만큼만 남기고 최대한 편다.
        // 높이의 32%만 쓰면 위아래에 80pt가 비어 감사가 빈 띠로 잡는다.
        // 이름표는 몸통 아래로 붙으므로 아래쪽 여유를 조금 더 준다
        // (2026-09-17).
        let labelRoom: CGFloat = 26
        let radius = max(
            min(
                (bounds.width - 80) / 2,
                (bounds.height - labelRoom * 2) / 2
            ),
            60
        )
        let step = (2 * Double.pi) / Double(max(ring.count, 1))
        // -90도에서 시작해 12시 방향부터 시계 방향으로 편다. 무거운 이웃이
        // 먼저 오므로 위쪽부터 읽힌다.
        let angle = -Double.pi / 2 + step * Double(index)
        return CGPoint(
            x: center.x + CGFloat(cos(angle)) * radius,
            y: center.y + CGFloat(sin(angle)) * radius
        )
    }

    /// 이 뉴런을 지금 어디에 그릴지. 전체 배치와 포커스 배치를 섞는다.
    private func layoutPoint(for nodeId: String, ring: [String]) -> CGPoint? {
        guard let raw = positions[nodeId] else { return nil }
        let global = fitted(raw)
        guard focusNodeId != nil, focusProgress > 0.01,
              let target = focusPoint(for: nodeId, ring: ring) else {
            return global
        }
        // 양 끝에서 속도가 0이 되는 곡선. 선형으로 두면 다가가기 시작할 때와
        // 멈출 때가 툭 끊겨 보인다.
        let t = CGFloat(min(max(focusProgress, 0), 1))
        let eased = t * t * (3 - 2 * t)
        return CGPoint(
            x: global.x + (target.x - global.x) * eased,
            y: global.y + (target.y - global.y) * eased
        )
    }

    /// 포커스 중에 둘레에 펼 뉴런의 순서. 무거운 시냅스부터다.
    private func focusRing() -> [String] {
        guard let focusId = focusNodeId else { return [] }
        var strongest: [String: Int] = [:]
        for edge in edges {
            let other: String
            if edge.source == focusId {
                other = edge.target
            } else if edge.target == focusId {
                other = edge.source
            } else {
                continue
            }
            strongest[other] = max(strongest[other] ?? 0, edge.weight)
        }
        return strongest.sorted { left, right in
            if left.value != right.value { return left.value > right.value }
            return left.key < right.key
        }.prefix(Self.focusNeighborLimit).map { $0.key }
    }

    /// A small force-directed relaxation. Repulsion between every pair is
    /// O(n^2), which is fine for a hand-curated graph of tens of nodes; the
    /// step count is capped so a refresh never blocks the main thread.
    private func settle(width: CGFloat, height: CGFloat) {
        guard !nodes.isEmpty, !settled else { return }
        let index = Dictionary(uniqueKeysWithValues: nodes.enumerated().map { ($0.element.id, $0.offset) })
        let margin: CGFloat = 34
        for _ in 0..<120 {
            var forces = [String: CGPoint](minimumCapacity: nodes.count)
            for node in nodes { forces[node.id] = .zero }
            for (i, left) in nodes.enumerated() {
                guard let leftPos = positions[left.id] else { continue }
                for right in nodes[(i + 1)...] {
                    guard let rightPos = positions[right.id] else { continue }
                    var dx = leftPos.x - rightPos.x
                    var dy = leftPos.y - rightPos.y
                    var distance = sqrt(dx * dx + dy * dy)
                    if distance < 0.01 {
                        // Two nodes on the same spot would divide by zero.
                        dx = CGFloat(i % 7) - 3
                        dy = CGFloat(i % 5) - 2
                        distance = sqrt(dx * dx + dy * dy)
                    }
                    let repulsion = 2600.0 / max(distance * distance, 1)
                    let fx = (dx / distance) * repulsion
                    let fy = (dy / distance) * repulsion
                    forces[left.id]?.x += fx
                    forces[left.id]?.y += fy
                    forces[right.id]?.x -= fx
                    forces[right.id]?.y -= fy
                }
            }
            for edge in edges {
                guard let a = positions[edge.source], let b = positions[edge.target] else { continue }
                let dx = b.x - a.x
                let dy = b.y - a.y
                let distance = max(sqrt(dx * dx + dy * dy), 0.01)
                // A heavier synapse pulls its two bodies closer.
                let target: CGFloat = 130 - CGFloat(min(100, max(0, edge.weight))) * 0.5
                let spring = (distance - target) * 0.010
                let fx = (dx / distance) * spring
                let fy = (dy / distance) * spring
                forces[edge.source]?.x += fx
                forces[edge.source]?.y += fy
                forces[edge.target]?.x -= fx
                forces[edge.target]?.y -= fy
            }
            let center = CGPoint(x: width / 2, y: height / 2)
            var moved: CGFloat = 0
            for node in nodes {
                guard var pos = positions[node.id], var velocity = velocities[node.id] else { continue }
                // Pull everything gently toward the middle so nothing drifts off.
                let toCenterX = (center.x - pos.x) * 0.006
                let toCenterY = (center.y - pos.y) * 0.006
                velocity.x = (velocity.x + (forces[node.id]?.x ?? 0) + toCenterX) * 0.82
                velocity.y = (velocity.y + (forces[node.id]?.y ?? 0) + toCenterY) * 0.82
                // Cap the per-step speed: a runaway node never leaves the canvas.
                let speed = sqrt(velocity.x * velocity.x + velocity.y * velocity.y)
                let limit: CGFloat = 6
                if speed > limit {
                    velocity.x = velocity.x / speed * limit
                    velocity.y = velocity.y / speed * limit
                }
                pos.x += velocity.x
                pos.y += velocity.y
                let bodyRadius = radius(node) + margin * 0.4
                pos.x = min(max(pos.x, bodyRadius), max(width - bodyRadius, bodyRadius))
                pos.y = min(max(pos.y, bodyRadius), max(height - bodyRadius, bodyRadius))
                moved += abs(velocity.x) + abs(velocity.y)
                positions[node.id] = pos
                velocities[node.id] = velocity
            }
            if moved < 0.6 {
                settled = true
                break
            }
        }
        _ = index
    }

    override func layout() {
        super.layout()
        // A resize re-runs the relaxation so nodes stay inside the new bounds.
        settled = false
        settle(width: max(bounds.width, 480), height: max(bounds.height, 320))
    }

    /// 눌린 자리에 있는 뉴런.
    ///
    /// 판정은 화면 좌표에서 한다. 시뮬레이션 좌표로 되돌려 놓고 비교하면
    /// 안 된다: 반지름은 화면 좌표에서 정해지는데 위치만 되돌리면, 배율이
    /// 1이 아닐 때 그려진 원과 눌리는 자리가 서로 어긋난다. 창을 넓게 펼친
    /// 그래프에서 가운데를 눌러도 옆 뉴런이 잡히던 원인이다 (2026-09-17,
    /// 6 Pro 지적).
    private func node(at point: CGPoint) -> KnowledgeNode? {
        var best: KnowledgeNode?
        var bestDistance = CGFloat.greatestFiniteMagnitude
        let ring = focusRing()
        for node in nodes {
            guard let pos = layoutPoint(for: node.id, ring: ring) else { continue }
            // 확대해서 걷어낸 뉴런은 화면에 없다. 눌리면 빈 곳을 눌렀는데
            // 보이지도 않는 뉴런이 골라져 근거 카드가 엉뚱한 것을 가리킨다
            // (2026-09-17).
            guard focusKeeps(nodeId: node.id) else { continue }
            let r = radius(node)
            let dx = point.x - pos.x
            let dy = point.y - pos.y
            let distance = dx * dx + dy * dy
            // 겹쳐 있으면 가장 가까운 뉴런을 고른다. 먼저 만난 것을 집으면
            // 큰 뉴런 뒤에 숨은 작은 뉴런을 영영 못 누른다.
            if distance <= r * r, distance < bestDistance {
                best = node
                bestDistance = distance
            }
        }
        return best
    }

    /// 그려진 뉴런이 실제로 차지하는 사각형. 감사가 이 값을 읽어 창이
    /// 비어 있는지 판단한다 (2026-09-16).
    var drawnSpread: NSRect? {
        var minX = CGFloat.greatestFiniteMagnitude
        var maxX = -CGFloat.greatestFiniteMagnitude
        var minY = CGFloat.greatestFiniteMagnitude
        var maxY = -CGFloat.greatestFiniteMagnitude
        let ring = focusRing()
        for node in nodes {
            guard let pos = layoutPoint(for: node.id, ring: ring) else { continue }
            let r = radius(node)
            minX = min(minX, pos.x - r)
            maxX = max(maxX, pos.x + r)
            minY = min(minY, pos.y - r)
            maxY = max(maxY, pos.y + r)
        }
        guard maxX > minX else { return nil }
        // layoutPoint()가 이미 그리는 좌표를 준다. 감사는 화면에 보이는
        // 자리를 재야 하므로 여기서 더 옮기지 않는다 (2026-09-17).
        return NSRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }

    override func mouseDown(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        let hit = node(at: point)
        selectedNodeId = hit?.id
        // 고른 뉴런으로 카메라를 부드럽게 들이민다. 빈 곳을 누르면 다시
        // 전체 그림으로 물러난다 (2026-09-17, 사용자 지시).
        beginFocus(on: hit?.id)
        onSelect?(hit)
        needsDisplay = true
    }

    /// 드릴다운 확대를 시작한다. 이미 그 뉴런을 보고 있으면 다시 시작하지 않는다.
    func beginFocus(on nodeId: String?) {
        let target = (nodeId == nil || focusGroup(around: nodeId!).count <= 1) ? nil : nodeId
        if target == focusNodeId { return }
        focusNodeId = target
        // 확대를 풀 때는 지금 배율에서, 걸 때는 전체 그림에서 출발한다.
        focusAnimationStart = Date().timeIntervalSince1970
        startFocusTimer()
    }

    /// 지금 화면이 확대 상태인지. 창이 힌트 줄에 안내를 띄울 때 쓴다.
    var isFocused: Bool { focusNodeId != nil && focusProgress > 0.01 }

    /// 확대 애니메이션을 끝 상태로 못 박는다.
    ///
    /// 배치 감사는 창을 화면에 띄우지 않으므로 RunLoop가 돌지 않아 타이머가
    /// 진행되지 않는다. 그대로 찍으면 확대 전 그림만 남아, 감사가 드릴다운을
    /// 한 번도 확인하지 못한다 (2026-09-17).
    func finishFocusForAudit() {
        focusTimer?.invalidate()
        focusTimer = nil
        focusProgress = focusNodeId == nil ? 0 : 1
        needsDisplay = true
    }

    /// 포커스 중인 뉴런의 이름. 힌트 줄이 무엇을 보고 있는지 말한다.
    var focusedNodeLabel: String? {
        guard let id = focusNodeId else { return nil }
        return nodes.first { $0.id == id }?.label
    }

    private func startFocusTimer() {
        guard focusTimer == nil else { return }
        let interval = 1.0 / Self.focusFramesPerSecond
        let timer = Timer(timeInterval: interval, repeats: true) { [weak self] _ in
            self?.stepFocus()
        }
        // 메뉴가 열려 있는 동안에도 애니메이션이 돌아야 하므로 common 모드에 건다.
        RunLoop.main.add(timer, forMode: .common)
        focusTimer = timer
    }

    private func stepFocus() {
        let elapsed = Date().timeIntervalSince1970 - focusAnimationStart
        let raw = min(max(elapsed / Self.focusDuration, 0), 1)
        let wanted = focusNodeId == nil ? 1 - raw : raw
        if abs(wanted - focusProgress) > 0.001 {
            focusProgress = wanted
            needsDisplay = true
        }
        if raw >= 1 {
            focusProgress = focusNodeId == nil ? 0 : 1
            focusTimer?.invalidate()
            focusTimer = nil
            // 확대가 끝나면 그 상태로 멈춘다. 계속 그리면 배터리만 쓴다.
            needsDisplay = true
        }
    }

    /// 포커스 중에 남길 뉴런의 불투명도.
    ///
    /// 이어진 이웃은 그대로 두고, 나머지는 확대가 끝날 때 완전히 사라진다.
    /// 흐리게 남겨 두면 화면 가장자리에 유령 같은 원이 늘어서, 정작 보고
    /// 싶은 이웃 관계가 그 사이에 묻힌다 (2026-09-17, 사용자 지시).
    private func focusAlpha(for nodeId: String) -> CGFloat {
        guard let focusId = focusNodeId, focusProgress > 0.01 else { return 1 }
        if nodeId == focusId { return 1 }
        let faded = CGFloat(min(max(focusProgress, 0), 1))
        // 둘레에 남는 이웃만 밝게. 목록에서 잘린 뉴런은 확대가 끝나면 완전히
        // 사라진다. focusKeeps(edge:)와 같은 목록을 봐야 선과 몸통이 어긋나지
        // 않는다 (2026-09-17).
        guard focusRing().contains(nodeId) else {
            return max(0, 1 - faded * 1.6)
        }
        return 1 - 0.2 * faded
    }

    /// 지금 그 뉴런을 눌러도 되는지. 사라진 뉴런은 누를 수 없어야 한다.
    private func focusKeeps(nodeId: String) -> Bool {
        focusAlpha(for: nodeId) > 0.02
    }

    /// 확대 중에 그릴 시냅스인지.
    ///
    /// 양쪽 끝이 모두 화면에 남는 시냅스만 그린다. 한쪽만 확인하면, 둘레에
    /// 펼 이웃 목록에서 잘린 뉴런으로 가는 선이 화면 밖까지 뻗어 별자리처럼
    /// 보인다 (2026-09-17, 사용자 지시).
    private func focusKeeps(edge: KnowledgeEdge) -> Bool {
        guard let focusId = focusNodeId, focusProgress > 0.01 else { return true }
        if edge.source != focusId, edge.target != focusId { return false }
        // 반대쪽 끝이 둘레에 남았는지 본다. 포커스 뉴런 자신은 언제나 남는다.
        let other = edge.source == focusId ? edge.target : edge.source
        return focusGroup(around: focusId).contains { $0.id == other }
    }

    override func mouseMoved(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        let hit = node(at: point)?.id
        if hit != hoveredId {
            hoveredId = hit
            needsDisplay = true
        }
    }

    override func mouseExited(with event: NSEvent) {
        if hoveredId != nil {
            hoveredId = nil
            needsDisplay = true
        }
    }

    /// 배경에 깔 시냅스의 자리.
    ///
    /// 관계를 전부 그리면 341개 선이 가운데에서 서로 엉겨, 그래프가 무엇을
    /// 말하는지보다 선이 많다는 사실만 보인다. 뉴런마다 굵은 시냅스 몇 개만
    /// 남기면 이웃 관계가 읽히고, 뉴런이 하나도 외톨이로 남지 않는다.
    /// 계산은 한 번만 하고, 뉴런이나 관계가 바뀔 때만 다시 한다
    /// (2026-09-17, 6 Pro 지적).
    private var keptSynapses: Set<Int> = []
    private var keptSynapsesKey = ""

    private func backgroundSynapses() -> Set<Int> {
        // The key must cover everything the result depends on. Node and edge
        // counts plus one source are not enough: a refresh that swaps an
        // equal-size graph, or one that keeps the counts and changes the
        // first edge, would keep the stale synapse set. Hashing every
        // endpoint and weight is cheap next to the layout it guards
        // (2026-09-17, 6 Pro 지적).
        var hasher = Hasher()
        hasher.combine(nodes.count)
        for node in nodes {
            hasher.combine(node.id)
        }
        hasher.combine(edges.count)
        for edge in edges {
            hasher.combine(edge.source)
            hasher.combine(edge.target)
            hasher.combine(edge.weight)
        }
        let key = String(hasher.finalize())
        if key == keptSynapsesKey { return keptSynapses }
        keptSynapsesKey = key
        // 뉴런마다 굵은 순으로 몇 개를 남긴다. 양쪽 끝에서 세므로 한 뉴런이
        // 여러 이웃과 이어져도 그중 굵은 것만 남는다.
        var perNode: [String: [(weight: Int, index: Int)]] = [:]
        for (index, edge) in edges.enumerated() {
            perNode[edge.source, default: []].append((edge.weight, index))
            perNode[edge.target, default: []].append((edge.weight, index))
        }
        // 뉴런마다 굵은 순으로 고른 뒤, 합집합이 다시 상한을 넘지 않게
        // 한 번 더 깎는다. 양쪽 끝에서 각각 넷을 고르면 어떤 뉴런은 여덟
        // 개까지 붙어, "뉴런당 넷"이라는 규칙이 화면에서 지켜지지 않았다
        // (2026-09-17, 6 Pro 지적).
        var kept: Set<Int> = []
        for (_, list) in perNode {
            for entry in list.sorted(by: { $0.weight > $1.weight }).prefix(Self.synapsesPerNeuron) {
                kept.insert(entry.index)
            }
        }
        // 무거운 시냅스부터 다시 넣으면서 양쪽 끝의 남은 자리를 확인한다.
        var budget: [String: Int] = [:]
        var trimmed: Set<Int> = []
        let ranked = edges.enumerated()
            .filter { kept.contains($0.offset) }
            .sorted { left, right in
                if left.element.weight != right.element.weight {
                    return left.element.weight > right.element.weight
                }
                return left.offset < right.offset
            }
        for (index, edge) in ranked {
            let usedSource = budget[edge.source, default: 0]
            let usedTarget = budget[edge.target, default: 0]
            guard usedSource < Self.synapsesPerNeuron,
                  usedTarget < Self.synapsesPerNeuron else { continue }
            budget[edge.source] = usedSource + 1
            budget[edge.target] = usedTarget + 1
            trimmed.insert(index)
        }
        keptSynapses = trimmed
        return trimmed
    }

    /// 시스템이 "대비 증가"를 켰는지. 흐린 회색 시냅스를 그대로 두면 배경에
    /// 묻혀, 저시력 사용자에게 그래프가 빈 캔버스로 보인다 (2026-09-17).
    private var increaseContrast: Bool {
        NSWorkspace.shared.accessibilityDisplayShouldIncreaseContrast
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        NSColor.clear.setFill()
        bounds.fill()
        let highContrast = increaseContrast
        let positions = self.positions
        let byId = Dictionary(uniqueKeysWithValues: nodes.map { ($0.id, $0) })
        // 포커스가 걸렸으면 이웃을 둘레로 다시 편다. 그린 자리와 누르는
        // 자리가 어긋나면 보이는 뉴런을 눌러도 다른 것이 잡힌다
        // (2026-09-17, 사용자 지시).
        let ring = focusRing()
        func placed(_ id: String) -> CGPoint? { layoutPoint(for: id, ring: ring) }

        // 시냅스를 먼저 깔아 세포체가 그 위에 앉게 한다.
        //
        // 예전에는 모든 시냅스에 중간 점을 하나씩 찍었다. 관계가 341개면
        // 점만 341개라, 그래프가 무엇을 말하는지보다 점이 많다는 사실만
        // 보였다. 지금은 고른 뉴런에 붙은 시냅스에만 점을 찍는다
        // (2026-09-17, 6 Pro 지적).
        let highlight = selectedNodeId ?? hoveredId
        // 배경에 깔 시냅스를 먼저 고른다. 뉴런마다 굵은 것 몇 개만 남기고,
        // 고른 뉴런에 붙은 것은 하나도 빠뜨리지 않는다 (2026-09-17).
        let kept = backgroundSynapses()
        for (index, edge) in edges.enumerated() {
            guard positions[edge.source] != nil, positions[edge.target] != nil else { continue }
            guard let a = placed(edge.source), let b = placed(edge.target) else { continue }
            let grounded = edge.evidence.grounded
            let strength = CGFloat(min(100, max(0, edge.weight))) / 100.0
            let touchesHighlight = highlight != nil
                && (edge.source == highlight || edge.target == highlight)
            guard touchesHighlight || kept.contains(index) else { continue }
            // 포커스를 잡으면 그 뉴런에 붙은 시냅스만 남긴다. 나머지를
            // 그대로 두면 확대해도 선밭이 따라와 읽히지 않는다
            // (2026-09-17, 사용자 지시).
            guard focusKeeps(edge: edge) else { continue }
            // 고른 뉴런에 붙은 시냅스만 또렷하게. 나머지는 배경으로 물러난다.
            var alpha = touchesHighlight
                ? 0.55 + 0.35 * strength
                : (highlight == nil ? 0.22 + 0.34 * strength : 0.06 + 0.10 * strength)
            if let focusId = focusNodeId, focusProgress > 0.01 {
                let onFocus = edge.source == focusId || edge.target == focusId
                if !onFocus {
                    alpha *= CGFloat(1 - 0.88 * min(max(focusProgress, 0), 1))
                }
            }
            if highContrast {
                // 대비 증가에서는 배경 선도 읽을 수 있어야 한다.
                alpha = touchesHighlight ? min(1.0, alpha + 0.15) : max(alpha, 0.42)
            }
            // 자비스 코어와 같은 금색 계열. 근거가 있는 시냅스는 밝은 금색,
            // 아직 확인되지 않은 것은 흐린 놋쇠색이다 (2026-09-17).
            let color = (grounded ? Self.synapseGold : Self.synapseBrass)
                .withAlphaComponent(alpha)
            let path = NSBezierPath()
            path.move(to: a)
            path.line(to: b)
            path.lineWidth = (touchesHighlight ? 1.6 : 0.8) + 2.0 * strength
            path.lineCapStyle = .round
            color.setStroke()
            path.stroke()
            guard touchesHighlight else { continue }
            // A pulse bead at the midpoint reads as a firing synapse.
            let mid = CGPoint(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2)
            let bead = NSBezierPath(ovalIn: NSRect(x: mid.x - 2.2, y: mid.y - 2.2, width: 4.4, height: 4.4))
            color.withAlphaComponent(0.85).setFill()
            bead.fill()
        }

        for node in nodes {
            guard let pos = placed(node.id) else { continue }
            let r = radius(node)
            let selected = node.id == selectedNodeId
            let hovered = node.id == hoveredId
            // 근거가 있는 뉴런은 빛나고, 아직 확인되지 않은 씨앗은 흐리다.
            //
            // 예전에는 모든 뉴런에 1.7배 광륜을 깔았다. 뉴런 50개가 서로
            // 겹치면서 가운데가 뿌연 얼룩이 되었다. 지금은 고르거나 마우스를
            // 올린 뉴런에만 광륜을 준다 (2026-09-17, 6 Pro 지적).
            let core = node.evidence.grounded ? Self.neuronGold : Self.neuronBrass
            let emphasized = selected || hovered
            // 확대해서 걷어낸 뉴런은 고리도 남기지 않는다. 몸통만 지우고
            // 고리를 두면 화면에 빈 동그라미만 떠서, 사라진 것이 아니라
            // 그려지다 만 것처럼 보인다 (2026-09-17).
            let focusOpacity = focusAlpha(for: node.id)
            let body = NSBezierPath(ovalIn: NSRect(x: pos.x - r, y: pos.y - r, width: r * 2, height: r * 2))
            var bodyAlpha: CGFloat = emphasized ? 1.0 : (node.evidence.grounded ? 0.82 : 0.48)
            bodyAlpha *= focusOpacity
            if highContrast, !emphasized {
                bodyAlpha = max(bodyAlpha, 0.85)
            }
            core.withAlphaComponent(bodyAlpha).setFill()
            body.fill()
            if node.evidence.retracted {
                NSColor.systemRed.withAlphaComponent(0.9 * focusOpacity).setStroke()
                body.lineWidth = 2
                body.stroke()
            }
            NSColor(calibratedRed: 1.0, green: 0.93, blue: 0.68, alpha: (selected ? 0.95 : 0.6) * focusOpacity)
                .setStroke()
            let ring = NSBezierPath(ovalIn: NSRect(x: pos.x - r, y: pos.y - r, width: r * 2, height: r * 2))
            ring.lineWidth = selected ? 2 : 1
            ring.stroke()

        }
        // 이름표는 세포체를 다 그린 뒤에 붙인다.
        //
        // 예전에는 뉴런마다 제자리에 바로 그렸다. 그래프가 촘촘해지자
        // 가운데 이름들이 서로 겹쳐 무엇이 무엇인지 읽을 수 없는 얼룩이
        // 되었다. 지금은 중요한 뉴런부터 자리를 잡고, 이미 놓인 이름표나
        // 남의 몸통과 겹치면 위·아래·옆으로 비켜 본다. 어디에도 들어가지
        // 않으면 이름을 접고, 고른 뉴런과 마우스를 올린 뉴런은 언제나
        // 보여 준다 (2026-09-16, 6 Pro 지적).
        drawLabels()
        _ = byId
    }

    /// 이름표를 겹치지 않게 배치한다. 중요한 뉴런이 먼저 자리를 갖는다.
    private func drawLabels() {
        var placed: [NSRect] = []
        // 세포체도 자리를 차지한다. 이름표가 남의 몸통을 덮으면 읽기 어렵다.
        // 확대에서 걷어낸 뉴런은 자리도 차지하지 않는다.
        let ring = focusRing()
        for node in nodes {
            guard focusAlpha(for: node.id) > 0.02 else { continue }
            guard let pos = layoutPoint(for: node.id, ring: ring) else { continue }
            let r = radius(node)
            placed.append(NSRect(x: pos.x - r, y: pos.y - r, width: r * 2, height: r * 2))
        }
        let ordered = nodes.sorted { left, right in
            if left.importance != right.importance { return left.importance > right.importance }
            return left.id < right.id
        }
        for node in ordered {
            guard focusAlpha(for: node.id) > 0.02 else { continue }
            guard let pos = layoutPoint(for: node.id, ring: ring) else { continue }
            let r = radius(node)
            let selected = node.id == selectedNodeId
            let hovered = node.id == hoveredId
            let text = node.label as NSString
            let alpha = focusAlpha(for: node.id)
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 10, weight: selected ? .semibold : .regular),
                .foregroundColor: NSColor.labelColor.withAlphaComponent(alpha),
            ]
            let size = text.size(withAttributes: attrs)
            // 이름 뒤에 까는 판이 좌우로 3pt씩 넓다 (labelExtent 참고).
            let width = size.width + 6
            let height = size.height + 2
            // 아래, 위, 오른쪽, 왼쪽 순으로 비켜 본다.
            let candidates = [
                NSPoint(x: pos.x - width / 2, y: pos.y + r + 2),
                NSPoint(x: pos.x - width / 2, y: pos.y - r - height - 2),
                NSPoint(x: pos.x + r + 3, y: pos.y - height / 2),
                NSPoint(x: pos.x - r - width - 3, y: pos.y - height / 2),
            ]
            var chosen: NSRect?
            for origin in candidates {
                let rect = NSRect(x: origin.x, y: origin.y, width: width, height: height)
                // 캔버스 밖으로 나가는 이름표는 놓지 않는다. 가장자리에서
                // 잘린 이름은 없는 것과 다를 바 없고, 자리만 차지한다
                // (2026-09-17).
                if !bounds.insetBy(dx: 1, dy: 1).contains(rect) { continue }
                if placed.contains(where: { $0.intersects(rect) }) { continue }
                chosen = rect
                break
            }
            // 눌러 놓고도 이름이 사라지면 무엇을 골랐는지 알 수 없다.
            if chosen == nil, !selected, !hovered { continue }
            let fallback = NSRect(
                x: max(bounds.minX + 1, min(pos.x - width / 2, bounds.maxX - width - 1)),
                y: min(pos.y + r + 2, max(bounds.maxY - height - 1, bounds.minY + 1)),
                width: width,
                height: height
            )
            let rect = chosen ?? fallback
            placed.append(rect)
            // A soft plate keeps the name readable over a synapse.
            NSColor.windowBackgroundColor.withAlphaComponent(0.72 * alpha).setFill()
            NSBezierPath(roundedRect: rect, xRadius: 3, yRadius: 3).fill()
            let inner = NSRect(
                x: rect.minX + 3,
                y: rect.minY + 1,
                width: rect.width - 6,
                height: rect.height - 2
            )
            text.draw(in: inner, withAttributes: attrs)
        }
    }
}

/// 자비스 홀로그램 코어.
///
/// 메뉴 패널의 주인공 화면이다. 주황·금색 톤의 입체 구형 뉴런 시냅스가
/// 천천히 돌고, 백그라운드 작업(자동 답변 생성·긱뉴스 전송·DB 동기화)이
/// 돌 때는 회전이 빨라지고 파동이 퍼지며 입자가 시냅스를 타고 흐른다.
///
/// 그리는 일은 전부 Canvas 2D(NSBezierPath)로만 한다. 구면 위의 점과
/// 시냅스는 처음 한 번만 만들어 두고, 매 프레임 회전시켜 투영한다.
/// 뉴런 수와 프레임 상한을 고정해 두어 메뉴가 열려 있는 동안 CPU를 계속
/// 붙잡지 않게 했다 (2026-09-17).
final class JarvisCoreView: NSView {

    /// 구면 위 뉴런의 개수. 늘리면 밀도가 올라가고 그리는 비용도 함께 는다.
    static let neuronCount = 96
    /// 뉴런마다 이어 붙일 이웃 시냅스 수.
    static let synapseNeighbors = 3
    /// 동시에 떠 있는 입자 수.
    static let particleCount = 30
    /// 초당 프레임 상한. 60fps로 두면 메뉴가 열려 있는 동안 CPU를 계속 쓴다.
    static let framesPerSecond: Double = 30
    /// 가만히 있을 때의 회전 속도(라디안/초).
    static let idleSpeed: Double = 0.34
    /// 작업이 돌 때 더해지는 회전 속도.
    static let activeSpeed: Double = 2.1
    /// 파동 하나가 살아 있는 시간(초).
    static let pulseLife: Double = 1.5

    private struct Point3 {
        var x: Double
        var y: Double
        var z: Double
    }

    private struct Projected {
        var x: Double
        var y: Double
        /// -1이 멀고 1이 가깝다. 크기·밝기·굵기가 모두 이 값에 붙는다.
        var depth: Double
    }

    private struct Particle {
        var edge: Int
        var progress: Double
        var speed: Double
    }

    private struct Pulse {
        var start: Double
        var strength: Double
    }

    /// 입자와 파동의 자리를 정하는 작은 난수기. 같은 씨앗이면 같은 그림이
    /// 나와, 감사가 찍는 한 장도 매번 같다.
    private struct SeededRandom {
        private var state: UInt64

        init(seed: UInt64) {
            state = seed | 1
        }

        mutating func next() -> Double {
            state ^= state << 13
            state ^= state >> 7
            state ^= state << 17
            return Double(state % 1_000_000) / 1_000_000.0
        }

        mutating func next(in range: Range<Double>) -> Double {
            range.lowerBound + next() * (range.upperBound - range.lowerBound)
        }
    }

    /// 구면 위의 점. 한 번만 만든다.
    private static let basePoints: [Point3] = JarvisCoreView.makeSphere(neuronCount)
    /// 이웃끼리 이은 시냅스. 회전해도 이웃 관계는 그대로라 한 번만 만든다.
    private static let synapseEdges: [(Int, Int)] = JarvisCoreView.makeSynapses(
        basePoints,
        neighbors: synapseNeighbors
    )

    /// 지금 돌고 있는 백그라운드 작업의 세기(0..1). 회전 속도와 파동, 입자
    /// 흐름이 모두 이 값에 반응한다.
    var activity: Double {
        get { currentActivity }
        set {
            let clamped = min(max(newValue, 0), 1)
            if clamped > currentActivity + 0.01 {
                spawnPulse(strength: max(clamped, 0.45))
            }
            currentActivity = clamped
        }
    }
    /// 지금 고른 방의 상태(green/yellow/red). 코어 자체는 늘 주황·금색이고,
    /// 이 값은 바깥 고리 하나에만 쓴다.
    var level: String = "green" {
        didSet { if level != oldValue { needsDisplay = true } }
    }

    private var currentActivity: Double = 0
    private var phase: Double = 0
    private var lastTick: Double = 0
    private var timer: Timer?
    private var pulses: [Pulse] = []
    private var particles: [Particle] = []
    private var random = SeededRandom(seed: 0x5A17C0DE)
    /// 매 프레임 새로 만들지 않고 재사용한다.
    private var projectedBuffer: [Projected] = []

    override var isFlipped: Bool { true }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        wantsLayer = true
    }

    deinit {
        timer?.invalidate()
    }

    // MARK: - 백그라운드 작업 접기

    /// 지금 백그라운드에서 무슨 일이 도는지 한 값으로 접는다.
    ///
    /// 진행 중인 단계가 있으면 그 자체로 세고, 대기 중인 작업이 남아 있어도
    /// 조금 빨라진다. 판단은 코어(파이썬)가 보낸 상태만 보고 한다.
    static func activity(
        pipeline: PipelineModel?,
        openJobs: Int,
        level: String
    ) -> Double {
        var value = 0.0
        for stage in pipeline?.stages ?? [] {
            switch stage.state {
            case "active": value = max(value, 0.9)
            case "failed", "blocked": value = max(value, 0.55)
            default: break
            }
        }
        if openJobs > 0 { value = max(value, 0.35) }
        switch level {
        case "red": value = max(value, 0.5)
        case "yellow": value = max(value, 0.25)
        default: break
        }
        return value
    }

    /// 코어 아래 한 줄로 지금 무슨 일이 도는지 말한다.
    ///
    /// 예전에는 8단계 파이프라인 띠가 그 일을 했다. 단계 이름 여덟 개를
    /// 읽어야 지금 상태를 알 수 있었고, 모두 초록이면 지금 도는 것인지
    /// 방금 끝난 것인지도 구분되지 않았다. 지금은 코어가 그 자리를 대신하고,
    /// 이 한 줄이 말로 확인해 준다 (2026-09-17).
    static func caption(pipeline: PipelineModel?, openJobs: Int) -> String {
        let stages = pipeline?.stages ?? []
        if let active = stages.first(where: { $0.state == "active" }) {
            let name = stageTitles[active.id] ?? active.id
            return "\(name) 진행 중"
        }
        if let failed = stages.first(where: { $0.state == "failed" || $0.state == "blocked" }) {
            let name = stageTitles[failed.id] ?? failed.id
            return "\(name)에서 멈춤"
        }
        if openJobs > 0 {
            return "대기 \(openJobs)건"
        }
        if pipeline?.outcome == "sent" {
            return "마지막 답변 전송 완료"
        }
        return "대기 중"
    }

    /// 단계 id를 사람이 읽는 이름으로. PipelineView와 같은 표를 쓴다.
    private static let stageTitles: [String: String] = Dictionary(
        uniqueKeysWithValues: PipelineView.labels.map { ($0.id, $0.title) }
    )

    // MARK: - 도형 만들기

    private static func makeSphere(_ count: Int) -> [Point3] {
        var points: [Point3] = []
        points.reserveCapacity(count)
        guard count > 1 else { return [Point3(x: 0, y: 0, z: 0)] }
        // 황금각으로 나눠야 점이 띠로 몰리지 않고 고르게 퍼진다.
        let golden = Double.pi * (3.0 - 5.0.squareRoot())
        for index in 0..<count {
            let y = 1.0 - (Double(index) / Double(count - 1)) * 2.0
            let ring = max(0.0, 1.0 - y * y).squareRoot()
            let theta = golden * Double(index)
            points.append(Point3(x: cos(theta) * ring, y: y, z: sin(theta) * ring))
        }
        return points
    }

    private static func makeSynapses(_ points: [Point3], neighbors: Int) -> [(Int, Int)] {
        guard points.count > 1, neighbors > 0 else { return [] }
        var edges: [(Int, Int)] = []
        var seen = Set<Int>()
        for i in 0..<points.count {
            var scored: [(Double, Int)] = []
            scored.reserveCapacity(points.count - 1)
            for j in 0..<points.count where j != i {
                let dx = points[i].x - points[j].x
                let dy = points[i].y - points[j].y
                let dz = points[i].z - points[j].z
                scored.append((dx * dx + dy * dy + dz * dz, j))
            }
            scored.sort { $0.0 < $1.0 }
            for k in 0..<min(neighbors, scored.count) {
                let j = scored[k].1
                let low = min(i, j)
                let high = max(i, j)
                if seen.insert(low * 1_000 + high).inserted {
                    edges.append((low, high))
                }
            }
        }
        return edges
    }

    // MARK: - 애니메이션

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window == nil {
            stopAnimation()
        } else {
            startAnimation()
        }
    }

    private func startAnimation() {
        guard timer == nil else { return }
        lastTick = Date.timeIntervalSinceReferenceDate
        let timer = Timer(
            timeInterval: 1.0 / Self.framesPerSecond,
            repeats: true
        ) { [weak self] _ in
            self?.tick()
        }
        // 메뉴가 열려 있는 동안은 이벤트 추적 모드라 common에 넣어야 돈다.
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func stopAnimation() {
        timer?.invalidate()
        timer = nil
    }

    private func tick() {
        let now = Date.timeIntervalSinceReferenceDate
        // 창을 오래 닫아 두었다 열면 dt가 커진다. 한 프레임에 구가 반 바퀴
        // 돌지 않도록 상한을 둔다.
        let dt = min(max(now - lastTick, 0), 0.25)
        lastTick = now
        let speed = Self.idleSpeed + Self.activeSpeed * currentActivity
        phase = (phase + speed * dt).truncatingRemainder(dividingBy: 2 * Double.pi)
        advanceParticles(dt: dt)
        pulses.removeAll { now - $0.start > Self.pulseLife }
        needsDisplay = true
    }

    private func spawnPulse(strength: Double) {
        // 파동이 우수수 겹치면 화면이 시끄럽다. 살아 있는 것이 셋을 넘으면
        // 가장 오래된 것을 버린다.
        if pulses.count >= 3 { pulses.removeFirst() }
        pulses.append(Pulse(start: Date.timeIntervalSinceReferenceDate, strength: strength))
    }

    private func advanceParticles(dt: Double) {
        guard !Self.synapseEdges.isEmpty else { return }
        if particles.isEmpty {
            particles.reserveCapacity(Self.particleCount)
            for _ in 0..<Self.particleCount {
                particles.append(newParticle())
            }
        }
        let boost = 1.0 + currentActivity * 1.7
        for index in particles.indices {
            particles[index].progress += particles[index].speed * boost * dt
            if particles[index].progress >= 1 {
                particles[index] = newParticle()
            }
        }
    }

    private func newParticle() -> Particle {
        let count = max(Self.synapseEdges.count, 1)
        let edge = min(Int(random.next() * Double(count)), count - 1)
        return Particle(
            edge: edge,
            // 가장자리에서 갑자기 나타나지 않게 앞쪽에서 시작한다.
            progress: random.next() * 0.25,
            speed: random.next(in: 0.35..<0.9)
        )
    }

    // MARK: - 그리기

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        NSColor.clear.setFill()
        bounds.fill()
        let radius = min(bounds.width, bounds.height) * 0.5 - 4
        guard radius > 8 else { return }
        let center = CGPoint(x: bounds.midX, y: bounds.midY)
        drawBackdrop(center: center, radius: radius)
        drawSphereRings(center: center, radius: radius)
        let projected = project(center: center, radius: Double(radius))
        drawSynapses(projected)
        drawNeurons(projected)
        drawParticles(projected)
        drawPulses(center: center, radius: Double(radius))
        drawLevelRing(center: center, radius: Double(radius))
    }

    /// 구의 중심에서 새어 나오는 빛. 작업이 셀수록 밝아진다.
    private func drawBackdrop(center: CGPoint, radius: CGFloat) {
        let inner = NSColor(
            calibratedRed: 1.0,
            green: 0.52,
            blue: 0.08,
            alpha: 0.20 + 0.16 * currentActivity
        )
        let outer = NSColor(calibratedRed: 1.0, green: 0.42, blue: 0.06, alpha: 0.0)
        let rect = NSRect(
            x: center.x - radius,
            y: center.y - radius,
            width: radius * 2,
            height: radius * 2
        )
        if let gradient = NSGradient(starting: inner, ending: outer) {
            gradient.draw(in: NSBezierPath(ovalIn: rect), relativeCenterPosition: .zero)
        }
    }

    /// 위도선 몇 개와 바깥 테두리. 이것만으로도 납작한 원이 아니라 구로 읽힌다.
    /// 코어를 감싸는 궤도 고리.
    ///
    /// 예전에는 위도선 다섯 개를 그렸다. 그러면 구가 아니라 줄무늬 공처럼
    /// 보였다. 지금은 기울어진 궤도 고리 세 개를 서로 다른 속도로 돌린다.
    /// 홀로그램처럼 보이면서, 구가 돌고 있다는 사실도 함께 읽힌다
    /// (2026-09-17).
    private func drawSphereRings(center: CGPoint, radius: CGFloat) {
        // 고리마다 기울기와 반지름, 도는 속도를 다르게 준다.
        let orbits: [(tilt: Double, squash: CGFloat, speed: Double, alpha: CGFloat)] = [
            (0.0, 0.30, 0.6, 0.30),
            (1.05, 0.42, -0.9, 0.22),
            (-0.75, 0.36, 1.4, 0.18),
        ]
        for orbit in orbits {
            let angle = phase * orbit.speed
            // 고리의 긴 지름은 코어보다 조금 크다. 궤도가 코어를 감싼다.
            let span = radius * (1.06 + 0.05 * CGFloat(abs(sin(angle))))
            let rect = NSRect(
                x: center.x - span,
                y: center.y - span * orbit.squash,
                width: span * 2,
                height: span * 2 * orbit.squash
            )
            let path = NSBezierPath(ovalIn: rect)
            path.lineWidth = 1
            NSColor(
                calibratedRed: 1.0,
                green: 0.70,
                blue: 0.26,
                alpha: orbit.alpha
            ).setStroke()
            path.stroke()
        }
        // 코어 자체의 둘레. 이것 하나만 또렷하게 둔다.
        let outer = NSBezierPath(
            ovalIn: NSRect(
                x: center.x - radius,
                y: center.y - radius,
                width: radius * 2,
                height: radius * 2
            )
        )
        outer.lineWidth = 1
        NSColor(calibratedRed: 1.0, green: 0.72, blue: 0.30, alpha: 0.30).setStroke()
        outer.stroke()
    }

    /// 구면 위의 점을 회전시켜 화면 좌표로 옮긴다. 결과 배열은 재사용한다.
    private func project(center: CGPoint, radius: Double) -> [Projected] {
        projectedBuffer.removeAll(keepingCapacity: true)
        let cosPhase = cos(phase)
        let sinPhase = sin(phase)
        // 위아래로 조금 기울여야 도는 것이 눈에 보인다.
        let tilt = 0.42
        let cosTilt = cos(tilt)
        let sinTilt = sin(tilt)
        for point in Self.basePoints {
            let x1 = point.x * cosPhase - point.z * sinPhase
            let z1 = point.x * sinPhase + point.z * cosPhase
            let y2 = point.y * cosTilt - z1 * sinTilt
            let z2 = point.y * sinTilt + z1 * cosTilt
            // 가까운 점은 조금 크게, 먼 점은 조금 작게. 원근이 없으면
            // 회전해도 납작한 무늬로 보인다.
            let perspective = 1.0 / (1.0 + z2 * 0.22)
            projectedBuffer.append(
                Projected(
                    x: Double(center.x) + x1 * radius * perspective,
                    y: Double(center.y) + y2 * radius * perspective,
                    depth: z2
                )
            )
        }
        return projectedBuffer
    }

    private func drawSynapses(_ projected: [Projected]) {
        guard projected.count == Self.basePoints.count else { return }
        for edge in Self.synapseEdges {
            let a = projected[edge.0]
            let b = projected[edge.1]
            let near = (a.depth + b.depth) * 0.25 + 0.5
            let alpha = 0.10 + 0.34 * near + 0.22 * currentActivity * near
            let path = NSBezierPath()
            path.move(to: CGPoint(x: a.x, y: a.y))
            path.line(to: CGPoint(x: b.x, y: b.y))
            path.lineWidth = 0.5 + 1.1 * near
            NSColor(calibratedRed: 1.0, green: 0.62, blue: 0.18, alpha: alpha).setStroke()
            path.stroke()
        }
    }

    private func drawNeurons(_ projected: [Projected]) {
        for point in projected {
            let near = (point.depth + 1) * 0.5
            let size = 0.9 + 2.3 * near
            let alpha = 0.34 + 0.62 * near
            let color = near > 0.74
                ? NSColor(calibratedRed: 1.0, green: 0.93, blue: 0.68, alpha: alpha)
                : NSColor(calibratedRed: 1.0, green: 0.70, blue: 0.26, alpha: alpha)
            color.setFill()
            NSBezierPath(
                ovalIn: NSRect(
                    x: point.x - size,
                    y: point.y - size,
                    width: size * 2,
                    height: size * 2
                )
            ).fill()
        }
    }

    private func drawParticles(_ projected: [Projected]) {
        guard projected.count == Self.basePoints.count else { return }
        for particle in particles {
            guard particle.edge >= 0, particle.edge < Self.synapseEdges.count else { continue }
            let edge = Self.synapseEdges[particle.edge]
            let a = projected[edge.0]
            let b = projected[edge.1]
            let t = min(max(particle.progress, 0), 1)
            let x = a.x + (b.x - a.x) * t
            let y = a.y + (b.y - a.y) * t
            let depth = a.depth + (b.depth - a.depth) * t
            let near = (depth + 1) * 0.5
            let size = 0.8 + 1.7 * near
            NSColor(
                calibratedRed: 1.0,
                green: 0.97,
                blue: 0.85,
                alpha: 0.22 + 0.7 * near
            ).setFill()
            NSBezierPath(
                ovalIn: NSRect(x: x - size, y: y - size, width: size * 2, height: size * 2)
            ).fill()
        }
    }

    private func drawPulses(center: CGPoint, radius: Double) {
        let now = Date.timeIntervalSinceReferenceDate
        for pulse in pulses {
            let age = now - pulse.start
            guard age >= 0, age <= Self.pulseLife else { continue }
            let t = age / Self.pulseLife
            let ringRadius = radius * (0.22 + 0.95 * t)
            let alpha = (1.0 - t) * 0.5 * pulse.strength
            let path = NSBezierPath(
                ovalIn: NSRect(
                    x: center.x - ringRadius,
                    y: center.y - ringRadius,
                    width: ringRadius * 2,
                    height: ringRadius * 2
                )
            )
            path.lineWidth = 1.6
            NSColor(calibratedRed: 1.0, green: 0.78, blue: 0.36, alpha: alpha).setStroke()
            path.stroke()
        }
    }

    /// 고른 방의 상태를 코어 바깥 고리 하나로만 알린다. 코어 자체는 늘
    /// 주황·금색이라 상태에 따라 색이 바뀌지 않는다.
    private func drawLevelRing(center: CGPoint, radius: Double) {
        let path = NSBezierPath(
            ovalIn: NSRect(
                x: center.x - radius,
                y: center.y - radius,
                width: radius * 2,
                height: radius * 2
            )
        )
        path.lineWidth = 1.5
        Palette.level(level).withAlphaComponent(0.5).setStroke()
        path.stroke()
    }
}


final class MenuPanelView: NSView {
    var model: MenubarModel {
        didSet { sync() }
    }
    let coreView = JarvisCoreView(frame: .zero)
    /// 코어 아래 한 줄. 지금 코어가 무엇을 하고 있는지 말로 알려 준다.
    private let coreCaption = NSTextField(labelWithString: "")
    weak var tileTarget: AnyObject?
    weak var hamburgerTarget: AnyObject?
    /// 우측 상단 톱니바퀴. 모든 제어와 설정은 이 단추 하나로 연다.
    let gearButton = NSButton(title: "", target: nil, action: #selector(AppDelegate.gearClicked(_:)))
    let roomButton = NSButton(title: "방", target: nil, action: #selector(AppDelegate.roomPickerClicked(_:)))
    var roomTitle = ""
    var selectedRoomId = 0
    static let panelWidth: CGFloat = 408
    static let roomGridColumns = 2
    static let roomCellHeight: CGFloat = 28
    static let roomGridGap: CGFloat = 6
    /// 위에서부터의 세로 리듬. 값 하나만 바꾸면 아래가 따라 움직이도록
    /// 조각을 이어 붙여 계산한다. 예전에는 7/36/94/98/158/186이 따로 박혀
    /// 있어 조각 사이 간격이 제각각이었다 (2026-09-16).
    /// 조각 사이 간격은 모두 같은 값을 쓴다. 예전에는 6/8/10이 섞여 있어
    /// 눈에 띄게 고르지 않았다 (2026-09-16).
    static let gap: CGFloat = 8
    static let panelTop: CGFloat = gap
    static let sideInset: CGFloat = 16
    static let statusPillHeight: CGFloat = 22
    static let afterStatusGap: CGFloat = gap
    /// 자비스 홀로그램 코어가 차지하는 정사각형의 한 변.
    ///
    /// 이 코어가 이 화면의 주인공이다. 예전에는 여기에 8단계 파이프라인
    /// 띠가 들어 있었는데, 단계 이름 여덟 개를 읽어야 현재 상태를 알 수
    /// 있어 한눈에 들어오지 않았다. 지금은 코어 하나가 상태를 대신하고,
    /// 단계는 창에서 본다 (2026-09-17).
    static let coreSize: CGFloat = 148
    static let afterCoreGap: CGFloat = gap
    static let afterRoomGridGap: CGFloat = gap
    static let tileHeight: CGFloat = 52
    static let afterTileGap: CGFloat = gap
    static let lampHeight: CGFloat = 18
    static let afterLampGap: CGFloat = gap
    static let actionHeight: CGFloat = 28
    static let bottomInset: CGFloat = gap
    /// 코어가 놓이는 위쪽 좌표(패널 위에서부터).
    static let coreTop: CGFloat = panelTop + statusPillHeight + afterStatusGap
    /// 코어 아래 한 줄이 차지하는 높이.
    static let coreCaptionHeight: CGFloat = 15
    /// 방 목록 격자가 시작하는 위쪽 좌표. 코어와 그 아래 한 줄에 붙는다.
    static let roomGridTop: CGFloat = coreTop + coreSize + coreCaptionHeight + afterCoreGap
    /// 지표 타일은 방 목록이 접혀 있을 때 격자 자리에서 바로 시작한다.
    static let tileTop: CGFloat = roomGridTop
    static let lampTop: CGFloat = tileTop + tileHeight + afterTileGap
    static let actionTop: CGFloat = lampTop + lampHeight + afterLampGap
    /// 상태 알약 + 홀로그램 코어 + 지표 타일 + 램프 줄 + 동작 버튼 + 아래 여백.
    static let panelBaseHeight: CGFloat = actionTop + actionHeight + bottomInset
    var roomsExpanded = false {
        didSet {
            guard roomsExpanded != oldValue else { return }
            rebuildRoomPopup()
            invalidateIntrinsicContentSize()
            needsLayout = true
        }
    }
    var roomRowButtons: [NSButton] = []
    /// 방 목록이 넘칠 때 굴리는 스크롤 뷰. 방이 적으면 만들지 않는다.
    private var roomGridScroll: NSScrollView?
    private var roomGridContent: NSView?
    var tileButtons: [NSButton] = []
    let autoButton = NSButton(title: "즉시 답장 보내기", target: nil, action: #selector(AppDelegate.instantAutoReplyClicked))
    let geekButton = NSButton(title: "긱뉴스 바로 전송", target: nil, action: #selector(AppDelegate.instantGeekNewsClicked))

    init(model: MenubarModel, frame: NSRect) {
        self.model = model
        super.init(frame: frame)
        coreView.frame = NSRect(
            x: (frame.width - Self.coreSize) / 2,
            y: Self.coreTop,
            width: Self.coreSize,
            height: Self.coreSize
        )
        coreView.autoresizingMask = [.minXMargin, .maxXMargin]
        coreView.level = model.level
        coreView.activity = JarvisCoreView.activity(
            pipeline: model.pipeline,
            openJobs: model.open_jobs,
            level: model.level
        )
        addSubview(coreView)
        coreCaption.font = NSFont.systemFont(ofSize: 11, weight: .medium)
        coreCaption.textColor = NSColor.secondaryLabelColor
        coreCaption.alignment = .center
        coreCaption.lineBreakMode = .byTruncatingTail
        addSubview(coreCaption)
        gearButton.bezelStyle = .inline
        gearButton.isBordered = false
        gearButton.controlSize = .regular
        gearButton.image = NSImage(
            systemSymbolName: "gearshape.fill",
            accessibilityDescription: "설정"
        )
        gearButton.imagePosition = .imageOnly
        gearButton.contentTintColor = NSColor.secondaryLabelColor
        gearButton.toolTip = "모든 설정과 제어를 엽니다"
        gearButton.identifier = NSUserInterfaceItemIdentifier("gear")
        addSubview(gearButton)
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
        return min(roomGridHeight(count: count), roomGridSpan(rows: maxRoomGridRows))
            + afterRoomGridGap
    }

    /// 방 목록이 차지할 수 있는 최대 줄 수.
    ///
    /// 방이 늘어날수록 패널이 그만큼 길어져 화면 아래로 넘어갔다. 넘어간
    /// 부분은 클릭할 수도 없어, 방을 여러 개 등록한 사람은 아래쪽 방을 아예
    /// 고를 수 없었다. 여섯 줄로 묶고 나머지는 스크롤로 본다 (2026-09-16).
    static let maxRoomGridRows = 6

    /// 방 목록 격자가 실제로 차지하는 높이.
    static func roomGridHeight(count: Int) -> CGFloat {
        let rooms = max(count, 1)
        let rows = min((rooms + roomGridColumns - 1) / roomGridColumns, maxRoomGridRows)
        return roomGridSpan(rows: rows)
    }

    /// 방 목록 격자 자체의 높이(배경 카드 여백은 뺀 값).
    static func roomGridSpan(rows: Int) -> CGFloat {
        CGFloat(rows) * roomCellHeight + CGFloat(max(rows - 1, 0)) * roomGridGap
    }

    func roomGridExtra() -> CGFloat {
        Self.roomGridExtra(count: AppDelegate.inspectableRooms(in: model).count, expanded: roomsExpanded)
    }

    func layoutWidth() -> CGFloat {
        max(bounds.width, Self.panelWidth)
    }

    /// 톱니바퀴가 차지하는 폭. 상태 알약 오른쪽 끝에 붙는다.
    static let gearWidth: CGFloat = 26

    /// 방 고르기 단추가 차지하는 폭.
    ///
    /// 예전에는 100pt로 박아 두어 "▸ 부자멘토멘티" 같은 제목이 56pt 잘렸다.
    /// 제목이 필요로 하는 만큼 주되, 설명 줄을 남겨 두고 패널 밖으로는
    /// 나가지 않게 한다 (2026-09-16).
    func roomButtonWidth() -> CGFloat {
        let needed = roomButton.attributedTitle.size().width + 24
        let room = max(96, layoutWidth() - Self.sideInset * 2 - 168 - Self.gearWidth)
        return min(max(needed, 96), room)
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
        roomGridScroll?.removeFromSuperview()
        roomGridScroll = nil
        roomGridContent = nil
        guard roomsExpanded else { return }
        // 방이 한 화면에 다 들어가면 스크롤 뷰를 만들지 않는다. 늘 스크롤
        // 뷰를 두면 방 두 개짜리 패널에도 스크롤 틀이 생겨 지저분하다
        // (2026-09-16).
        let needed = Self.roomGridHeight(count: rooms.count)
        let visible = Self.roomGridHeight(count: min(rooms.count, Self.maxRoomGridRows * Self.roomGridColumns))
        let scrolls = needed > visible + 0.5
        let gridHost: NSView
        if scrolls {
            let scroll = NSScrollView()
            scroll.translatesAutoresizingMaskIntoConstraints = true
            scroll.hasVerticalScroller = true
            scroll.hasHorizontalScroller = false
            scroll.autohidesScrollers = true
            scroll.drawsBackground = false
            scroll.borderType = .noBorder
            let content = FlippedContainerView(frame: NSRect(x: 0, y: 0, width: layoutWidth(), height: needed))
            scroll.documentView = content
            addSubview(scroll)
            roomGridScroll = scroll
            roomGridContent = content
            gridHost = content
        } else {
            gridHost = self
        }
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
            gridHost.addSubview(button)
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
        let pickerWidth = roomButtonWidth()
        gearButton.frame = NSRect(
            x: width - Self.sideInset - Self.gearWidth,
            y: Self.panelTop,
            width: Self.gearWidth,
            height: Self.statusPillHeight
        )
        roomButton.frame = NSRect(
            x: width - Self.sideInset - Self.gearWidth - 6 - pickerWidth,
            y: Self.panelTop,
            width: pickerWidth,
            height: Self.statusPillHeight
        )
        coreView.frame = NSRect(
            x: (width - Self.coreSize) / 2,
            y: Self.coreTop,
            width: Self.coreSize,
            height: Self.coreSize
        )
        coreCaption.frame = NSRect(
            x: Self.sideInset,
            y: Self.coreTop + Self.coreSize,
            width: width - Self.sideInset * 2,
            height: Self.coreCaptionHeight
        )
        let columns = Self.roomGridColumns
        let gap = Self.roomGridGap
        let cellH = Self.roomCellHeight
        let cellW = max(80, (width - 32 - gap) / CGFloat(columns))
        // 방 목록이 넘치면 스크롤 뷰가 그 자리를 차지하고, 단추들은 그 안쪽
        // 문서 좌표계에 놓인다. 스크롤 뷰 자체는 늘 보이는 만큼만 차지한다
        // (2026-09-16).
        let gridHeight = Self.roomGridHeight(count: max(roomRowButtons.count, 1))
        let visibleHeight = min(gridHeight, Self.roomGridSpan(rows: Self.maxRoomGridRows))
        if let scroll = roomGridScroll {
            scroll.frame = NSRect(
                x: Self.sideInset,
                y: Self.roomGridTop,
                width: width - Self.sideInset * 2,
                height: visibleHeight
            )
            roomGridContent?.frame = NSRect(
                x: 0,
                y: 0,
                width: width - Self.sideInset * 2,
                height: gridHeight
            )
        }
        let gridOriginX = roomGridScroll == nil ? Self.sideInset : 0
        let gridOriginY = roomGridScroll == nil ? Self.roomGridTop : 0
        for (index, button) in roomRowButtons.enumerated() {
            let col = index % columns
            let row = index / columns
            button.frame = NSRect(
                x: gridOriginX + CGFloat(col) * (cellW + gap),
                y: gridOriginY + CGFloat(row) * (cellH + gap),
                width: cellW,
                height: cellH
            )
        }
        let tileY: CGFloat = Self.tileTop + extra
        let actionY: CGFloat = Self.actionTop + extra
        let tileW = (width - Self.sideInset * 2 - 18) / 4
        for (index, button) in tileButtons.enumerated() {
            button.target = tileTarget
            button.frame = NSRect(
                x: Self.sideInset + CGFloat(index) * (tileW + 6),
                y: tileY,
                width: tileW,
                height: Self.tileHeight
            )
        }
        autoButton.target = tileTarget
        geekButton.target = tileTarget
        let buttonW = (width - Self.sideInset * 2 - 8) / 2
        autoButton.frame = NSRect(x: Self.sideInset, y: actionY, width: buttonW, height: Self.actionHeight)
        geekButton.frame = NSRect(
            x: Self.sideInset + buttonW + 8,
            y: actionY,
            width: buttonW,
            height: Self.actionHeight
        )
    }

    func sync() {
        let room = AppDelegate.selectedRoom(in: model, preferred: selectedRoomId)
        selectedRoomId = room?.chat_id ?? 0
        roomTitle = room?.title ?? "전체"
        let pipeline = room?.pipeline ?? model.pipeline
        coreView.level = room?.level ?? model.level
        let openJobs = room?.open_jobs ?? model.open_jobs
        coreView.activity = JarvisCoreView.activity(
            pipeline: pipeline,
            openJobs: openJobs,
            level: room?.level ?? model.level
        )
        coreCaption.stringValue = JarvisCoreView.caption(pipeline: pipeline, openJobs: openJobs)
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
        let statusPill = NSRect(
            x: Self.sideInset,
            y: Self.panelTop,
            width: 24 + titleSize.width,
            height: Self.statusPillHeight
        )
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
        // 방 고르기 단추가 방 이름을 이미 보여 주므로 설명 줄에서는 뺀다.
        // 예전에는 같은 이름이 "▸ 부자멘토멘티"와 "부자멘토멘티 · …"로 두 번
        // 나와서 한 줄을 두 번 읽어야 했다 (2026-09-16).
        let pickerWidth = roomButton.isHidden
            ? Self.gearWidth
            : roomButtonWidth() + 6 + Self.gearWidth
        let captionMax = max(40, width - Self.sideInset - pickerWidth - captionX)
        let status = Palette.caption(code: room?.codes.first ?? model.primary_code)
        let caption = NSString(string: roomButton.isHidden ? "\(roomTitle) · \(status)" : status)
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
        let tileY: CGFloat = Self.tileTop + extra
        let lampY: CGFloat = Self.lampTop + extra
        if roomsExpanded {
            let rows = (max(AppDelegate.inspectableRooms(in: model).count, 1) + Self.roomGridColumns - 1) / Self.roomGridColumns
            let card = NSRect(
                x: 12,
                y: Self.roomGridTop - 4,
                width: width - 24,
                height: Self.roomGridSpan(rows: rows) + 8
            )
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
            let rect = NSRect(x: x, y: tileY, width: tileW, height: Self.tileHeight)
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
            let chip = NSRect(x: x, y: lampY, width: 16 + labelSize.width + 8, height: Self.lampHeight)
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
            let pill = NSRect(x: slotX - size.width - 16, y: lampY, width: size.width + 14, height: Self.lampHeight)
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
final class CenteredLabelCell: NSTableCellView {
    let label: NSTextField

    /// 한 줄짜리 열은 끝을 자르고 전체 글자를 툴팁에 둔다. 설명처럼 긴 글이
    /// 창의 주 내용인 열은 두 줄로 접어 보여 준다. 한 줄로 고정하면 긴
    /// 설명이 잘린 채로 나온다 (2026-09-16).
    func setLines(_ lines: Int) {
        label.maximumNumberOfLines = lines
        label.usesSingleLineMode = lines <= 1
        label.lineBreakMode = lines <= 1 ? .byTruncatingTail : .byWordWrapping
        if let cell = label.cell as? NSTextFieldCell {
            cell.wraps = lines > 1
            cell.lineBreakMode = lines <= 1 ? .byTruncatingTail : .byWordWrapping
        }
    }

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
    var logSummary: NSTextField?
    var logHint: NSTextField?
    var logTable: NSTableView?
    var logTableScroll: NSScrollView?
    var logScopeButton: NSPopUpButton?
    var logRoomButton: NSPopUpButton?
    var logStatusField: NSTextField?
    var logEmptyLabel: NSTextField?
    var logEmptyState: EmptyStateView?
    var logDetailView: NSTextView?
    var logDetailCard: NSView?
    var receiptRows: [ReceiptRow] = []
    var displayedReceipts: [ReceiptRow] = []
    var receiptTitles: [(id: String, title: String)] = []
    var logScopes: [(code: String, title: String)] = []
    var logRoomIds: [String] = []
    var logScopeValue = ""
    var logRoomValue = ""
    var selectedReceiptKey = ""
    var lastReceiptFingerprint = ""
    var lastLogDetailKey = ""
    var logPageMissing = false
    var logPageUnread = false
    var logReadAt: Double = 0
    var roomsWindow: NSWindow?
    var roomsTable: NSTableView?
    var roomsFilterField: NSTextField?
    var roomsTableScroll: NSScrollView?
    var roomsStack: NSStackView?
    /// 사용자가 직접 크기를 바꾼 창은 자동으로 줄이지 않는다.
    var roomsWindowUserResized = false
    var roomsFitting = false
    var displayedChats: [AvailableChat] = []
    var allChats: [AvailableChat] = []
    var jobsWindow: NSWindow?
    var jobsTable: NSTableView?
    var jobsSummary: NSTextField?
    var displayedJobs: [JobRow] = []
    var jobsStatus = "open"
    var jobsSkipButton: NSButton?
    var jobsAckButton: NSButton?
    var jobsHint: NSTextField?
    var jobsEmptyLabel: NSTextField?
    var jobsEmptyState: EmptyStateView?
    /// 작업 목록 창의 세로 스택. 내용에 맞춰 창을 줄일 때 쓴다.
    var jobsStack: NSStackView?
    /// 사용자가 직접 크기를 바꾼 창은 자동으로 줄이지 않는다.
    var jobsWindowUserResized = false
    /// 작업 목록 표의 최소 높이. 줄 수에 맞춰 낮춘다.
    var jobsTableHeight: NSLayoutConstraint?
    var jobsFitting = false
    var jobsTableScroll: NSScrollView?
    var jobsTrace: NSTextView?
    /// 목록 조회가 실패했는지. 실패를 "0건"으로 보여 주면 운영자가 일이
    /// 없다고 믿는다 (2026-09-16).
    var jobsReadFailed = false
    var jobsTraceCard: NSView?
    var jobsActions: NSView?
    var jobsFilterControl: NSSegmentedControl?
    var selectedJobEventId = ""
    var roomsSelectedChatId = 0
    var lastRoomsFingerprint = ""
    var vectorWindow: NSWindow?
    var vectorTable: NSTableView?
    var vectorSummary: NSTextField?
    var vectorHint: NSTextField?
    var vectorEmptyLabel: NSTextField?
    var vectorEmptyState: EmptyStateView?
    var vectorTableScroll: NSScrollView?
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
    var vectorSourceKind = "knowledge_graph"
    var vectorTopicKey = ""
    var vectorSourceButton: NSPopUpButton?
    var vectorTopicButton: NSPopUpButton?
    let vectorSourceTitles = ["지식 그래프", "최연우 기억", "모든 대화", "주제별 지식", "설명 자료", "답장 기록", "말투·반응 통계", "탐색 프롬프트"]
    let vectorSourceKeys = ["knowledge_graph", "style", "messages", "topics", "references", "replies", "profiles", "prompts"]
    var vectorRestoreButton: NSButton?
    // 신경망 보기(뉴런·시냅스). 지식 그래프를 고를 때만 보인다.
    var vectorGraphView: KnowledgeGraphView?
    var vectorGraphStack: NSStackView?
    /// 캔버스 최소 높이. 그래프를 못 읽어 캔버스를 접을 때 함께 끈다.
    var vectorGraphHeightConstraint: NSLayoutConstraint?
    var vectorEditCard: NSView?
    var vectorPager: NSView?
    var vectorStack: NSStackView?
    /// 사용자가 직접 크기를 바꾼 창은 자동으로 줄이지 않는다.
    var vectorWindowUserResized = false
    /// 자동 축소가 스스로를 다시 부르지 않게 막는다.
    var vectorFitting = false
    var vectorGraphHint: NSTextField?
    var vectorGraphToken = 0
    /// 마지막으로 읽은 그래프의 규모. 힌트 줄을 다시 쓸 때 쓴다.
    var vectorGraphTotal = 0
    var vectorGraphGrounded = 0
    /// 색인이 마지막으로 끝난 시각과, 지금 재색인이 도는 중인지.
    var vectorGraphIndexedAt = 0
    var vectorGraphIsStale = false
    var lastVectorGraphReadAt = Date.distantPast
    var lastVectorGraphSource = ""
    /// 그래프 화면 상태. 실패와 "아직 데이터 없음"을 같은 문구로 보여 주면
    /// 첫 조회가 타임아웃 났을 때 사용자가 색인이 비었다고 오해한다
    /// (2026-09-17, 6 Pro 지적).
    enum VectorGraphPhase {
        case idle
        case loading
        case ready
        case empty
        case error
        case stale
    }
    var vectorGraphPhase: VectorGraphPhase = .idle
    /// 재시도 버튼은 실패했을 때만 보여 준다.
    var vectorGraphRetryButton: NSButton?
    var vectorGraphRetryHandler: (() -> Void)?
    // 모델 설정 창 (답변 모델 + 이미지 모델 통합, R5)
    var modelWindow: NSWindow?
    var modelReplyPopup: NSPopUpButton?
    var modelImagePopup: NSPopUpButton?
    var modelReplySummary: NSTextField?
    var modelHardwareHint: NSTextField?
    var modelImageSummary: NSTextField?
    var modelStatusField: NSTextField?
    var modelReplyStatus: NSTextField?
    var modelImageStatus: NSTextField?
    var modelRevertButton: NSButton?
    /// 같은 값이 두 곳에 남지 않도록, 되돌리기에 필요한 직전 선택만 보관한다.
    var lastReplySelection: ReplyModelSelection?
    var lastImageSelection: ReplyModelSelection?
    // 폴백 사슬 (모델 설정 창의 한 섹션). 순서·출처는 코어가 계산한 값을 그대로 쓴다.
    var modelFallbackRows: NSStackView?
    /// 내용 높이에 맞춰 창을 줄일 때, 같은 값으로 반복해서 흔들지 않도록 기억한다.
    var lastModelContentHeight: CGFloat = 0
    /// 모델 창이 넘지 않을 높이. 폴백을 최대치까지 넣어도 화면 안에 남는다.
    static let modelWindowHeightLimit: CGFloat = 760
    /// 지식 그래프 창이 넘지 않을 높이. 화면보다 커지면 아래 조작이 화면 밖으로
    /// 나가므로 24인치 화면에서도 남는 값으로 잡는다 (2026-09-16).
    static let vectorWindowHeightLimit: CGFloat = 900
    /// 이 창이 어떤 보기에서도 내려가지 않는 크기. 그래프가 뭉개지지 않을
    /// 만큼은 남긴다.
    static let vectorWindowFloor = NSSize(width: 700, height: 560)
    var modelSettingsStack: NSStackView?
    var modelSettingsScroll: NSScrollView?
    /// 사용자가 창 크기를 직접 만졌으면 자동 축소를 하지 않는다.
    var modelWindowUserResized = false
    /// 감사 모드에서 함께 재는 화면 밖 뷰(메뉴 패널 등).
    var layoutAuditPanels: [(String, NSWindow, NSView)] = []
    var modelFallbackAddButton: NSPopUpButton?
    var modelFallbackResetButton: NSButton?
    var modelFallbackRestoreButton: NSButton?
    var modelFallbackStatus: NSTextField?
    var modelFallbackChain: [String] = []
    var modelFallbackSource = "default"
    var modelFallbackMax = 6
    var modelFallbackBusy = false
    /// 코어가 알려 준 "지금 카탈로그에 없는 저장 모델"과 "지금 답변 모델".
    var modelFallbackUnknown: [String] = []
    var modelFallbackPrimary = ""
    /// 저장을 확인한 뒤에도 오래된 조회가 화면을 되돌리지 않게, 확인된 값을 잠시 들고 있는다.
    var modelFallbackLocalOverride: (models: [String], source: String)?
    /// 마지막으로 반영한 저장 리비전(파일 mtime). 이보다 오래된 조회는
    /// 무시하고, 더 새로운 변경은 다른 창·CLI에서 왔더라도 반영한다.
    var modelFallbackRevision: Int64 = 0
    var modelFallbackNote: String?
    /// 추가 메뉴를 마지막으로 그린 내용. 갱신마다 다시 만들면 펼쳐 둔 하위
    /// 메뉴가 닫히므로, 내용이 그대로면 손대지 않는다.
    var modelFallbackMenuSignature = ""
    var menuPanel: MenuPanelView?
    var inspectedRoomId = 0
    var roomsListExpanded = false
    var menuTracking = false
    var refreshInFlight = false
    var refreshQueued = false
    var lastStatusImageKey = ""
    var lastApplySignature = ""
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
    /// 목록이 찼을 때 지키는 최소 높이. 표가 다섯 줄은 보여야 한다.
    static let jobsWindowMinimum = NSSize(width: 620, height: 400)
    /// 목록이 비었을 때의 최소 높이. 안내 한 줄과 단추만 남으므로 하한을
    /// 낮춘다. 낮추지 않으면 빈 안내 판이 263pt로 늘어난다 (2026-09-16).
    static let jobsWindowEmptyFloor = NSSize(width: 620, height: 240)
    /// 표가 한 번에 보여 주는 최대 높이(머리글 28 + 다섯 줄 170 + 양식 여백
    /// 10). 이보다 줄이 많으면 표 안에서 굴린다 (2026-09-17).
    ///
    /// 양식 여백을 빼고 200으로 두었더니 표가 클립 뷰보다 8pt 커져 마지막
    /// 줄 아래가 잘렸다. 잘린 자리는 아무것도 그리지 않아 흰 띠로 남는다
    /// (2026-09-18).
    static let jobsTableMaximumHeight: CGFloat = 210
    /// `.inset` 양식이 첫 줄 위와 마지막 줄 아래에 두는 세로 여백의 합.
    static let jobsTableStylePadding: CGFloat = 10

    init(config: Config) {
        self.config = config
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(
            atPath: config.logsDir,
            withIntermediateDirectories: true
        )
        if !config.layoutAudit.isEmpty {
            // 런루프가 한 바퀴 돈 뒤에 재야 AppKit이 배치를 끝낸 상태가 된다.
            let directory = config.layoutAudit
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                let summary = self.runLayoutAudit(directory)
                FileHandle.standardError.write(Data(("layout-audit: " + summary + "\n").utf8))
                NSApp.terminate(nil)
            }
            return
        }
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
        // 조회를 시작한 시점의 변경 세대를 기억한다. 도착했을 때 세대가 바뀌었으면
        // 모델 선택값은 반영하지 않는다(오래된 조회가 완료된 선택을 덮지 않게).
        let fetchToken = modelChangeToken
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            let model = self.loadModel() ?? Self.unavailableModel()
            DispatchQueue.main.async {
                self.refreshInFlight = false
                self.apply(model, modelToken: fetchToken)
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
        runPythonOutcome(extra, timeout: timeout).data
    }

    /// 시간 초과와 실패를 구분해서 돌려준다. 저장 요청이 제한 시간을 넘겼을 때
    /// "저장하지 못했어요"라고 단정하면, 코어에는 이미 저장됐는데 화면만
    /// 되돌리는 경우가 생긴다(2026-09-15).
    func runPythonOutcome(
        _ extra: [String] = [],
        timeout: TimeInterval = 8
    ) -> (data: Data?, timedOut: Bool) {
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
            return (nil, false)
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
        // terminate()가 실제로 먹혔는지로 시간 초과를 판정한다. 종료 코드만
        // 보면 신호로 끝난 정상 실패와 구분되지 않는다.
        let timedOut = process.terminationReason == .uncaughtSignal
        guard process.terminationStatus == 0, !data.isEmpty else {
            return (nil, timedOut)
        }
        return (data, timedOut)
    }

    func loadModel() -> MenubarModel? {
        // 스냅샷은 커다란 컨텍스트 DB를 읽는다. 기본 8초는 호스트가 DB를 쓸 때
        // 자주 넘겨 메뉴가 붉게 깜빡였다(2026-09-13). 여유를 준다.
        guard let data = runPython([], timeout: 25) else { return nil }
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
        // 한 배열 리터럴에 열아홉 조각을 넣으면 타입 검사기가 포기한다.
        // CI 러너에서 실제로 "unable to type-check this expression in
        // reasonable time"으로 빌드가 멈췄다 (2026-09-16). 조각을 변수로
        // 나눠 두면 각 줄이 독립적으로 검사된다.
        let fields: [String] = [
            model.level,
            model.primary_code,
            model.reply_model?.id ?? "",
            model.image_reply_model?.id ?? "",
            replyModels,
            model.codes.joined(separator: ","),
            model.watermark ?? "",
            String(model.open_jobs),
        ]
        let counters: [String] = [
            String(model.sent),
            String(model.skipped),
            String(model.delivery_unknown),
        ]
        let body: [String] = [
            model.geeknews_slots.joined(separator: ","),
            stages,
            rooms,
        ]
        let tail: [String] = [
            roomsFingerprint(model.available_chats ?? []),
            model.vector_memory?.fingerprint ?? "",
            model.log_summary ?? "",
            logs,
            model.reply_receipts?.fingerprint ?? "",
        ]
        let all = fields + counters + body + tail
        return all.joined(separator: "|")
    }

    func apply(_ model: MenubarModel, modelToken: Int) {
        // 조회가 시작된 뒤 변경 세대가 달라졌다면 선택값 반영을 버린다.
        let generationCurrent = modelToken == modelChangeToken
        lastModel = model
        if let current = model.reply_model, !current.id.isEmpty {
            let live = currentReplyModel
            let authoritativeIdle = current.enabled == false || current.source == "unused"
            let snapshotLag = !authoritativeIdle
                && live?.source == "override"
                && current.source != "override"
                && current.id != live?.id
            // 변경이 진행 중이거나 조회 후 세대가 바뀌었으면 선택값을 덮지 않는다.
            if !snapshotLag && !modelChangeInFlight && generationCurrent {
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
            if !snapshotLag && !modelChangeInFlight && generationCurrent {
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
        // 이 메뉴에는 패널 하나만 들어갑니다.
        //
        // 예전에는 패널 아래에 "답변 기록 보기…", "AI 모델 설정…", "채팅방
        // 관리…", "지식 그래프 (대화 기억)…", "메뉴 종료"가 줄줄이 늘어서
        // 있었습니다. 메뉴를 열 때마다 이 다섯 줄을 다시 읽어야 했고, 무엇을
        // 먼저 눌러야 하는지도 알 수 없었습니다. 지금은 패널 우측 상단
        // 톱니바퀴 하나가 그 창들을 모두 엽니다 (2026-09-17).
        return menu
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

    enum ModelRowPhase { case idle, applying, verifying, applied, failed }

    struct ModelRowState {
        var phase: ModelRowPhase = .idle
        var message: String?
        var targetId: String?
    }

    enum ReplyModelTarget: String { case reply, image }

    struct ModelRevertRecord {
        let target: ReplyModelTarget
        let selection: ReplyModelSelection
    }

    /// 행별 상태는 새로고침이 덮지 않는다. 화면은 이 상태만 렌더링한다.
    var modelReplyState = ModelRowState()
    var modelImageState = ModelRowState()
    /// 적용이 끝나기 전에 다른 변경이 끼어들지 않게 한다.
    var modelChangeInFlight = false
    /// 변경마다 증가하는 토큰. 오래된 재확인·완료 처리가 새 변경을 덮지 않게 한다.
    var modelChangeToken = 0
    var lastModelChange: ModelRevertRecord?

    /// 답변 모델을 바꾼다. 진행 중 답변은 옛 설정으로 끝까지 완료되고, 다음
    /// 답변부터 새 모델을 씁니다 (R5.5, R5.6은 코어의 스냅샷 스왑이 보장).
    func setReplyModel(id: String, label: String) {
        applyModelChange(.reply, id: id, label: label)
    }

    /// 이미지 모델을 바꾼다 (R5.1, R5.4). 실패 시 이전 설정 유지 (R5.7).
    func setImageModel(id: String, label: String) {
        applyModelChange(.image, id: id, label: label)
    }

    func setRowState(_ target: ReplyModelTarget, _ state: ModelRowState) {
        if target == .reply {
            modelReplyState = state
        } else {
            modelImageState = state
        }
    }

    /// 새 변경이 무효화한 이전 확인 작업의 행을 종료 상태로 남긴다. 토큰이 바뀌면
    /// 예약된 재확인은 조용히 반환하므로, 그 행의 "확인 중" 문구가 영영 남지 않게
    /// 여기서 명시적으로 끝낸다. 공통 잠금(finishModelChange)은 건드리지 않는다.
    func cancelStaleVerification(_ target: ReplyModelTarget) {
        let state = target == .reply ? modelReplyState : modelImageState
        guard state.phase == .verifying else { return }
        let label = target == .reply ? "답변 모델" : "이미지 모델"
        setRowState(
            target,
            ModelRowState(
                phase: .failed,
                message: "\(label)의 적용 결과를 확인하지 못했습니다. 모델을 다시 선택해 주세요.",
                targetId: nil
            )
        )
    }

    /// 저장 응답에서 실제로 저장된 모델 ID를 읽는다.
    func storedModelId(_ data: Data?) -> String? {
        guard let data,
              let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
              report.ok == true,
              let rid = report.model, !rid.isEmpty
        else { return nil }
        return rid
    }

    /// 저장된 이미지 모델 ID를 답변 목록 응답에서 읽는다. 답변 모델과 섞이지 않게
    /// 별도 필드(image_model)만 본다.
    func storedImageModelId(_ data: Data?) -> String? {
        guard let data,
              let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
              report.ok == true,
              let rid = report.image_model, !rid.isEmpty
        else { return nil }
        return rid
    }

    /// 준비 단계 응답이 실제로 준비 완료를 보고했는지 확인한다.
    func isPrepared(_ data: Data?) -> Bool {
        guard let data,
              let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
              report.ok == true
        else { return false }
        return report.prepared == true
    }

    /// 준비(oMLX 상주) 단계가 필요한 모델인지 저장 응답으로 판단한다.
    func needsPrepare(_ data: Data?) -> Bool {
        guard let data,
              let report = try? JSONDecoder().decode(ModelsReport.self, from: data)
        else { return false }
        return report.needs_prepare == true && report.prepared != true
    }

    /// 모델 변경을 한 곳에서 처리한다: 저장 → 저장값 확인 → (준비가 필요한 모델만) 준비.
    /// 저장값을 확인하기 전에는 성공도 "이전 모델 유지"도 단정하지 않는다.
    func applyModelChange(_ target: ReplyModelTarget, id: String, label: String) {
        guard !id.isEmpty, !modelChangeInFlight else { return }
        modelChangeToken += 1
        let token = modelChangeToken
        // 다른 행에 남은 예약 확인은 이 토큰 변경으로 무효가 되므로, 그 행을 끝낸다.
        cancelStaleVerification(target == .reply ? .image : .reply)
        let previous = target == .reply ? currentReplyModel : currentImageReplyModel
        modelChangeInFlight = true
        if target == .reply {
            applyReplyModelSelection(replyModelSelection(id: id, label: label, source: "override"))
            setRowState(target, ModelRowState(phase: .applying, message: "적용 중…", targetId: id))
            modelStatusField?.stringValue = "답변 모델을 바꾸는 중이에요…"
        } else {
            applyImageReplyModelSelection(replyModelSelection(id: id, label: label, source: "override"))
            setRowState(target, ModelRowState(phase: .applying, message: "적용 중…", targetId: id))
            modelStatusField?.stringValue = "이미지 모델을 바꾸는 중이에요…"
        }
        refreshModelSettingsWindowIfOpen()
        let action = target == .reply ? "model-set" : "image-model-set"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            // 1단계: 저장만 한다(준비 대기 없음).
            var saveArgs = ["--action", action, "--model", id]
            if target == .reply { saveArgs.append("--no-wait") }
            let saveData = self.runPython(saveArgs, timeout: 45)
            let savedId = self.storedModelId(saveData)
            if savedId == nil {
                // 응답을 받지 못한 것만으로 저장되지 않았다고 단정할 수 없다.
                // 저장값을 다시 읽어 확인하고, 확인 전에는 성공/유지 어느 쪽도 말하지 않는다.
                DispatchQueue.main.async {
                    self.setRowState(target, ModelRowState(phase: .verifying, message: "결과 확인 중…", targetId: id))
                    self.modelStatusField?.stringValue = "적용 결과를 확인하는 중이에요…"
                    self.refreshModelSettingsWindowIfOpen()
                }
                let modelsData = self.runPython(["--action", "models"], timeout: 45)
                // 답변은 답변 목록에서, 이미지는 image_model 필드에서 저장값을 확인한다.
                let readBack = target == .reply
                    ? self.storedModelId(modelsData)
                    : self.storedImageModelId(modelsData)
                // 복구 경로도 준비 완료 조건을 똑같이 적용한다. ID만 같다고 완료가 아니다.
                // 준비는 저장을 다시 하지 않는 model-prepare로만 확인한다.
                var recoveredPrepared = false
                if readBack == id && target == .reply {
                    let prepData = self.runPython(["--action", "model-prepare", "--model", id], timeout: 200)
                    recoveredPrepared = self.isPrepared(prepData)
                }
                DispatchQueue.main.async {
                    guard self.modelChangeToken == token else { return }
                    if readBack == id && target == .image {
                        self.finishModelChange(
                            target,
                            phase: .applied,
                            previous: previous,
                            applied: self.replyModelSelection(id: id, label: label, source: "override"),
                            rowMessage: nil,
                            status: "저장됨"
                        )
                    } else if readBack == id && recoveredPrepared {
                        self.finishModelChange(
                            target,
                            phase: .applied,
                            previous: previous,
                            applied: self.replyModelSelection(id: id, label: label, source: "override"),
                            rowMessage: nil,
                            status: target == .reply ? "적용됨" : "저장됨"
                        )
                    } else {
                        let message = "적용 결과를 확인하지 못했습니다. 현재 설정을 다시 확인하고 있습니다."
                        self.finishModelChange(target, phase: .verifying, previous: nil, rowMessage: message, status: message)
                        self.scheduleVerificationRetry(target, id: id, previous: previous, attempt: 1, token: token)
                    }
                }
                return
            }
            guard savedId == id else {
                DispatchQueue.main.async {
                    self.finishModelChange(
                        target,
                        phase: .failed,
                        previous: previous,
                        rowMessage: "변경하지 못했습니다. 이전 모델을 유지합니다.",
                        status: "변경하지 못했어요. 값을 확인한 뒤 다시 골라 주세요."
                    )
                }
                return
            }
            // 2단계: 저장값을 다시 읽어 확인한다(성공 문구를 먼저 쓰지 않는다).
            if target == .reply {
                DispatchQueue.main.async {
                    self.setRowState(target, ModelRowState(phase: .verifying, message: "결과 확인 중…", targetId: id))
                    self.modelStatusField?.stringValue = "적용 결과를 확인하는 중이에요…"
                    self.refreshModelSettingsWindowIfOpen()
                }
                let readData = self.runPython(["--action", "models"], timeout: 45)
                let readBack = self.storedModelId(readData)
                if readBack != id {
                    let message = readBack == nil
                        ? "적용 결과를 확인하지 못했습니다. 현재 설정을 다시 확인하고 있습니다."
                        : "저장된 설정이 바뀌었을 수 있어요. 모델을 다시 골라 주세요."
                    DispatchQueue.main.async {
                        guard self.modelChangeToken == token else { return }
                        self.finishModelChange(
                            target,
                            phase: .verifying,
                            previous: nil,
                            rowMessage: message,
                            status: message
                        )
                        self.scheduleVerificationRetry(target, id: id, previous: previous, attempt: 1, token: token)
                    }
                    return
                }
            }
            // 3단계: 준비가 필요한 모델만 준비 단계를 따로 확인한다.
            if target == .reply && self.needsPrepare(saveData) {
                // 준비는 저장을 다시 하지 않는다. 저장된 선택값을 덮어쓰지 않는다.
                let prepData = self.runPython(["--action", "model-prepare", "--model", id], timeout: 200)
                if !self.isPrepared(prepData) {
                    // 저장은 확인됐어도 준비가 확인되지 않으면 완료라고 말하지 않는다.
                    let message = "모델 준비를 확인하지 못했습니다. 다음 답변에서 다시 시도합니다."
                    DispatchQueue.main.async {
                        self.finishModelChange(
                            target,
                            phase: .verifying,
                            previous: nil,
                            rowMessage: message,
                            status: message
                        )
                    }
                    return
                }
            }
            DispatchQueue.main.async {
                self.finishModelChange(
                    target,
                    phase: .applied,
                    previous: previous,
                    applied: self.replyModelSelection(id: id, label: label, source: "override"),
                    rowMessage: nil,
                    status: target == .reply ? "적용됨" : "저장됨"
                )
            }
        }
    }

    /// 변경 시도의 끝을 한 곳에서 정리한다. 저장값이 확인된 경우에만 되돌리기 이력을 남긴다.
    func finishModelChange(
        _ target: ReplyModelTarget,
        phase: ModelRowPhase,
        previous: ReplyModelSelection?,
        applied: ReplyModelSelection? = nil,
        rowMessage: String?,
        status: String
    ) {
        modelChangeInFlight = false
        let label = target == .reply ? "답변 모델" : "이미지 모델"
        setRowState(target, ModelRowState(phase: phase, message: rowMessage, targetId: nil))
        switch phase {
        case .applied:
            // 확인된 새 선택값을 다시 세팅한다. 늦게 도착한 조회가 이전 값으로
            // 되돌려 놓았어도 화면과 저장값이 어긋나지 않는다.
            if let applied, !applied.id.isEmpty {
                setTargetSelection(target, applied)
            }
            if let previous, !previous.id.isEmpty {
                lastModelChange = ModelRevertRecord(target: target, selection: previous)
                if target == .reply {
                    lastReplySelection = previous
                } else {
                    lastImageSelection = previous
                }
            }
            // 창 머리말이 "다음 턴부터 적용됩니다"를 이미 말한다. 여기서는
            // 무엇을 바꿨는지만 적는다 (2026-09-16).
            modelStatusField?.stringValue = "\(label)을(를) 바꿨어요."
        case .failed:
            if let previous { setTargetSelection(target, previous) }
            modelStatusField?.stringValue = "\(label)을(를) 바꾸지 못했어요. 값을 확인한 뒤 다시 골라 주세요."
        default:
            modelStatusField?.stringValue = status
        }
        modelRevertButton?.isEnabled = lastModelChange != nil
        modelRevertButton?.title = lastModelChange.map {
            $0.target == .reply ? "답변 모델 되돌리기" : "이미지 모델 되돌리기"
        } ?? "되돌리기"
        refreshModelSettingsWindowIfOpen()
    }

    /// 확인하지 못한 변경을 실제로 다시 확인한다. 문구만 남기고 끝내지 않는다.
    /// 다른 변경 때문에 기다린 횟수(waited)와 실제 확인 횟수(attempt)를 구분해,
    /// 대기로 소진해도 반드시 종료 상태로 끝난다.
    func scheduleVerificationRetry(
        _ target: ReplyModelTarget,
        id: String,
        previous: ReplyModelSelection?,
        attempt: Int,
        waited: Int = 0,
        token: Int
    ) {
        guard attempt <= 3 else { return }
        let delay = Double(attempt) * 20.0
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self else { return }
            guard self.modelChangeToken == token else { return }
            if self.modelChangeInFlight {
                if waited < 3 {
                    self.scheduleVerificationRetry(
                        target, id: id, previous: previous,
                        attempt: attempt, waited: waited + 1, token: token
                    )
                } else {
                    let message = "적용 결과를 확인하지 못했습니다. 모델을 다시 골라 주세요."
                    self.finishModelChange(
                        target, phase: .failed, previous: nil, rowMessage: message, status: message
                    )
                }
                return
            }
            DispatchQueue.global(qos: .userInitiated).async {
                let modelsData = self.runPython(["--action", "models"], timeout: 45)
                // 답변은 답변 목록에서, 이미지는 image_model 필드에서 저장값을 확인한다.
                let readBack = target == .reply
                    ? self.storedModelId(modelsData)
                    : self.storedImageModelId(modelsData)
                var retryPrepared = false
                if readBack == id && target == .reply {
                    // 준비는 저장을 다시 하지 않는 model-prepare로만 확인한다.
                    let prepData = self.runPython(["--action", "model-prepare", "--model", id], timeout: 200)
                    retryPrepared = self.isPrepared(prepData)
                }
                DispatchQueue.main.async {
                    // 오래된 재확인은 새 변경을 건드리지 않는다.
                    guard self.modelChangeToken == token else { return }
                    if readBack == id && target == .image {
                        self.finishModelChange(
                            target,
                            phase: .applied,
                            previous: previous,
                            applied: self.replyModelSelection(id: id, label: id, source: "override"),
                            rowMessage: nil,
                            status: "저장됨"
                        )
                    } else if readBack == id && retryPrepared {
                        self.finishModelChange(
                            target,
                            phase: .applied,
                            previous: previous,
                            applied: self.replyModelSelection(id: id, label: id, source: "override"),
                            rowMessage: nil,
                            status: target == .reply ? "적용됨" : "저장됨"
                        )
                    } else if attempt < 3 {
                        self.scheduleVerificationRetry(
                            target, id: id, previous: previous,
                            attempt: attempt + 1, waited: 0, token: token
                        )
                    } else {
                        self.finishModelChange(
                            target,
                            phase: .failed,
                            previous: nil,
                            rowMessage: "적용 결과를 확인하지 못했습니다. 모델을 다시 골라 주세요.",
                            status: "적용 결과를 확인하지 못했어요. 모델을 다시 골라 주세요."
                        )
                    }
                }
            }
        }
    }

    func setTargetSelection(_ target: ReplyModelTarget, _ selection: ReplyModelSelection) {
        if target == .reply {
            applyReplyModelSelection(selection)
        } else {
            applyImageReplyModelSelection(selection)
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

    // MARK: - 폴백 모델 사슬 (모델 설정 창)

    /// 폴백 사슬을 코어에 저장한다. 화면은 저장이 확인된 뒤에만 바뀐 값을 확정하고,
    /// 실패하면 이전 목록으로 되돌린다(답변/이미지 모델과 같은 규약).
    /// 폴백 사슬을 코어에 저장한다. clear는 저장한 목록을 지워 내장 기본값으로
    /// 돌리는 요청이고, 빈 목록은 "폴백 없음"이라는 다른 요청이다 — 한도에
    /// 걸리면 다른 모델로 넘어가지 않고 답변을 건너뛴다.
    func saveFallbackChain(_ models: [String], note: String, clear: Bool = false) {
        guard !modelFallbackBusy else { return }
        let previousChain = modelFallbackChain
        let previousSource = modelFallbackSource
        modelFallbackBusy = true
        modelFallbackNote = nil
        refreshModelSettingsWindowIfOpen()
        // 모델 ID에는 쉼표가 들어갈 수 없으므로 목록은 CSV 한 인자로 보낸다.
        let csv = models.joined(separator: ",")
        var extra = ["--action", "fallback-models-set", "--models", csv]
        if clear { extra.append("--clear") }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let outcome = self.runPythonOutcome(extra, timeout: 12)
            let data = outcome.data
            let report = data.flatMap { try? JSONDecoder().decode(ModelsReport.self, from: $0) }
            DispatchQueue.main.async {
                self.modelFallbackBusy = false
                if let report, report.ok == true, let saved = report.fallback_models {
                    self.modelFallbackChain = saved
                    self.modelFallbackSource = report.fallback_source ?? "override"
                    if let max = report.fallback_max {
                        self.modelFallbackMax = max
                    }
                    // 저장 뒤 리비전을 올려, 저장 전에 읽힌 조회가 화면을
                    // 되돌리지 않게 한다. 파일을 지우는 복원은 0을 보내므로
                    // 이전 값을 그대로 둔다(2026-09-15).
                    if let revision = report.fallback_revision, revision > 0 {
                        self.modelFallbackRevision = revision
                    }
                    // 저장이 확인된 값을 잠근다 — 늦게 도착한 조회가 화면을 되돌리지 않게.
                    self.modelFallbackLocalOverride = (models: saved, source: self.modelFallbackSource)
                    self.modelFallbackNote = note
                } else if outcome.timedOut {
                    // 저장 자체는 끝났을 수 있다. 실패로 단정하지 않고, 다시
                    // 읽어 확인하라고 안내한다(2026-09-15).
                    self.modelFallbackChain = previousChain
                    self.modelFallbackSource = previousSource
                    self.modelFallbackNote = "저장 결과를 확인하지 못했어요 · 잠시 뒤 실제 저장값이 자동으로 반영됩니다."
                    self.refreshModelSettingsWindowIfOpen()
                } else {
                    self.modelFallbackChain = previousChain
                    self.modelFallbackSource = previousSource
                    self.modelFallbackNote = "저장하지 못했어요. 값을 확인한 뒤 다시 시도해 주세요."
                    self.presentOperatorResult(action: "fallback-models-set", data: data)
                }
                self.refreshModelSettingsWindowIfOpen()
            }
        }
    }

    @objc func modelFallbackAdd(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String, !id.isEmpty else { return }
        guard !modelFallbackChain.contains(id) else { return }
        guard modelFallbackChain.count < modelFallbackMax else { return }
        var next = modelFallbackChain
        next.append(id)
        saveFallbackChain(next, note: "폴백을 추가했어요")
    }

    @objc func modelFallbackMoveUp(_ sender: NSButton) {
        let index = sender.tag
        guard index > 0, index < modelFallbackChain.count else { return }
        var next = modelFallbackChain
        next.swapAt(index, index - 1)
        saveFallbackChain(next, note: "폴백 순서를 바꿨어요")
    }

    @objc func modelFallbackMoveDown(_ sender: NSButton) {
        let index = sender.tag
        guard index >= 0, index < modelFallbackChain.count - 1 else { return }
        var next = modelFallbackChain
        next.swapAt(index, index + 1)
        saveFallbackChain(next, note: "폴백 순서를 바꿨어요")
    }

    @objc func modelFallbackRemove(_ sender: NSButton) {
        let index = sender.tag
        guard index >= 0, index < modelFallbackChain.count else { return }
        var next = modelFallbackChain
        next.remove(at: index)
        saveFallbackChain(
            next,
            note: next.isEmpty
                ? "폴백을 모두 비웠어요 · 앞 모델이 한도에 걸리면 답변을 건너뜁니다"
                : "폴백을 뺐어요"
        )
    }

    /// 폴백을 모두 비운다. 저장 목록을 빈 목록으로 남기므로, 앞 모델이 한도에
    /// 걸려도 다른 모델로 넘어가지 않고 그 턴을 건너뛴다. 내장 기본값으로
    /// 되돌리는 것과는 다른 상태다(2026-09-15).
    @objc func modelFallbackClearClicked(_ sender: Any?) {
        guard !modelFallbackChain.isEmpty else { return }
        saveFallbackChain(
            [],
            note: "폴백을 모두 비웠어요 · 앞 모델이 한도에 걸리면 답변을 건너뜁니다"
        )
    }

    /// 저장한 목록을 지워 내장 기본값으로 되돌린다.
    @objc func modelFallbackResetClicked(_ sender: Any?) {
        saveFallbackChain(
            [],
            note: "내장 기본값으로 복원했어요",
            clear: true
        )
    }

    func modelProvidersForWindow() -> [ReplyModelProvider] {
        let live = lastModel?.reply_model_providers ?? []
        return catalogProviders.isEmpty ? live : catalogProviders
    }

    /// 코어가 보낸 폴백 사슬을 화면 상태로 옮긴다. 저장 확인이 끝난 직후에는
    /// 방금 저장한 값을 유지해, 늦게 도착한 조회가 화면을 되돌리지 않게 한다.
    func applyFallbackState(_ state: ReplyModelFallbacks?) {
        guard let state else { return }
        let source = (state.source ?? "default").lowercased()
        let revision = state.revision ?? 0
        if let local = modelFallbackLocalOverride {
            // 로컬 확인값이 코어 값과 같아지면 더 이상 붙들지 않는다.
            if local.source == source, local.models == (state.models ?? []) {
                modelFallbackLocalOverride = nil
            } else if revision > modelFallbackRevision {
                // 저장 리비전이 내가 들고 있는 값보다 새롭다 = 다른 창이나 CLI가
                // 그 사이에 바꿨다. 내 확인값을 놓고 새 값을 그대로 반영한다.
                modelFallbackLocalOverride = nil
            } else {
                // 내 저장보다 먼저 읽힌 조회다. 화면을 되돌리지 않는다.
                return
            }
        }
        if revision > modelFallbackRevision {
            modelFallbackRevision = revision
        }
        modelFallbackChain = state.models ?? []
        modelFallbackSource = source
        modelFallbackMax = max(state.max ?? 6, 1)
        modelFallbackUnknown = state.unknown ?? []
        modelFallbackPrimary = state.primary ?? ""
    }

    /// 폴백 모델 선택 목록: 현재 답변/이미지 모델을 뺀, 화면에 보이는 모든 모델.
    func fallbackCandidateProviders() -> [ReplyModelProvider] {
        let providers = modelProvidersForWindow()
        let current = currentReplyModel ?? lastModel?.reply_model
        let currentId = current?.id ?? ""
        let imageId = (currentImageReplyModel ?? lastModel?.image_reply_model)?.id ?? ""
        let blocked = Set([currentId, imageId].filter { !$0.isEmpty })
        return providers.map { provider in
            ReplyModelProvider(
                id: provider.id,
                label: provider.label,
                models: provider.models.filter {
                    !blocked.contains($0.id) && !modelFallbackChain.contains($0.id)
                }
            )
        }.filter { !$0.models.isEmpty }
    }

    func fallbackStatusText() -> String {
        if let note = modelFallbackNote { return note }
        if modelFallbackBusy { return "폴백 모델을 저장하는 중이에요…" }
        if modelFallbackChain.isEmpty {
            return "폴백 없음 — 앞 모델이 한도에 걸리면 답변을 건너뜁니다."
        }
        let count = "\(modelFallbackChain.count)/\(modelFallbackMax)개"
        let head = modelFallbackSource == "override"
            ? "사용자 지정 \(count)"
            : "내장 기본값 \(count) · 바꾸려면 아래에서 추가하세요"
        // 순서와 이름은 바로 위 행 목록이 보여 준다. 상태 줄은 머리말과
        // 손봐야 할 문제만 말한다.
        if modelFallbackUnknown.isEmpty {
            return head
        }
        // 목록에서 사라진 모델은 그대로 두면 조용히 건너뛰어진다. 고칠 수 있게
        // 몇 개가 목록에 없는지 알린다.
        return "\(head) · 목록에 없는 모델 \(modelFallbackUnknown.count)개"
    }

    /// 폴백 행을 다시 그린다. 순서 이동은 위/아래 버튼만 쓰고, 값은 코어에 저장한다.
    func rebuildFallbackRows() {
        guard let rows = modelFallbackRows else { return }
        for view in rows.arrangedSubviews {
            rows.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
        if modelFallbackChain.isEmpty {
            let empty = Chrome.hint(
                "폴백 모델이 없습니다. '폴백 모델 추가…'에서 골라 주세요.",
                size: 12
            )
            rows.addArrangedSubview(empty)
        }
        for (index, model) in modelFallbackChain.enumerated() {
            let order = Chrome.label("\(index + 1).", size: 12, color: .secondaryLabelColor, lines: 1)
            order.setContentHuggingPriority(.required, for: .horizontal)
            let name = Chrome.label(Self.friendlyModelName(model), size: 12, lines: 1)
            name.toolTip = "모델 ID: \(model)"
            // 저장한 뒤 카탈로그가 바뀌면 그 칸은 호출 전에 건너뛴다. 조용히
            // 넘어가지 않게 줄에서 바로 표시한다(2026-09-15).
            var marker: NSTextField?
            if modelFallbackUnknown.contains(model) {
                marker = Chrome.label("목록에 없음", size: 11, color: .systemOrange, lines: 1)
                marker?.toolTip = "지금 모델 목록에 없는 모델입니다. 이 칸은 호출되지 않습니다. 삭제하고 지금 있는 모델로 바꿔 주세요."
            } else if !modelFallbackPrimary.isEmpty, model == modelFallbackPrimary {
                marker = Chrome.label("지금 답변 모델", size: 11, color: .systemOrange, lines: 1)
                marker?.toolTip = "지금 답변에 쓰는 모델입니다. 답변 모델을 바꾸면 이 칸이 동작합니다."
            }
            if let marker {
                marker.setContentHuggingPriority(.required, for: .horizontal)
            }
            let up = Chrome.roundedButton("▲", target: self, action: #selector(modelFallbackMoveUp(_:)))
            up.tag = index
            up.toolTip = "이 폴백을 한 칸 위로 올립니다."
            up.isEnabled = index > 0 && !modelFallbackBusy
            up.setContentHuggingPriority(.required, for: .horizontal)
            let down = Chrome.roundedButton("▼", target: self, action: #selector(modelFallbackMoveDown(_:)))
            down.tag = index
            down.toolTip = "이 폴백을 한 칸 아래로 내립니다."
            down.isEnabled = index < modelFallbackChain.count - 1 && !modelFallbackBusy
            down.setContentHuggingPriority(.required, for: .horizontal)
            let remove = Chrome.roundedButton("삭제", target: self, action: #selector(modelFallbackRemove(_:)))
            remove.tag = index
            remove.toolTip = "이 폴백을 목록에서 뺍니다."
            remove.isEnabled = !modelFallbackBusy
            remove.setContentHuggingPriority(.required, for: .horizontal)
            let row = Chrome.hstack(
                [order, name] + (marker.map { [$0] } ?? []) + [Chrome.spacer(), up, down, remove],
                spacing: 6
            )
            rows.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: rows.widthAnchor).isActive = true
        }
    }

    /// '폴백 모델 추가…' 메뉴를 만든다. 이미 고른 모델과 현재 답변/이미지 모델은 뺀다.
    func buildFallbackAddMenu() -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false
        // 풀다운 버튼은 0번 항목을 제목으로 쓴다. 메뉴를 새로 만들 때도 제목을
        // 다시 넣어야 버튼 이름이 비지 않는다.
        let title = NSMenuItem(title: "폴백 모델 추가…", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        let providers = fallbackCandidateProviders()
        if catalogLoading && providers.isEmpty {
            let loading = NSMenuItem(title: "모델 목록 불러오는 중…", action: nil, keyEquivalent: "")
            loading.isEnabled = false
            menu.addItem(loading)
        } else if providers.isEmpty {
            let empty = NSMenuItem(
                title: modelFallbackChain.count >= modelFallbackMax
                    ? "폴백은 최대 \(modelFallbackMax)개까지입니다"
                    : "추가할 모델이 없습니다 — '모델 목록 새로고침'을 눌러 주세요",
                action: nil,
                keyEquivalent: ""
            )
            empty.isEnabled = false
            menu.addItem(empty)
        } else {
            menu.addItem(.separator())
            for provider in providers {
                // 제공자당 모델이 수십 개라 평평한 목록은 고를 수 없다. 제공자별
                // 하위 메뉴로 묶고, 지금 몇 개를 골랐는지 아래 줄에 남긴다.
                let submenu = NSMenu()
                submenu.autoenablesItems = false
                for item in provider.models {
                    let row = NSMenuItem(
                        title: item.label,
                        action: #selector(modelFallbackAdd(_:)),
                        keyEquivalent: ""
                    )
                    row.target = self
                    row.representedObject = item.id
                    row.toolTip = item.id
                    row.isEnabled = !modelFallbackBusy
                    submenu.addItem(row)
                }
                let parent = NSMenuItem(
                    title: "\(provider.label) · \(provider.models.count)개",
                    action: nil,
                    keyEquivalent: ""
                )
                parent.submenu = submenu
                menu.addItem(parent)
            }
            let footer = NSMenuItem(
                title: "지금 \(modelFallbackChain.count)/\(modelFallbackMax)개 · 이미 고른 모델은 목록에서 빠집니다",
                action: nil,
                keyEquivalent: ""
            )
            footer.isEnabled = false
            menu.addItem(.separator())
            menu.addItem(footer)
        }
        return menu
    }

    /// 추가 버튼은 상한에 걸렸거나 고를 모델이 없을 때 눌리지 않게 한다.
    func updateFallbackAddButton() {
        guard let button = modelFallbackAddButton else { return }
        let full = modelFallbackChain.count >= modelFallbackMax
        let providers = fallbackCandidateProviders()
        button.isEnabled = !full && !modelFallbackBusy && !providers.isEmpty
        button.toolTip = full
            ? "폴백은 최대 \(modelFallbackMax)개까지 저장할 수 있습니다."
            : "앞 모델이 사용량 한도에 걸렸을 때 이어서 시도할 모델을 고릅니다."
        // 후보 목록을 버튼에 실제로 붙인다. 예전에는 메뉴를 만드는 함수만 있고
        // 붙이는 곳이 없어, 추가 버튼을 눌러도 빈 메뉴가 떴다(2026-09-15).
        let signature = fallbackMenuSignature(providers: providers)
        if signature != modelFallbackMenuSignature {
            modelFallbackMenuSignature = signature
            button.menu = buildFallbackAddMenu()
        }
    }

    /// 추가 메뉴를 다시 그릴 필요가 있는지 판단하는 서명.
    func fallbackMenuSignature(providers: [ReplyModelProvider]) -> String {
        let listed = providers
            .map { provider in
                provider.id + ":" + provider.models.map { $0.id }.joined(separator: ",")
            }
            .joined(separator: "|")
        return [
            listed,
            modelFallbackChain.joined(separator: ","),
            catalogLoading ? "loading" : "ready",
            modelFallbackBusy ? "busy" : "idle",
        ].joined(separator: "#")
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
        defer { shrinkModelSettingsWindow() }
        let providers = modelProvidersForWindow()
        // 폴백 사슬은 코어가 계산한 값을 먼저 반영하고, 그 위에 방금 저장한
        // 확인값을 유지한다(늦게 도착한 조회가 화면을 되돌리지 않게).
        applyFallbackState(lastModel?.reply_model_fallbacks)

        let reply = currentReplyModel ?? lastModel?.reply_model
        let replyId = reply?.id ?? ""
        let replyLabel = (reply?.label ?? replyId).trimmingCharacters(in: .whitespacesAndNewlines)
        // 역할 설명은 제목 아래에 고정하고, 현재 값은 선택란과 상태 줄에만 둔다.
        if let hw = lastModel?.ondevice_hardware, let hwLabel = modelHardwareHint {
            hwLabel.stringValue = "온디바이스 감지: \(hw.hardware.chip) (\(Int(hw.hardware.memory_gb))GB RAM) · 추천 엔진: \(hw.recommendation.primary_engine.uppercased()) (Qwen3.8 최적화)"
            hwLabel.toolTip = hw.recommendation.reason
        }
        modelReplySummary?.stringValue = "메시지에 답할 때 씁니다."
        modelReplyStatus?.stringValue = modelReplyState.message ?? (replyLabel.isEmpty
            ? (catalogLoading ? "모델 목록 불러오는 중…" : "모델을 선택하세요.")
            : "적용됨")
        modelReplyPopup?.toolTip = replyId.isEmpty ? nil : "현재 모델 ID: \(replyId)"
        if let popup = modelReplyPopup {
            populateModelPopup(popup, providers: providers, currentId: replyId, enabled: !modelChangeInFlight)
        }

        let image = currentImageReplyModel ?? lastModel?.image_reply_model
        let imageEnabled = image?.enabled ?? true
        let imageId = image?.id ?? ""
        let imageLabel = (image?.label ?? imageId).trimmingCharacters(in: .whitespacesAndNewlines)
        modelImageSummary?.stringValue = "사진이 있으면 이 모델이 답합니다."
        // 코어가 알려 준 명시적 상태를 먼저 쓰고, 없을 때만 예전 추정을 쓴다.
        let autoSelected = imageEnabled
            && (image?.auto_selected
                ?? (imageId.lowercased().contains("auto") || imageLabel.lowercased() == "auto"))
        if let note = modelImageState.message {
            modelImageStatus?.stringValue = note
        } else if !imageEnabled {
            modelImageStatus?.stringValue = "답변 모델이 사진도 함께 봐요 — 이미지 모델을 따로 고르지 않아도 됩니다."
        } else if autoSelected {
            // '고른 모델 없음'과 '자동 선택'을 같은 문구로 보여 주지 않는다.
            modelImageStatus?.stringValue = "자동 선택 · 이미지 메시지에 사용할 모델을 자동으로 선택합니다."
        } else if imageLabel.isEmpty {
            modelImageStatus?.stringValue = "이미지 모델 선택… — 이미지 메시지에 답하려면 모델을 선택하세요."
        } else {
            modelImageStatus?.stringValue = "적용됨"
        }
        modelImagePopup?.toolTip = imageId.isEmpty ? nil : "현재 모델 ID: \(imageId)"
        if let popup = modelImagePopup {
            populateModelPopup(popup, providers: providers, currentId: imageId, enabled: imageEnabled && !modelChangeInFlight)
        }

        modelFallbackStatus?.stringValue = fallbackStatusText()
        // 비우기는 지울 폴백이 있을 때만, 복원은 저장한 목록이 있을 때만.
        modelFallbackResetButton?.isEnabled = !modelFallbackChain.isEmpty && !modelFallbackBusy
        modelFallbackRestoreButton?.isEnabled = modelFallbackSource == "override" && !modelFallbackBusy
        rebuildFallbackRows()
        updateFallbackAddButton()
    }

    func refreshModelSettingsWindowIfOpen() {
        if let window = modelWindow, window.isVisible {
            updateModelSettingsWindow()
        }
    }

    /// 내용보다 창이 훨씬 길면 아래쪽에 아무것도 없는 빈 자리가 남는다.
    /// 내용 높이에 맞춰 줄이되, 스크롤이 필요할 만큼 길면 손대지 않는다.
    func shrinkModelSettingsWindow(attempt: Int = 0) {
        guard let window = modelWindow,
              let stack = modelSettingsStack,
              modelSettingsScroll != nil,
              let content = window.contentView,
              !modelWindowUserResized,
              attempt < 4 else { return }
        stack.layoutSubtreeIfNeeded()
        let contentHeight = stack.fittingSize.height
        guard contentHeight > 1 else { return }
        // 창이 담아야 할 높이는 내용 + 스크롤 여백(위 16 + 아래 16)이다.
        // 화면보다 커질 수는 없으므로 남는 높이까지만 키운다.
        let desired = min(contentHeight + 32, Self.modelWindowHeightLimit)
        let current = content.bounds.height
        // 줄일 때만 보던 것을 늘릴 때도 본다. 폴백을 추가해 내용이 길어지면
        // 창은 작은 채로 남아 아래 조작 단추가 스크롤 뒤로 숨었다
        // (2026-09-16).
        guard abs(current - desired) > 12 else { return }
        // 배치가 한 번에 수렴하지 않으므로, 줄어든 뒤 다시 재서 맞춘다.
        guard abs(current - lastModelContentHeight) > 1 else { return }
        lastModelContentHeight = current
        var frame = window.frame
        let delta = current - desired
        frame.size.height -= delta
        // 위쪽 모서리를 고정한다. 아래에서 자라면 창이 화면 밖으로 밀린다.
        frame.origin.y += delta
        window.setFrame(frame, display: true, animate: false)
        DispatchQueue.main.async { [weak self] in
            self?.shrinkModelSettingsWindow(attempt: attempt + 1)
        }
    }

    /// 감사 모드에서 쓰는 동기 축소. 위 재귀는 다음 런루프를 기다리므로
    /// 감사가 창을 재기 전에는 끝나지 않아, 빈 상태에서 창 아래에 143pt가
    /// 그대로 남았다 (2026-09-16).
    func shrinkModelSettingsWindowNow() {
        for _ in 0..<4 {
            guard let window = modelWindow,
                  let stack = modelSettingsStack,
                  modelSettingsScroll != nil,
                  let content = window.contentView,
                  !modelWindowUserResized else { return }
            content.layoutSubtreeIfNeeded()
            stack.layoutSubtreeIfNeeded()
            let contentHeight = stack.fittingSize.height
            guard contentHeight > 1 else { return }
            let desired = min(contentHeight + 32, Self.modelWindowHeightLimit)
            let current = content.bounds.height
            guard abs(current - desired) > 12 else { return }
            var frame = window.frame
            let delta = current - desired
            frame.size.height -= delta
            frame.origin.y += delta
            window.setFrame(frame, display: false, animate: false)
            content.layoutSubtreeIfNeeded()
        }
    }

    func ensureModelSettingsWindow() {
        if modelWindow != nil {
            return
        }
        let window = Chrome.operatorWindow(
            title: "모델 설정",
            size: NSSize(width: 660, height: 640),
            autosave: "AutoReplyModelSettings2"
        )
        let content = NSView()
        window.contentView = content

        // 창 머리말 한 줄만 남긴다. 예전에는 이 문장이 창 머리말·답변 모델
        // 줄·이미지 모델 줄·폴백 상태·되돌리기 안내까지 다섯 번 나왔다. 같은
        // 말을 다섯 번 읽을 이유가 없어 여기 한 번만 남긴다 (2026-09-16,
        // 6 Pro 지적).
        let hint = Chrome.hint("고른 모델은 다음 턴부터 적용됩니다.", size: 12)
        let hwHint = Chrome.hint("온디바이스 하드웨어 사양을 감지하는 중…", size: 11)
        modelHardwareHint = hwHint

        let replyTitle = Chrome.label("답변 모델", size: 13, weight: .semibold, lines: 1)
        let replySummary = Chrome.hint("현재 모델을 불러오는 중…", size: 12)
        modelReplySummary = replySummary
        let replyPopup = NSPopUpButton(frame: .zero, pullsDown: false)
        replyPopup.translatesAutoresizingMaskIntoConstraints = false
        replyPopup.target = self
        replyPopup.action = #selector(modelReplyPopupChanged(_:))
        modelReplyPopup = replyPopup

        let imageTitle = Chrome.label("이미지 답변 모델", size: 13, weight: .semibold, lines: 1)
        let imageSummary = Chrome.hint("현재 이미지 모델을 불러오는 중…", size: 12)
        modelImageSummary = imageSummary
        let imagePopup = NSPopUpButton(frame: .zero, pullsDown: false)
        imagePopup.translatesAutoresizingMaskIntoConstraints = false
        imagePopup.target = self
        imagePopup.action = #selector(modelImagePopupChanged(_:))
        modelImagePopup = imagePopup

        let status = Chrome.statusLabel()
        modelStatusField = status
        // 모델별 상태 줄: 바꾼 행에서 바로 결과를 볼 수 있게 한다.
        let replyStatus = Chrome.statusLabel()
        modelReplyStatus = replyStatus
        let imageStatus = Chrome.statusLabel()
        modelImageStatus = imageStatus

        // 폴백 사슬: 앞 모델이 사용량 한도에 걸렸을 때 이어서 시도할 순서.
        let fallbackTitle = Chrome.label("폴백 모델", size: 13, weight: .semibold, lines: 1)
        let fallbackSummary = Chrome.hint(
            "앞 모델이 막히면 위에서부터 다시 시도합니다.",
            size: 12
        )
        let fallbackRows = NSStackView()
        fallbackRows.orientation = .vertical
        fallbackRows.alignment = .leading
        fallbackRows.distribution = .fill
        fallbackRows.spacing = 6
        fallbackRows.translatesAutoresizingMaskIntoConstraints = false
        modelFallbackRows = fallbackRows
        let fallbackStatus = Chrome.statusLabel(lines: 3)
        modelFallbackStatus = fallbackStatus
        let fallbackAdd = NSPopUpButton(frame: .zero, pullsDown: true)
        fallbackAdd.translatesAutoresizingMaskIntoConstraints = false
        fallbackAdd.addItem(withTitle: "폴백 모델 추가…")
        modelFallbackAddButton = fallbackAdd
        let fallbackClear = Chrome.roundedButton(
            "폴백 모두 비우기",
            target: self,
            action: #selector(modelFallbackClearClicked(_:))
        )
        fallbackClear.toolTip = "폴백을 하나도 쓰지 않습니다. 앞 모델이 한도에 걸리면 그 턴은 답변을 건너뜁니다."
        fallbackClear.isEnabled = false
        modelFallbackResetButton = fallbackClear
        let fallbackRestore = Chrome.roundedButton(
            "내장 기본값으로 복원",
            target: self,
            action: #selector(modelFallbackResetClicked(_:))
        )
        fallbackRestore.toolTip = "저장한 폴백 목록을 지우고 내장 기본값을 다시 씁니다."
        fallbackRestore.isEnabled = false
        modelFallbackRestoreButton = fallbackRestore
        let fallbackActions = Chrome.hstack(
            [fallbackAdd, fallbackClear, fallbackRestore, Chrome.spacer()],
            spacing: 8
        )

        let registerButton = Chrome.roundedButton("모델 제공자 추가…", target: self, action: #selector(modelProviderRegisterClicked(_:)))
        let reloadButton = Chrome.roundedButton("모델 목록 새로고침", target: self, action: #selector(reloadModelsClicked))
        let revertButton = Chrome.roundedButton("이전 모델로 되돌리기", target: self, action: #selector(modelRevertClicked(_:)))
        revertButton.isEnabled = false
        modelRevertButton = revertButton
        // 단추 셋이 왼쪽에 몰려 오른쪽 절반이 빈 자리로 남던 것을, 같은
        // 폭으로 나눠 창 끝까지 채운다 (2026-09-16).
        let actions = Chrome.actionRow([registerButton, reloadButton, revertButton])

        // 섹션 1: 주요 모델 설정 카드 (카드 레이아웃으로 불필요한 공백 제거)
        replyTitle.setContentHuggingPriority(.required, for: .horizontal)
        imageTitle.setContentHuggingPriority(.required, for: .horizontal)
        replyTitle.widthAnchor.constraint(equalToConstant: 120).isActive = true
        imageTitle.widthAnchor.constraint(equalToConstant: 120).isActive = true
        let replyRow = Chrome.hstack([replyTitle, replyPopup], spacing: 10)
        let imageRow = Chrome.hstack([imageTitle, imagePopup], spacing: 10)
        let primaryContent = Chrome.vstack(
            [replyRow, replySummary, replyStatus, imageRow, imageSummary, imageStatus],
            spacing: 8
        )
        primaryContent.setCustomSpacing(12, after: replyStatus)
        let primaryCard = Chrome.card(primaryContent, padding: 14)

        // 섹션 2: 폴백 모델 사슬 카드
        let fallbackContent = Chrome.vstack(
            [fallbackTitle, fallbackSummary, fallbackRows, fallbackStatus, fallbackActions],
            spacing: 8
        )
        let fallbackCard = Chrome.card(fallbackContent, padding: 14)

        let stack = Chrome.vstack(
            [hint, hwHint, primaryCard, fallbackCard, status, actions],
            spacing: 12
        )
        // 폴백을 최대치까지 넣으면 창보다 길어지므로 스크롤로 감싼다.
        modelSettingsStack = stack
        modelSettingsScroll = Chrome.scrollable(stack, in: content)
        window.delegate = self
        NSLayoutConstraint.activate([
            hint.widthAnchor.constraint(equalTo: stack.widthAnchor),
            hwHint.widthAnchor.constraint(equalTo: stack.widthAnchor),
            primaryCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            primaryContent.widthAnchor.constraint(equalTo: primaryCard.widthAnchor, constant: -28),
            replyRow.widthAnchor.constraint(equalTo: primaryContent.widthAnchor),
            imageRow.widthAnchor.constraint(equalTo: primaryContent.widthAnchor),
            fallbackCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            fallbackContent.widthAnchor.constraint(equalTo: fallbackCard.widthAnchor, constant: -28),
            fallbackRows.widthAnchor.constraint(equalTo: fallbackContent.widthAnchor),
            fallbackActions.widthAnchor.constraint(equalTo: fallbackContent.widthAnchor),
            status.widthAnchor.constraint(equalTo: stack.widthAnchor),
            actions.widthAnchor.constraint(equalTo: stack.widthAnchor),
        ])
        // 키보드 순서: 답변 모델 → 이미지 모델 → 폴백 추가 → 폴백 모두 비우기 →
        // 내장 기본값으로 복원 → 제공자 추가 → 목록 새로고침 → 되돌리기.
        window.initialFirstResponder = replyPopup
        replyPopup.nextKeyView = imagePopup
        imagePopup.nextKeyView = fallbackAdd
        fallbackAdd.nextKeyView = fallbackClear
        fallbackClear.nextKeyView = fallbackRestore
        fallbackRestore.nextKeyView = registerButton
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

    /// 직전에 적용했던 모델로 되돌린다. 답변·이미지 어느 쪽을 바꿨든 그 항목을
    /// 되돌리고, 성공한 항목의 이력만 지워 재시도를 막지 않는다.
    @objc func modelRevertClicked(_ sender: Any?) {
        guard let record = lastModelChange, !modelChangeInFlight else { return }
        let target = record.target
        let action = target == .reply ? "model-set" : "image-model-set"
        // 되돌리기도 새 변경이다. 이전 변경의 재확인이 되돌리기 결과를 덮지 않게 토큰을 올린다.
        modelChangeToken += 1
        // 다른 행에 남은 예약 확인도 무효가 되므로 그 행을 끝낸다.
        cancelStaleVerification(target == .reply ? .image : .reply)
        modelChangeInFlight = true
        setRowState(target, ModelRowState(phase: .verifying, message: "되돌리는 중…", targetId: record.selection.id))
        modelStatusField?.stringValue = "이전 모델로 되돌리는 중이에요…"
        modelRevertButton?.isEnabled = false
        refreshModelSettingsWindowIfOpen()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let data = self.runPython(["--action", action, "--model", record.selection.id], timeout: 200)
            let restored = self.storedModelId(data) == record.selection.id
            DispatchQueue.main.async {
                self.modelChangeInFlight = false
                if restored {
                    self.setTargetSelection(target, record.selection)
                    // 복구가 확인된 뒤에만 이력을 지운다.
                    self.lastModelChange = nil
                    self.setRowState(target, ModelRowState(phase: .applied, message: nil, targetId: nil))
                    self.modelStatusField?.stringValue = "이전 모델로 되돌렸어요."
                    self.modelRevertButton?.isEnabled = false
                    self.modelRevertButton?.title = "되돌리기"
                } else {
                    self.setRowState(target, ModelRowState(phase: .failed, message: "되돌리지 못했습니다. 다시 시도해 주세요.", targetId: nil))
                    self.modelStatusField?.stringValue = "되돌리지 못했어요. 다시 시도해 주세요."
                    self.modelRevertButton?.isEnabled = true
                }
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
        // 목록 조회도 시작 시점의 세대를 기억해, 완료 시 선택값 덮어쓰기를 막는다.
        let fetchToken = modelChangeToken
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--action", "models"], timeout: 45)
            DispatchQueue.main.async {
                guard let self else { return }
                self.catalogLoading = false
                if let data,
                   let report = try? JSONDecoder().decode(ModelsReport.self, from: data),
                   report.ok == true {
                    if let providers = report.providers, !providers.isEmpty {
                        self.catalogProviders = providers
                    }
                    // 새로 고침이 성공하면 지난번 실패 안내를 지운다.
                    if (self.modelStatusField?.stringValue ?? "").hasPrefix("모델 목록") {
                        self.modelStatusField?.stringValue = ""
                    }
                    if let id = report.model, !id.isEmpty, !self.modelChangeInFlight,
                       fetchToken == self.modelChangeToken {
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
                    self.modelStatusField?.stringValue = "모델 목록을 새로 불러오지 못했습니다. 이전 목록을 표시합니다."
                } else {
                    // 보여 줄 목록 자체가 없으면 다음 행동을 알려 준다.
                    self.modelStatusField?.stringValue = "모델 목록을 불러오지 못했습니다. '모델 목록 새로고침'을 눌러 주세요."
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
            } else if action == "fallback-models-set" {
                fallback = "폴백 모델 목록을 저장하지 못했습니다."
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
            } else if action == "fallback-models-set" {
                title = "폴백 모델"
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

    /// 창을 화면에 띄우지 않고 배치만 해서 기하 수치와 그림을 남긴다. 눈으로
    /// 보지 않고도 불필요한 빈 여백과 넘침을 찾기 위한 진단 모드다.
    func runLayoutAudit(_ outputDir: String) -> String {
        let directory = URL(fileURLWithPath: outputDir, isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        ensureModelSettingsWindow()
        ensureJobsWindow()
        ensureLogWindow()
        ensureRoomsWindow()
        ensureVectorWindow()
        // 메뉴를 눌렀을 때 가장 먼저 보이는 화면도 같은 기준으로 잰다.
        // 메뉴를 눌렀을 때 보이는 패널은 접힌 상태와 펼친 상태의 높이가
        // 다르다. 둘 다 재야 어느 쪽에 빈 띠가 생기는지 알 수 있다.
        layoutAuditPanels = []
        for expanded in [false, true] {
            // 상태를 읽지 못해도 패널은 사용자가 가장 먼저 보는 화면이다.
            // 감사는 그 경우까지 재야 한다 (2026-09-16).
            let model = loadModel() ?? Self.unavailableModel()
            let rooms = Self.inspectableRooms(in: model)
            let extra = MenuPanelView.roomGridExtra(count: rooms.count, expanded: expanded)
            let panel = MenuPanelView(
                model: model,
                frame: NSRect(
                    x: 0,
                    y: 0,
                    width: MenuPanelView.panelWidth,
                    height: MenuPanelView.panelBaseHeight + extra
                )
            )
            panel.selectedRoomId = inspectedRoomId
            panel.roomsExpanded = expanded
            let host = NSWindow(
                contentRect: panel.frame,
                styleMask: [.borderless],
                backing: .buffered,
                defer: false
            )
            host.contentView = panel
            layoutAuditPanels.append((expanded ? "menu-panel-rooms" : "menu-panel", host, panel))
        }
        let windows: [(String, NSWindow?)] = [
            ("model", modelWindow),
            ("jobs", jobsWindow),
            ("log", logWindow),
            ("rooms", roomsWindow),
            ("vector", vectorWindow),
        ]
        var rows: [[String: Any]] = []
        var images: [String] = []
        var modes: [[String: Any]] = []
        // 실제 데이터를 채운 뒤에 재야 빈 목록으로 인한 거짓 여백을 보지 않는다.
        if let model = loadModel() {
            lastModel = model
            updateModelSettingsWindow()
            updateRoomsWindow(model)
            // 창을 열 때 실제로 타는 경로를 그대로 쓴다. 예전에는
            // applyLogReceipts만 불러서, 기록 창의 머리말이 초기 문구인
            // "상태를 읽는 중"에 머문 채로 찍혔다. 그 그림을 근거로 창이
            // 고장 났다고 판단하면 멀쩡한 곳을 고치게 된다 (2026-09-16).
            updateLogWindow(model)
        }
        if let report = loadJobs(status: jobsStatus) {
            jobsReadFailed = false
            applyJobs(report)
        } else {
            // 감사에서도 실패를 빈 목록으로 보지 않는다. 그래야 실제 운영
            // 화면과 같은 상태를 재게 된다 (2026-09-16).
            jobsReadFailed = true
            applyJobs(JobReport(
                ok: false,
                action: "jobs",
                privacy: "content_redacted",
                status: jobsStatus,
                title: "목록",
                count: 0,
                truncated: false,
                jobs: []
            ))
        }
        if let report = loadVectorReport([
            "--action", "vector-list",
            "--vector-offset", "0",
            "--vector-source", vectorSourceKind,
        ]) {
            applyVectorReport(report)
        }
        // 신경망 보기는 평소 백그라운드에서 읽는다. 감사는 그 결과를 기다릴
        // 수 없어 뉴런이 하나도 없는 260pt 빈 띠를 재게 된다. 여기서는 한 번
        // 동기로 읽어 실제로 그려지는 그림을 잰다 (2026-09-16).
        if vectorSourceKind == "knowledge_graph",
           let data = runPython(["--action", "knowledge-graph"], timeout: 25),
           let report = try? JSONDecoder().decode(KnowledgeGraphReport.self, from: data),
           report.ok {
            vectorGraphView?.applySnapshot(nodes: report.nodes, edges: report.edges)
            vectorGraphView?.layoutSubtreeIfNeeded()
            vectorGraphView?.displayIfNeeded()
        }
        // 읽을 자료가 없으면 신경망 보기도 감춘다. 빈 캔버스 260pt를 그대로
        // 두면 창 아래가 통째로 비어 보인다 (2026-09-16).
        hideEmptyKnowledgeGraph()
        // 창 크기 조정은 원래 다음 런루프에서 끝난다. 감사는 그 전에 재므로
        // 여기서 한 번에 맞춘다.
        shrinkModelSettingsWindowNow()
        fitVectorWindow()
        // 작업 목록 창은 행 수에 맞춰 스스로 줄어든다. 감사는 창을 만들자마자
        // 재기 때문에 그 조정이 아직 돌지 않아, 오토세이브된 488pt 프레임에
        // 행 하나만 그려진 그림을 재게 된다. 실제 운영 화면과 같은 상태로
        // 맞춘 뒤에 잰다 (2026-09-17).
        jobsWindowUserResized = false
        fitJobsWindow()
        for entry in windows {
            guard let window = entry.1, let content = window.contentView else { continue }
            // 창을 옮기고 크기를 바꾸면 AppKit이 그 프레임을 autosave 이름에
            // 저장한다. 감사가 운영자의 창 크기를 덮어쓰지 않도록 연결을
            // 끊는다. 저장된 값 자체는 그대로 남는다 (2026-09-16).
            window.setFrameAutosaveName("")
            // 창을 만들 때 크기에서만 재면 "줄였을 때 무엇이 사라지는가"를
            // 놓친다. 운영자가 모서리를 끌어 줄일 수 있는 가장 작은 크기를
            // 따로 재서, 그 크기에서 내용이 잘리거나 넘치는지 본다. 기록 창의
            // 마지막 열이 사라지던 결함이 이 검사로 드러났다 (2026-09-16).
            let originalSize = content.bounds.size
            // window.minSize is the frame size, not the content size, so
            // passing it to setContentSize under-sizes the content by the
            // title bar. The audit then measured a window the operator can
            // never actually reach, and real clipping could hide behind the
            // difference. Convert through the frame rect (2026-09-17, 6 Pro
            // 지적).
            let minimumFrame = NSRect(origin: window.frame.origin, size: window.minSize)
            window.setFrame(minimumFrame, display: false)
            window.layoutIfNeeded()
            content.layoutSubtreeIfNeeded()
            LayoutAudit.settle()
            LayoutAudit.collect(
                from: content,
                window: entry.0 + "-min",
                path: entry.0 + "-min",
                windowSize: content.bounds.size,
                into: &rows
            )
            // 원래 크기로 되돌린다. 되돌리지 않으면 아래 캡처가 줄어든
            // 창을 찍어, 운영자가 실제로 보는 그림과 달라진다 (2026-09-16).
            window.setContentSize(originalSize)
            window.layoutIfNeeded()
            content.layoutSubtreeIfNeeded()
            // 창을 화면 밖에 세워 실제로 배치시킨다. 런루프가 돌지 않으면
            // AppKit이 배치를 미루고, 그러면 프레임을 잘못 읽는다.
            window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
            window.orderFrontRegardless()
            window.layoutIfNeeded()
            content.layoutSubtreeIfNeeded()
            LayoutAudit.settle()
            LayoutAudit.collect(
                from: content,
                window: entry.0,
                path: entry.0,
                windowSize: content.bounds.size,
                into: &rows
            )
            // 라이트를 분명히 밝히고 찍는다. 밝히지 않으면 이 Mac이 다크
            // 모드일 때 "라이트" 캡처까지 어둡게 나와 두 장이 같은 파일이
            // 되고, 다크 대비 검증이 통째로 무의미해진다 (2026-09-16).
            if let aqua = NSAppearance(named: .aqua),
               LayoutAudit.writeCapture(content, appearance: aqua, to: directory.appendingPathComponent(entry.0 + ".png")) {
                images.append(entry.0 + ".png")
            }
            window.orderOut(nil)
        }
        for (name, window, view) in layoutAuditPanels {
            window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
            window.orderFrontRegardless()
            view.layoutSubtreeIfNeeded()
            LayoutAudit.collect(
                from: view,
                window: name,
                path: name,
                windowSize: view.bounds.size,
                into: &rows
            )
            if let aqua = NSAppearance(named: .aqua),
               LayoutAudit.writeCapture(view, appearance: aqua, to: directory.appendingPathComponent(name + ".png")) {
                images.append(name + ".png")
            }
            window.orderOut(nil)
        }
        // 드릴다운 확대를 한 장 더 찍는다. 뉴런을 눌렀을 때 이웃만 남고
        // 카메라가 다가가는지, 이름표가 잘리지 않는지는 이 그림이 없으면
        // 눈으로 확인할 방법이 없다 (2026-09-17).
        if let graph = vectorGraphView, let window = vectorWindow,
           let content = window.contentView, graph.nodes.count > 1 {
            // 가장 많이 이어진 뉴런을 고른다. 이웃이 하나뿐인 뉴런을 고르면
            // 확대 효과가 거의 보이지 않아 감사가 통과해도 의미가 없다.
            var degree: [String: Int] = [:]
            for edge in graph.edges {
                degree[edge.source, default: 0] += 1
                degree[edge.target, default: 0] += 1
            }
            let hub = graph.nodes.max { left, right in
                (degree[left.id] ?? 0) < (degree[right.id] ?? 0)
            }
            if let hub {
                graph.selectedNodeId = hub.id
                graph.beginFocus(on: hub.id)
                // 확대 애니메이션은 타이머로 돈다. 감사는 화면을 띄우지
                // 않으므로 여기서 끝 상태로 못 박고 찍는다.
                graph.finishFocusForAudit()
                window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
                window.orderFrontRegardless()
                content.layoutSubtreeIfNeeded()
                LayoutAudit.collect(
                    from: content,
                    window: "vector-focus",
                    path: "vector-focus",
                    windowSize: content.bounds.size,
                    into: &rows
                )
                if let aqua = NSAppearance(named: .aqua),
                   LayoutAudit.writeCapture(
                       content,
                       appearance: aqua,
                       to: directory.appendingPathComponent("vector-focus.png")
                   ) {
                    images.append("vector-focus.png")
                }
                window.orderOut(nil)
            }
        }
        // 다크 모드에서도 같은 그림이 나오는지 잰다. 캡처는 화면에 실제로
        // 그리는 색을 담으므로, 라이트에서만 통과하면 어두운 배경에서
        // 글자가 사라지는 결함을 놓친다 (2026-09-16).
        if let appearance = NSAppearance(named: .darkAqua) {
            modes.append(LayoutAudit.mode(name: "aqua", appearance: NSAppearance(named: .aqua)))
            modes.append(LayoutAudit.mode(name: "darkAqua", appearance: appearance))
            for entry in windows + layoutAuditPanels.map({ ($0.0, Optional($0.1)) }) {
                guard let window = entry.1, let content = window.contentView else { continue }
                let previous = content.appearance
                content.appearance = appearance
                window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
                window.orderFrontRegardless()
                window.layoutIfNeeded()
                content.layoutSubtreeIfNeeded()
                LayoutAudit.collect(
                    from: content,
                    window: entry.0 + "-dark",
                    path: entry.0 + "-dark",
                    windowSize: content.bounds.size,
                    into: &rows
                )
                if LayoutAudit.writeCapture(content, appearance: appearance, to: directory.appendingPathComponent(entry.0 + "-dark.png")) {
                    images.append(entry.0 + "-dark.png")
                }
                content.appearance = previous
                window.orderOut(nil)
            }
        }
        let payload: [String: Any] = ["windows": rows, "images": images, "modes": modes]
        if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: directory.appendingPathComponent("layout.json"))
        }
        return String(rows.count) + " views, " + String(images.count) + " images"
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

    /// 우측 상단 톱니바퀴 하나로 모든 제어와 설정을 연다.
    ///
    /// 예전에는 메뉴 아래에 "답변 기록 보기…", "AI 모델 설정…", "채팅방
    /// 관리…", "지식 그래프 (대화 기억)…" 네 줄이 따로 늘어서 있었다.
    /// 무엇을 먼저 눌러야 하는지 알 수 없었고, 같은 설정이 두 곳에 나뉘어
    /// 있기도 했다. 지금은 톱니바퀴 하나가 그 네 창을 모두 연다
    /// (2026-09-17).
    @objc func gearClicked(_ sender: NSButton) {
        _ = sender
        // 메뉴 안의 단추를 누르면 AppKit이 먼저 메뉴를 닫는다. 그 닫는
        // 동작이 끝나기 전에 새 메뉴를 열면 곧바로 닫혀 버리므로, 한 바퀴
        // 돌린 뒤에 연다 (2026-09-17).
        DispatchQueue.main.async { [weak self] in
            self?.presentGearMenu()
        }
    }

    /// 톱니바퀴를 누르면 열리는 목록. 창을 여는 유일한 입구다.
    func presentGearMenu() {
        statusItem.button?.highlight(false)
        let menu = NSMenu()
        menu.autoenablesItems = false
        let entries: [(String, Selector)] = [
            ("AI 모델 설정…", #selector(showModelSettingsWindow)),
            ("채팅방 관리…", #selector(showRoomsWindow)),
            ("답변 기록 보기…", #selector(showLogWindow)),
            ("지식 그래프…", #selector(showVectorWindow)),
        ]
        for (title, action) in entries {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.target = self
            item.isEnabled = true
            menu.addItem(item)
        }
        menu.addItem(.separator())
        let quit = NSMenuItem(
            title: "메뉴 종료",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        quit.isEnabled = true
        menu.addItem(quit)
        guard let button = statusItem.button, button.window != nil else { return }
        let origin = NSPoint(x: 0, y: button.bounds.height + 4)
        menu.popUp(positioning: nil, at: origin, in: button)
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

    /// 사용자가 크기를 직접 바꾼 창은 자동으로 줄이지 않는다. 자동 축소가
    /// 방금 한 조절을 되돌리면 창이 제멋대로 움직이는 것처럼 보인다.
    func windowDidEndLiveResize(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        if window === modelWindow {
            modelWindowUserResized = true
        } else if window === vectorWindow {
            vectorWindowUserResized = true
        } else if window === jobsWindow {
            jobsWindowUserResized = true
        } else if window === roomsWindow {
            roomsWindowUserResized = true
        }
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
        // 작업 목록은 원장과 큐를 함께 읽는다. 기본 8초는 호스트가 큐를 쓰는
        // 동안 자주 넘겨, 목록이 조용히 빈 채로 돌아왔다 (2026-09-16).
        guard let data = runPython(["--action", "jobs", "--jobs-status", status], timeout: 20) else { return nil }
        return try? JSONDecoder().decode(JobReport.self, from: data)
    }

    func refreshJobs(status: String) {
        jobsStatus = status
        jobsSummary?.stringValue = "목록을 읽는 중"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            // 읽지 못한 것과 일이 없는 것은 다르다. 예전에는 실패를 빈 목록으로
            // 바꿔 넣어서, 조회가 넘긴 순간에도 화면은 "0건"이라고 단언했다
            // (2026-09-16, 6 Pro 지적).
            let loaded = self.loadJobs(status: status)
            let report = loaded ?? JobReport(
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
                self.jobsReadFailed = (loaded == nil)
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
            summary.stringValue = report.ok
                ? "\(report.title) \(report.count)건\(extra)"
                : "목록을 읽지 못했습니다"
        }
        jobsTable?.reloadData()
        restoreJobsSelection(eventId: selected)
        updateJobsActions()
        fitJobsTableHeight()
        updateJobsEmptyState()
    }

    /// 표가 비면 회색 띠 대신 이유를 적는다. 자리를 미리 차지하지 않도록
    /// AutoHidingLabel을 쓰므로, 채워지면 스스로 사라진다 (2026-09-16).
    func updateJobsEmptyState() {
        guard let label = jobsEmptyLabel else { return }
        let empty = displayedJobs.isEmpty
        label.stringValue = ""
        jobsTableScroll?.isHidden = empty
        jobsEmptyState?.isHidden = !empty
        guard empty else { return }
        if jobsReadFailed {
            // 조회가 넘겼을 때 "0건"이라고 적으면 운영자는 할 일이 없다고
            // 믿고 창을 닫는다. 다시 눌러 볼 수 있게 사실대로 적는다
            // (2026-09-16).
            jobsEmptyState?.titleText = "목록을 읽지 못했습니다"
            jobsEmptyState?.detailText = "잠시 뒤 상태를 다시 눌러 주세요."
        } else {
            jobsEmptyState?.titleText = "이 목록에 지금 보여 줄 작업이 없습니다"
            jobsEmptyState?.detailText = ""
        }
        fitJobsWindow()
    }

    /// 목록이 비면 창을 머리말과 안내 한 줄 높이로 줄인다.
    ///
    /// 표가 비었을 때 표가 차지하던 200pt를 그대로 두면, 창 아래 3분의 2가
    /// 아무 내용 없는 판으로 남아 화면이 통째로 안내문이 된다. 할 일이 없을
    /// 때는 창도 그만큼 작아야 한다. 사용자가 직접 키운 창은 건드리지 않는다
    /// (2026-09-16, 6 Pro 지적).
    func fitJobsWindow() {
        guard !jobsFitting,
              !jobsWindowUserResized,
              let window = jobsWindow,
              let stack = jobsStack,
              let content = window.contentView else { return }
        jobsFitting = true
        defer { jobsFitting = false }
        fitJobsTableHeight()
        // 목록이 비면 창이 표 높이에 묶여 있던 하한을 풀어 준다.
        //
        // 이 창의 최소 높이 400pt는 표가 다섯 줄은 보이게 하려고 정한 값이다.
        // 그런데 목록이 비면 그 높이가 그대로 남아, 안내 한 줄이 263pt짜리
        // 빈 판으로 늘어났다. 보여 줄 것이 없을 때는 하한도 필요 없다. 다시
        // 목록이 차면 원래 하한으로 되돌린다 (2026-09-16, 6 Pro 지적).
        let wantsFloor = !displayedJobs.isEmpty
        for _ in 0..<3 {
            content.layoutSubtreeIfNeeded()
            stack.layoutSubtreeIfNeeded()
            let needed = stack.fittingSize.height
            guard needed > 1 else { return }
            // 창은 내용에 맞춘다. 하한은 "사용자가 모서리를 끌어 줄일 수 있는
            // 최소"일 뿐, 지금 크기를 그 값까지 늘리는 데 쓰지 않는다.
            // 늘리는 데 쓰면 작업이 한 줄뿐일 때 창 아래가 200pt짜리 빈 판으로
            // 남는다 (2026-09-17, 감사 지적).
            let desired = needed + 32
            let userFloor = wantsFloor
                ? Self.jobsWindowMinimum.height
                : Self.jobsWindowEmptyFloor.height
            let minimum = NSSize(
                width: Self.jobsWindowMinimum.width,
                height: min(userFloor, desired)
            )
            if abs(window.minSize.height - minimum.height) > 0.5
                || abs(window.minSize.width - minimum.width) > 0.5 {
                window.minSize = minimum
            }
            let current = content.bounds.height
            guard abs(current - desired) > 12 else { return }
            var frame = window.frame
            let delta = current - desired
            if delta > 0 {
                // 줄일 때는 위쪽 모서리를 고정한다. 아래에서 줄이면 창이 화면
                // 밖으로 밀린다.
                frame.size.height -= delta
                frame.origin.y += delta
            } else {
                // 늘릴 때는 화면 위쪽을 넘지 않게 막는다.
                let grown = min(-delta, frame.origin.y)
                guard grown > 0 else { return }
                frame.size.height += grown
                frame.origin.y -= grown
            }
            window.setFrame(frame, display: false, animate: false)
            content.layoutSubtreeIfNeeded()
        }
    }

    /// 표의 최소 높이를 지금 목록의 줄 수에 맞춘다.
    ///
    /// 표는 늘 다섯 줄 높이(200pt)를 최소로 잡고 있었다. 작업이 한 줄뿐이어도
    /// 그 200pt가 그대로 남아 창 아래가 빈 판이 되었다. 줄 수에 맞춰 하한을
    /// 낮추면 창도 함께 줄어든다. 사용자가 창을 키우면 스택이 남는 자리를
    /// 표에 주므로 표는 그대로 늘어난다 (2026-09-17, 감사 지적).
    func fitJobsTableHeight() {
        guard let constraint = jobsTableHeight, let table = jobsTable else { return }
        let header = table.headerView?.frame.height ?? 28
        let rowHeight = table.rowHeight + table.intercellSpacing.height
        let rows = CGFloat(max(displayedJobs.count, 0))
        // 표 양식이 두는 세로 여백까지 더한다. 빼고 잡으면 표가 클립 뷰보다
        // 커져 마지막 줄의 아래쪽이 잘리고, 그 자리가 빈 띠로 남는다
        // (2026-09-18).
        let wanted = header + rowHeight * rows + Self.jobsTableStylePadding
        let clamped = min(
            Self.jobsTableMaximumHeight,
            max(header + rowHeight + Self.jobsTableStylePadding, wanted)
        )
        guard abs(constraint.constant - clamped) > 0.5 else { return }
        constraint.constant = clamped
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
        // 고른 줄이 없으면 과정 카드와 조치 단추를 접는다. 접힌 뷰는 스택이
        // 자리째 빼므로 목록이 그만큼 넓어진다 (2026-09-16, 6 Pro 지적).
        jobsTraceCard?.isHidden = job == nil
        jobsActions?.isHidden = job == nil
        switch jobsStatus {
        case "sent":
            jobsHint?.stringValue = "이미 보낸 기록입니다. 다시 보내지 않습니다."
        case "skipped":
            jobsHint?.stringValue = "건너뛴 작업입니다. 다시 보내지 않습니다."
        case "unknown":
            jobsHint?.stringValue = "결과를 모르는 작업입니다. 확인은 카카오톡에 이미 올라간 경우만 기록합니다."
        default:
            jobsHint?.stringValue = "시간 순서입니다. 어느 쪽을 눌러도 다시 보내지 않습니다."
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
        // 표 200 + 머리말에 단추 줄까지 더하면 400pt가 필요하다. 예전에는
        // 과정 카드 152pt까지 미리 잡아 508pt로 두었는데, 그 카드는 고른
        // 작업이 있을 때만 나오므로 목록만 볼 때는 필요 없는 높이였다
        // (2026-09-16).
        let window = Chrome.operatorWindow(
            title: "작업 목록",
            size: NSSize(width: 720, height: 540),
            autosave: "AutoReplyJobs",
            minimum: Self.jobsWindowMinimum
        )
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

        // 창의 규칙을 길게 설명하던 자리다. 운영자가 알아야 하는 것은 표가
        // 시간 순서라는 것과 두 단추가 다시 보내지 않는다는 것뿐이라 한
        // 줄로 줄인다 (2026-09-16).
        let hint = Chrome.hint("시간 순서입니다. 어느 쪽을 눌러도 다시 보내지 않습니다.")
        jobsHint = hint

        // 표가 비면 회색 띠만 남아 창 아래가 통째로 비어 보인다. 표 대신
        // 이유를 적어 준다. 자리는 보통 라벨처럼 15pt를 미리 차지하지
        // 않도록 AutoHidingLabel을 쓴다 (2026-09-16).
        let jobsEmpty = Chrome.statusLabel(size: 12, lines: 3)
        jobsEmptyLabel = jobsEmpty
        // 표가 빈 자리를 이유 한 줄로 대신한다.
        //
        // 표를 그대로 두고 위에 한 줄만 얹으면 빈 표의 교차 줄무늬가 창
        // 아래까지 늘어선다. 실제로 그렇게 만들어 보니 회색 줄이 다섯 줄
        // 남아, 목록이 비었다는 한 줄보다 그 줄무늬가 먼저 눈에 들어왔다.
        // 표를 숨기고 같은 자리에 이유를 적되, 판은 한 줄 높이로 둔다
        // (2026-09-16).
        let jobsEmptyState = EmptyStateView(symbolName: "tray", compact: true)
        jobsEmptyState.isHidden = true
        self.jobsEmptyState = jobsEmptyState
        let skip = Chrome.roundedButton("미확인 건너뛰기", target: self, action: #selector(jobsSkipClicked))
        skip.isEnabled = false
        jobsSkipButton = skip
        let ack = Chrome.roundedButton("전송된 것으로 확인", target: self, action: #selector(jobsAckClicked))
        ack.isEnabled = false
        jobsAckButton = ack
        // 단추 둘이 왼쪽에 몰려 오른쪽 절반이 빈 자리로 남던 것을, 같은
        // 폭으로 나눠 창 끝까지 채운다 (2026-09-16).
        let actions = Chrome.actionRow([skip, ack])

        let (scroll, table) = Chrome.table()
        table.delegate = self
        table.dataSource = self
        for spec in [
            ("when", "시각", 168.0),
            ("status", "상태", 88.0),
            ("reason", "구분", 340.0),
        ] as [(String, String, CGFloat)] {
            // 머리글은 본문과 같은 쪽에 붙는다. 예전에는 본문이 왼쪽인
            // "구분" 열의 머리글만 가운데라 제목이 칸 가운데에 떠 보였다
            // (2026-09-16).
            Chrome.addColumn(
                table,
                id: spec.0,
                title: spec.1,
                width: spec.2,
                minWidth: 72,
                alignment: spec.0 == "reason" ? .left : .center
            )
        }
        jobsTable = table
        jobsTableScroll = scroll

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

        // 목록 요약과 안내는 한 카드로 묶고, 필터는 바로 아래 한 줄에 둔다.
        // 예전에는 여섯 줄이 각자 한 줄씩 차지해 창마다 위아래 여백만 남았다
        // (2026-09-16).
        let headerContent = Chrome.vstack([summary, hint], spacing: 4)
        let headerCard = Chrome.card(headerContent, padding: 12)

        let filterRow = Chrome.hstack([filter, Chrome.spacer()], spacing: 8)

        let traceTitle = Chrome.label("선택한 작업의 과정", size: 11, weight: .semibold, color: .secondaryLabelColor, lines: 1)
        let traceContent = Chrome.vstack([traceTitle, traceScroll], spacing: 6)
        let traceCard = Chrome.card(traceContent, padding: 10)

        // 과정 카드와 조치 단추는 고른 작업이 있을 때만 나온다.
        //
        // 예전에는 둘 다 늘 자리를 차지했다. 목록이 비었거나 아무것도 고르지
        // 않았을 때 화면의 절반이 "작업을 선택하면…" 한 줄과 누를 수 없는
        // 단추로 채워졌다. 고르면 그때 펼친다 (2026-09-16, 6 Pro 지적).
        traceCard.isHidden = true
        actions.isHidden = true
        jobsTraceCard = traceCard
        jobsActions = actions

        // 빈 상태 판은 표와 같은 자리를 쓰되, 스택 안에서는 표 바로 앞에 둔다.
        // 둘 중 하나만 보이므로 화면에는 한 자리만 남는다 (2026-09-16).
        let stack = Chrome.vstack([headerCard, filterRow, jobsEmpty, jobsEmptyState, scroll, traceCard, actions], spacing: 10)
        jobsStack = stack
        Chrome.fill(stack, in: content)
        let tableHeight = scroll.heightAnchor.constraint(
            greaterThanOrEqualToConstant: Self.jobsTableMaximumHeight
        )
        jobsTableHeight = tableHeight
        NSLayoutConstraint.activate([
            headerCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            headerContent.widthAnchor.constraint(equalTo: headerCard.widthAnchor, constant: -24),
            summary.widthAnchor.constraint(equalTo: headerContent.widthAnchor),
            hint.widthAnchor.constraint(equalTo: headerContent.widthAnchor),
            filterRow.widthAnchor.constraint(equalTo: stack.widthAnchor),
            filter.widthAnchor.constraint(lessThanOrEqualTo: filterRow.widthAnchor),
            jobsEmpty.widthAnchor.constraint(equalTo: stack.widthAnchor),
            jobsEmptyState.widthAnchor.constraint(equalTo: stack.widthAnchor),
            // 빈 상태 판은 스스로 높이를 갖지 않아, 창을 내용에 맞추면 0으로
            // 붕괴해 안내문이 통째로 사라진다. 예전에는 창의 최소 높이가
            // 240으로 버텨 주었을 뿐이다 (2026-09-17, 감사 지적).
            jobsEmptyState.heightAnchor.constraint(greaterThanOrEqualToConstant: 96),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            actions.widthAnchor.constraint(equalTo: stack.widthAnchor),
            // 표의 최소 높이는 줄 수에 맞춰 바뀐다. 고정 200으로 두면
            // 작업이 한 줄뿐일 때도 창이 그 높이에 묶여 아래가 빈 판으로
            // 남는다 (2026-09-17, 감사 지적).
            tableHeight,
            traceCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            traceContent.widthAnchor.constraint(equalTo: traceCard.widthAnchor, constant: -20),
            traceTitle.widthAnchor.constraint(equalTo: traceContent.widthAnchor),
            traceScroll.widthAnchor.constraint(equalTo: traceContent.widthAnchor),
            traceScroll.heightAnchor.constraint(equalToConstant: 112),
        ])
        jobsWindow = window
    }

    func ensureLogWindow() {
        if logWindow != nil {
            return
        }
        // 여섯 열의 최소 너비 합(624pt)에 창 여백을 더한 값보다 좁아지면
        // 마지막 "답변" 열이 표 밖으로 밀려나고 가로 스크롤이 없어 읽을 수
        // 없다. 세로는 머리말 117 + 스크롤 260 + 상세 208에 사이 여백을
        // 더해 650pt가 필요하다. 처음 여는 크기도 그보다 커야 표가 눌리지
        // 않는다 (2026-09-16).
        let window = Chrome.operatorWindow(
            title: "답변 기록",
            size: NSSize(width: 1000, height: 756),
            autosave: "AutoReplyReceipts",
            minimum: NSSize(width: 704, height: 650)
        )
        let content = NSView()
        window.contentView = content

        let summary = Chrome.summary("상태를 읽는 중")
        logSummary = summary
        // 표가 무엇을 보여 주지 않는지만 한 줄로 남긴다. 원문이 나오지
        // 않는다는 사실은 운영자가 오해하면 안 되는 부분이라 지우지 않는다
        // (2026-09-16).
        let hint = Chrome.hint("턴마다 한 줄입니다. 원문은 나오지 않습니다.")
        logHint = hint

        let scope = NSPopUpButton(frame: .zero, pullsDown: false)
        scope.translatesAutoresizingMaskIntoConstraints = false
        scope.target = self
        scope.action = #selector(logScopeChanged(_:))
        logScopeButton = scope

        let room = NSPopUpButton(frame: .zero, pullsDown: false)
        room.translatesAutoresizingMaskIntoConstraints = false
        room.target = self
        room.action = #selector(logRoomChanged(_:))
        room.lineBreakMode = .byTruncatingTail
        logRoomButton = room

        let status = Chrome.label("기록을 읽는 중", size: 11, color: .secondaryLabelColor, lines: 1)
        status.alignment = .right
        logStatusField = status
        // 필터는 한 줄로 붙이고, 남는 가로 공간은 spacer가 먹는다. 예전에는
        // 팝업 둘과 상태가 각자 폭을 요구해 가운데가 벌어졌다 (2026-09-16).
        scope.setContentHuggingPriority(.required, for: .horizontal)
        room.setContentHuggingPriority(.required, for: .horizontal)
        status.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let scopeTitle = Chrome.label("결과", size: 11, color: .secondaryLabelColor, lines: 1)
        scopeTitle.setContentHuggingPriority(.required, for: .horizontal)
        let roomTitle = Chrome.label("채팅방", size: 11, color: .secondaryLabelColor, lines: 1)
        roomTitle.setContentHuggingPriority(.required, for: .horizontal)
        let toolbar = Chrome.hstack([scopeTitle, scope, roomTitle, room, Chrome.spacer(), status], spacing: 8)

        // 기록을 읽기 전에는 빈 글자다. 보통 라벨이면 15pt를 차지해 창
        // 가운데에 아무것도 없는 띠가 생긴다 (2026-09-16).
        let empty = Chrome.statusLabel(size: 12, lines: 3)
        logEmptyLabel = empty
        let emptyState = EmptyStateView(symbolName: "clock.arrow.circlepath")
        emptyState.isHidden = true
        self.logEmptyState = emptyState

        let (scroll, table) = Chrome.table()
        table.delegate = self
        table.dataSource = self
        Chrome.addColumn(table, id: "time", title: "시각", width: 92, minWidth: 76, alignment: .left)
        Chrome.addColumn(table, id: "room", title: "채팅방", width: 150, minWidth: 96, alignment: .left)
        Chrome.addColumn(table, id: "outcome", title: "결과", width: 92, minWidth: 76, alignment: .center)
        Chrome.addColumn(table, id: "reason", title: "사유", width: 150, minWidth: 96, alignment: .left)
        Chrome.addColumn(table, id: "retrieval", title: "검색·입력", width: 200, minWidth: 120, alignment: .left)
        Chrome.addColumn(table, id: "reply", title: "답변", width: 220, minWidth: 120, alignment: .left)
        logTable = table
        logTableScroll = scroll

        let detailScroll = NSScrollView()
        detailScroll.translatesAutoresizingMaskIntoConstraints = false
        detailScroll.hasVerticalScroller = true
        detailScroll.hasHorizontalScroller = false
        detailScroll.autohidesScrollers = true
        detailScroll.borderType = .bezelBorder
        detailScroll.drawsBackground = true
        let detail = NSTextView(frame: .zero)
        detail.isEditable = false
        detail.isSelectable = true
        detail.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        detail.textColor = NSColor.labelColor
        detail.backgroundColor = NSColor.textBackgroundColor
        detail.minSize = NSSize(width: 0, height: 0)
        detail.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        detail.isHorizontallyResizable = false
        detail.isVerticallyResizable = true
        detail.textContainerInset = NSSize(width: 10, height: 8)
        detail.textContainer?.widthTracksTextView = true
        detail.string = ""
        detailScroll.documentView = detail
        logDetailView = detail

        // 중첩 테두리를 제거하고 불필요한 설명 대신 요약과 도구줄을 단일 카드로 정돈한다 (2026-09-17).
        let headerContent = Chrome.vstack([summary, toolbar], spacing: 10)
        let headerCard = Chrome.card(headerContent, padding: 12)

        let detailTitle = Chrome.label("선택한 턴의 자세한 기록", size: 11, weight: .semibold, color: .secondaryLabelColor, lines: 1)
        let detailContent = Chrome.vstack([detailTitle, detailScroll], spacing: 6)
        let detailCard = Chrome.card(detailContent, padding: 10)
        logDetailCard = detailCard

        // 빈 상태 판은 표와 같은 자리를 쓰되, 스택 안에서는 표 바로 앞에 둔다.
        // 둘 중 하나만 보이므로 화면에는 한 자리만 남는다 (2026-09-16).
        let stack = Chrome.vstack([headerCard, empty, emptyState, scroll, detailCard], spacing: 10)
        Chrome.fill(stack, in: content)
        NSLayoutConstraint.activate([
            headerCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            headerContent.widthAnchor.constraint(equalTo: headerCard.widthAnchor, constant: -24),
            summary.widthAnchor.constraint(equalTo: headerContent.widthAnchor),
            toolbar.widthAnchor.constraint(equalTo: headerContent.widthAnchor),
            empty.widthAnchor.constraint(equalTo: stack.widthAnchor),
            emptyState.widthAnchor.constraint(equalTo: stack.widthAnchor),
            // 빈 상태 판은 표와 같은 높이를 차지한다. 표를 숨긴 자리가 그대로
            // 빈 띠로 남지 않게 하려면 높이가 같아야 한다 (2026-09-16).
            emptyState.heightAnchor.constraint(greaterThanOrEqualToConstant: 260),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            detailCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            detailContent.widthAnchor.constraint(equalTo: detailCard.widthAnchor, constant: -20),
            detailTitle.widthAnchor.constraint(equalTo: detailContent.widthAnchor),
            detailScroll.widthAnchor.constraint(equalTo: detailContent.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 260),
            detailScroll.heightAnchor.constraint(equalToConstant: 168),
            scope.widthAnchor.constraint(greaterThanOrEqualToConstant: 112),
            scope.widthAnchor.constraint(lessThanOrEqualToConstant: 168),
            room.widthAnchor.constraint(greaterThanOrEqualToConstant: 148),
            room.widthAnchor.constraint(lessThanOrEqualToConstant: 260),
        ])
        logWindow = window
    }

    func updateLogWindow(_ model: MenubarModel) {
        let inspected = Self.selectedRoom(in: model, preferred: inspectedRoomId)
        // 이 창은 기록 목록을 보여 주는 곳이다. 예전에는 창 맨 위에 방의
        // 8단계 파이프라인 띠를 늘 붙여 두었다. 그 띠는 지금 고른 방의
        // 진행 상황인데 목록은 결과·채팅방 필터로 따로 걸러, 같은 화면에서
        // 두 가지 다른 기준이 섞였다. 파이프라인은 메뉴 패널에 이미 있고,
        // 여기서는 목록이 곧 답이다 (2026-09-16, 6 Pro 지적).
        _ = inspected
        let summary = model.log_summary?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        // 이 창의 머리말은 이 창의 목록을 설명한다.
        //
        // 코어의 시스템 요약은 한 상태를 여러 문장으로 늘어놓을 때가 있다.
        // 그대로 넣으면 창을 최소 크기로 줄였을 때 세 줄을 넘겨 뒷문장이
        // 화면에서 사라지고, 정작 목록은 그만큼 아래로 밀린다. 잠금 사유
        // 같은 문장은 메뉴 패널이 이미 보여 준다 (2026-09-16, 6 Pro 지적).
        let levelTitle = Palette.title(level: model.level)
        let scope = Self.logScopeSummary(summary)
        logSummary?.stringValue = scope.isEmpty
            ? "\(levelTitle) — \(Palette.caption(code: model.primary_code))"
            : "\(levelTitle) — \(scope)"
        // 잘라낸 뒷문장은 여기 남는다. 머리말은 한 줄로 두되, 왜 그런지가
        // 궁금할 때 마우스를 올리면 전문을 읽을 수 있다 (2026-09-16).
        logSummary?.toolTip = summary.isEmpty ? nil : summary
        applyLogReceipts(model)
    }

    @objc func logScopeChanged(_ sender: NSPopUpButton) {
        let index = sender.indexOfSelectedItem
        logScopeValue = index >= 0 && index < logScopes.count ? logScopes[index].code : ""
        applyLogFilter()
    }

    /// 코어의 상태 요약에서 머리말에 쓸 첫 문장만 남긴다.
    ///
    /// 요약은 "주의 — 감독 프로그램이 정상이 아닙니다. 안전을 위해 자동
    /// 답변을 멈춘 상태입니다. …"처럼 한 상태를 여러 문장으로 설명한다.
    /// 창 머리말은 한 줄이어야 하고, 뒤 문장들은 같은 사실을 풀어 쓴 것이라
    /// 창을 최소 크기로 줄이면 화면에서 잘려 나간다. 전문은 머리말의
    /// 도움말에 그대로 남겨 두어 필요할 때 읽을 수 있게 한다 (2026-09-16).
    static func logScopeSummary(_ summary: String) -> String {
        let raw = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return "" }
        // 머리말은 "상태 — 내용" 꼴이라, 코어가 붙인 상태 머리말은 떼고
        // 우리가 다시 붙인다. 두 번 붙으면 "주의 — 주의 — …"가 된다.
        var body = raw
        if let separator = body.range(of: "—") {
            body = String(body[separator.upperBound...]).trimmingCharacters(in: .whitespaces)
        }
        for terminator in ["다.", ".", "!"] {
            if let end = body.range(of: terminator) {
                body = String(body[..<end.upperBound])
                break
            }
        }
        body = body.trimmingCharacters(in: .whitespaces)
        // 첫 문장이 길어도 한 줄을 넘지 않게 막는다. 창을 최소 크기로 줄여도
        // 남아야 하므로 폭을 재는 대신 글자 수로 자른다.
        let limit = 60
        if body.count > limit {
            body = String(body.prefix(limit)).trimmingCharacters(in: .whitespaces) + "…"
        }
        return body
    }

    @objc func logRoomChanged(_ sender: NSPopUpButton) {
        let index = sender.indexOfSelectedItem
        logRoomValue = index >= 0 && index < logRoomIds.count ? logRoomIds[index] : ""
        applyLogFilter()
    }

    /// 기록 창을 채운다. 문구와 판단은 코어(CLI)가 만들고 여기서는 방으로 묶어
    /// 최신 순으로 줄 세우기만 한다. 2초마다 불리므로 목록이 그대로면 표를 다시
    /// 그리지 않는다 — 다시 그리면 스크롤과 선택이 튄다(2026-09-15).
    func applyLogReceipts(_ model: MenubarModel) {
        let page = model.reply_receipts
        var rows: [ReceiptRow] = []
        var titles: [(id: String, title: String)] = []
        for room in page?.rooms ?? [] {
            let id = (room.chat_id ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let title = receiptRoomTitle(id, room: room, model: model)
            titles.append((id, title))
            for receipt in room.receipts ?? [] {
                let event = (receipt.event_id ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                let time = receipt.display_time ?? receipt.clock ?? ""
                rows.append(ReceiptRow(
                    key: "\(id)|\(event.isEmpty ? time + (receipt.reason_code ?? "") : event)",
                    roomId: id,
                    room: title,
                    time: time,
                    outcome: receipt.outcome ?? "",
                    outcomeText: receipt.outcome_text ?? "",
                    reasonText: receipt.reason_text ?? "",
                    retrievalText: receipt.retrieval_text ?? "",
                    reply: receipt.preview_text ?? "",
                    attention: receipt.needs_attention ?? false,
                    summary: receipt.summary ?? "",
                    detail: receipt.detail ?? [],
                    sortKey: receipt.recorded_at ?? ""
                ))
            }
        }
        rows.sort { left, right in
            if left.sortKey == right.sortKey {
                return left.time > right.time
            }
            return left.sortKey > right.sortKey
        }
        let fingerprint = rows.map { "\($0.key):\($0.outcome):\($0.retrievalText):\($0.reply)" }.joined(separator: "|")
        let changed = fingerprint != lastReceiptFingerprint
        lastReceiptFingerprint = fingerprint
        receiptRows = rows
        receiptTitles = titles
        logPageMissing = page == nil
        logPageUnread = page?.missing ?? false
        logReadAt = page?.read_at ?? 0
        syncLogScopes()
        syncLogRooms()
        applyLogFilter(reload: changed)
    }

    /// 방 이름은 채팅방 목록에서 먼저 찾고, 없으면 기록이 스스로 적어 둔 이름을
    /// 쓴다. 둘 다 없으면 방 번호를 그대로 보여 준다.
    func receiptRoomTitle(_ id: String, room: ReplyReceiptRoom, model: MenubarModel) -> String {
        if let chat = model.available_chats?.first(where: { String($0.chat_id) == id }), !chat.title.isEmpty {
            return chat.title
        }
        for receipt in room.receipts ?? [] {
            let name = (receipt.chat ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty {
                return name
            }
        }
        let own = (room.chat ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !own.isEmpty, own != id {
            return own
        }
        return id
    }

    /// 결과 필터에는 실제로 기록에 나온 결과만 넣는다. 항목 이름도 코어가 쓴
    /// 낱말을 그대로 쓰므로 화면과 기록의 말이 갈라지지 않는다.
    func syncLogScopes() {
        var scopes: [(code: String, title: String)] = [("", "전체"), ("attention", "확인 필요")]
        for code in Self.receiptOutcomeCodes {
            if let row = receiptRows.first(where: { $0.outcome == code }) {
                scopes.append((code, row.outcomeText.isEmpty ? code : row.outcomeText))
            }
        }
        logScopes = scopes
        guard let popup = logScopeButton else { return }
        let titles = scopes.map { $0.title }
        if popup.itemTitles != titles {
            popup.removeAllItems()
            popup.addItems(withTitles: titles)
        }
        if let index = scopes.firstIndex(where: { $0.code == logScopeValue }) {
            if popup.indexOfSelectedItem != index {
                popup.selectItem(at: index)
            }
        } else {
            logScopeValue = ""
            popup.selectItem(at: 0)
        }
    }

    func syncLogRooms() {
        var ids = [""]
        var titles = ["모든 채팅방"]
        for room in receiptTitles where !room.id.isEmpty {
            if ids.contains(room.id) {
                continue
            }
            ids.append(room.id)
            titles.append(room.title)
        }
        logRoomIds = ids
        guard let popup = logRoomButton else { return }
        if popup.itemTitles != titles {
            popup.removeAllItems()
            popup.addItems(withTitles: titles)
        }
        if let index = ids.firstIndex(of: logRoomValue) {
            if popup.indexOfSelectedItem != index {
                popup.selectItem(at: index)
            }
        } else {
            logRoomValue = ""
            popup.selectItem(at: 0)
        }
    }

    func applyLogFilter(reload: Bool = true) {
        rememberLogSelection()
        displayedReceipts = receiptRows.filter { row in
            if !logRoomValue.isEmpty, row.roomId != logRoomValue {
                return false
            }
            if logScopeValue.isEmpty {
                return true
            }
            if logScopeValue == "attention" {
                return row.attention
            }
            return row.outcome == logScopeValue
        }
        if reload {
            logTable?.reloadData()
        }
        restoreLogSelection()
        // 표를 숨기면 그 자리가 빈 띠로 남는다. 같은 자리를 빈 상태 판이
        // 대신 차지하고 왜 비었는지 적는다. 예전에는 걸러지기 전의 전체
        // 기록(receiptRows)을 보고 판단해서, 결과 필터로 모든 줄이 빠진
        // 뒤에도 안내가 뜨지 않고 빈 표만 남았다 (2026-09-16).
        let empty = displayedReceipts.isEmpty
        logEmptyLabel?.stringValue = ""
        logTableScroll?.isHidden = empty
        logEmptyState?.isHidden = !empty
        logEmptyState?.titleText = logEmptyTitle()
        logEmptyState?.detailText = logEmptyDetail()
        applyLogDetail()
        updateLogStatus()
    }

    func rememberLogSelection() {
        guard let table = logTable else { return }
        let row = table.selectedRow
        if row >= 0, row < displayedReceipts.count {
            selectedReceiptKey = displayedReceipts[row].key
        }
    }

    /// 선택은 (방, 턴)으로 기억한다. 새 기록이 위에 붙어도 같은 턴을 계속
    /// 보고 있어야 하고, 그 턴이 사라졌을 때만 맨 위로 돌아간다.
    func restoreLogSelection() {
        guard let table = logTable else { return }
        if let index = displayedReceipts.firstIndex(where: { $0.key == selectedReceiptKey }) {
            if table.selectedRow != index {
                table.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false)
            }
            return
        }
        if displayedReceipts.isEmpty {
            selectedReceiptKey = ""
            if table.selectedRow != -1 {
                table.deselectAll(nil)
            }
            return
        }
        selectedReceiptKey = displayedReceipts[0].key
        table.selectRowIndexes(IndexSet(integer: 0), byExtendingSelection: false)
    }

    func applyLogDetail() {
        guard let view = logDetailView else { return }
        let row = logTable?.selectedRow ?? -1
        var lines: [String] = []
        let hasSelection = row >= 0 && row < displayedReceipts.count
        if hasSelection {
            lines = displayedReceipts[row].detail
            if lines.isEmpty, !displayedReceipts[row].summary.isEmpty {
                lines = [displayedReceipts[row].summary]
            }
        }
        // 고른 턴이 없으면 이 카드는 접는다.
        //
        // 예전에는 그 자리에 코어의 시스템 로그 줄을 대신 채웠다. 제목은
        // "선택한 턴의 자세한 기록"인데 내용은 감독·감시·세션 이야기라,
        // 운영자는 한 턴의 기록과 시스템 상태를 같은 칸에서 읽어야 했다.
        // 시스템 상태는 메뉴 패널이 이미 보여 준다 (2026-09-16, 6 Pro 지적).
        logDetailCard?.isHidden = !hasSelection
        guard hasSelection else {
            lastLogDetailKey = ""
            return
        }
        let text = lines.isEmpty ? "이 턴에 대한 자세한 기록이 없습니다." : lines.joined(separator: "\n")
        if text == lastLogDetailKey, (view.textStorage?.length ?? 0) > 0 {
            return
        }
        lastLogDetailKey = text
        view.string = text
    }

    /// 고른 행이 없을 때 아래 칸에 채우는 코어의 한 줄 요약.
    func logFriendlyLines() -> [String] {
        guard let model = lastModel else { return [] }
        let rooms = model.reply_receipts?.rooms ?? []
        if !logRoomValue.isEmpty {
            if let room = rooms.first(where: { ($0.chat_id ?? "") == logRoomValue }),
               let lines = room.lines, !lines.isEmpty {
                return lines
            }
        }
        let lines = rooms.flatMap { $0.lines ?? [] }
        return lines.isEmpty ? displayLogLines(model) : lines
    }

    func logEmptyMessage() -> String {
        if logPageMissing {
            return "설치된 앱이 턴 기록을 아직 보내지 않습니다. 앱과 CLI를 다시 설치하면 여기에 채워집니다."
        }
        if receiptRows.isEmpty || logPageUnread {
            return "아직 기록된 턴이 없습니다. 답변을 시도하면 한 줄씩 쌓입니다."
        }
        return "고른 조건에 맞는 기록이 없습니다."
    }

    /// 빈 상태 판의 제목. 표 자리를 대신 차지하는 만큼 한 줄로 짧게 적는다.
    func logEmptyTitle() -> String {
        if logPageMissing {
            return "설치된 앱이 턴 기록을 아직 보내지 않습니다"
        }
        if receiptRows.isEmpty || logPageUnread {
            return "아직 기록된 턴이 없습니다"
        }
        return "고른 조건에 맞는 기록이 없습니다"
    }

    /// 빈 상태 판의 두 번째 줄: 지금 무엇을 하면 되는지.
    func logEmptyDetail() -> String {
        if logPageMissing {
            return "앱과 CLI를 다시 설치하면 여기에 채워집니다."
        }
        if receiptRows.isEmpty || logPageUnread {
            return "답변을 시도하면 한 줄씩 쌓입니다."
        }
        return "위에서 결과나 채팅방을 바꿔 보세요. 기록은 \(receiptRows.count)건 있습니다."
    }

    /// 결과 필터에 넣는 순서. 항목 낱말은 코어가 준 outcome_text를 그대로 쓴다.
    static let receiptOutcomeCodes = ["sent", "deferred", "scheduled", "skipped"]

    func logOutcomeColor(_ row: ReceiptRow) -> NSColor {
        if row.attention {
            return NSColor.systemRed
        }
        switch row.outcome {
        case "sent":
            return NSColor.systemGreen
        case "deferred":
            return NSColor.systemOrange
        case "skipped", "scheduled":
            return NSColor.secondaryLabelColor
        default:
            return NSColor.labelColor
        }
    }

    func updateLogStatus() {
        guard let field = logStatusField else { return }
        var parts: [String] = []
        if displayedReceipts.count != receiptRows.count {
            parts.append("\(displayedReceipts.count)/\(receiptRows.count)건")
        } else {
            parts.append("\(receiptRows.count)건")
        }
        let attention = displayedReceipts.filter { $0.attention }.count
        if attention > 0 {
            parts.append("확인 필요 \(attention)")
        }
        if logReadAt > 0 {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "ko_KR")
            formatter.dateFormat = "HH:mm"
            parts.append("\(formatter.string(from: Date(timeIntervalSince1970: logReadAt))) 기준")
        }
        if logPageMissing {
            parts.append("기록 없음")
        }
        field.stringValue = parts.joined(separator: " · ")
        field.textColor = attention > 0 ? NSColor.systemRed : NSColor.secondaryLabelColor
    }

    func ensureRoomsWindow() {
        if roomsWindow != nil {
            return
        }
        // 표 최소 높이가 300pt라 380pt까지 줄이면 머리말만 남고 목록이
        // 사라진다. 머리말 105 + 표 300에 사이 여백을 더해 470pt가 필요하다
        // (2026-09-16).
        let window = Chrome.operatorWindow(
            title: "단체 채팅방",
            // 머리말과 표를 합친 만큼만 준다. 예전에는 창 위 파이프라인 띠
            // 39pt와 그 여백까지 더해 608pt로 열었다 (2026-09-16).
            size: NSSize(width: 760, height: 470),
            autosave: "AutoReplyRooms",
            minimum: NSSize(width: 620, height: 440)
        )
        let content = NSView()
        window.contentView = content

        // 켜고 끄는 규칙은 두 문장이면 끝난다. 어느 칸을 눌러야 하는지까지
        // 나열하던 문장은 표의 칸 제목이 이미 말한다 (2026-09-16).
        let hint = Chrome.hint("칸을 눌러 켜고 끕니다. 답변·긱뉴스를 켜면 동작도 함께 켜집니다.")

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
        roomsTableScroll = scroll
        for spec in [
            ("title", "제목", 280.0),
            ("members", "인원", 52.0),
            ("live", "동작", 64.0),
            ("reply", "답변", 64.0),
            ("geek", "긱뉴스", 72.0),
            ("catalog", "추가됨", 64.0),
        ] as [(String, String, CGFloat)] {
            // 제목 열의 본문은 왼쪽이다. 머리글만 가운데면 방 이름 위에서
            // 제목이 칸 가운데에 떠 보인다 (2026-09-16).
            Chrome.addColumn(
                table,
                id: spec.0,
                title: spec.1,
                width: spec.2,
                minWidth: 48,
                alignment: spec.0 == "title" ? .left : .center
            )
        }
        roomsTable = table

        // 검색과 추가·삭제는 안내 한 줄과 같은 카드에 둔다. 예전에는 이
        // 도구줄이 카드 안의 또 다른 카드라, "방을 찾고 설정한다"는 한 가지
        // 일에 테두리가 두 겹이었다 (2026-09-16, 6 Pro 지적).
        let headerContent = Chrome.vstack([hint, toolbar], spacing: 8)
        let headerCard = Chrome.card(headerContent, padding: 12)

        let stack = Chrome.vstack([headerCard, scroll], spacing: 10)
        roomsStack = stack
        Chrome.fill(stack, in: content)
        NSLayoutConstraint.activate([
            headerCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            headerContent.widthAnchor.constraint(equalTo: headerCard.widthAnchor, constant: -24),
            hint.widthAnchor.constraint(equalTo: headerContent.widthAnchor),
            toolbar.widthAnchor.constraint(equalTo: headerContent.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 300),
            filter.widthAnchor.constraint(greaterThanOrEqualToConstant: 220),
        ])
        window.initialFirstResponder = filter
        roomsWindow = window
    }

    /// 저장된 큰 창 크기가 표의 빈 영역으로 흘러 들어가지 않게, 머리말과
    /// 표의 실제 최소 높이에 맞춘다. 사용자가 직접 늘린 뒤에는 그대로 둔다.
    func fitRoomsWindow() {
        guard !roomsFitting,
              !roomsWindowUserResized,
              let window = roomsWindow,
              let stack = roomsStack,
              let scroll = roomsTableScroll,
              let content = window.contentView else { return }
        roomsFitting = true
        defer { roomsFitting = false }
        for _ in 0..<3 {
            content.layoutSubtreeIfNeeded()
            stack.layoutSubtreeIfNeeded()
            let needed = stack.fittingSize.height
            guard needed > 1 else { return }
            // 스크롤 뷰는 창의 남는 높이를 전부 먹는다. 그 늘어난 부분을
            // fittingSize에서 빼야 오토세이브된 큰 프레임을 다시 목표 높이로
            // 쓰지 않는다.
            let tableExcess = max(0, scroll.bounds.height - 300)
            let desired = needed - tableExcess + 32
            let current = content.bounds.height
            guard abs(current - desired) > 12 else { return }
            var frame = window.frame
            let delta = current - desired
            if delta > 0 {
                frame.size.height -= delta
                frame.origin.y += delta
            } else {
                let grown = min(-delta, frame.origin.y)
                guard grown > 0 else { return }
                frame.size.height += grown
                frame.origin.y -= grown
            }
            window.setFrame(frame, display: false, animate: false)
            content.layoutSubtreeIfNeeded()
        }
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
        // 이 창의 주 내용은 방 목록이다. 창 맨 위에 붙어 있던 8단계 파이프라인
        // 띠는 지금 고른 방의 진행 상황이라 목록과 다른 기준이었다. 방이 지금
        // 도는지는 표의 "동작" 칸이 이미 말하므로 띠는 뺀다 (2026-09-16,
        // 6 Pro 지적).
        if fingerprint != lastRoomsFingerprint {
            lastRoomsFingerprint = fingerprint
            applyRoomsFilter()
        } else {
            restoreRoomsSelection()
        }
        fitRoomsWindow()
    }

    func reusedLabel(
        in tableView: NSTableView,
        column: String,
        text: String,
        font: NSFont,
        color: NSColor,
        alignment: NSTextAlignment = .center,
        toolTip: String? = nil
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
        cell.label.toolTip = toolTip
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

        if tableView === logTable {
            return displayedReceipts.count
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
        // 행마다 옅은 교차 배경을 직접 칠한다. 표의 내장 교차 배경은 행이
        // 없는 자리까지 칠해서, 행이 한두 개인 창의 아래 절반이 회색 줄무늬로
        // 남았다 (2026-09-17).
        return tableCellView(tableView, column: tableColumn, row: row)
    }

    func tableView(_ tableView: NSTableView, rowViewForRow row: Int) -> NSTableRowView? {
        let ident = NSUserInterfaceItemIdentifier("row.stripe")
        let view = tableView.makeView(withIdentifier: ident, owner: self) as? StripedRowView
            ?? StripedRowView()
        view.identifier = ident
        view.isOdd = row % 2 == 1
        return view
    }

    private func tableCellView(_ tableView: NSTableView, column tableColumn: NSTableColumn?, row: Int) -> NSView? {
        if tableView === logTable {
            guard row >= 0, row < displayedReceipts.count, let column = tableColumn else { return nil }
            let receipt = displayedReceipts[row]
            // 같은 턴이 어디서 왔는지 코어가 적어 둔 한 줄을 그대로 띄운다.
            let tip = receipt.summary.isEmpty ? nil : receipt.summary
            switch column.identifier.rawValue {
            case "time":
                return reusedLabel(
                    in: tableView,
                    column: "log.time",
                    text: receipt.time,
                    font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
                    color: NSColor.secondaryLabelColor,
                    alignment: .left,
                    toolTip: tip
                )
            case "room":
                return reusedLabel(
                    in: tableView,
                    column: "log.room",
                    text: receipt.room,
                    font: NSFont.systemFont(ofSize: 12),
                    color: NSColor.labelColor,
                    alignment: .left,
                    toolTip: tip
                )
            case "outcome":
                return reusedLabel(
                    in: tableView,
                    column: "log.outcome",
                    text: receipt.outcomeText,
                    font: NSFont.systemFont(ofSize: 12, weight: receipt.attention ? .semibold : .regular),
                    color: logOutcomeColor(receipt),
                    alignment: .center,
                    toolTip: tip
                )
            case "reason":
                return reusedLabel(
                    in: tableView,
                    column: "log.reason",
                    text: receipt.reasonText,
                    font: NSFont.systemFont(ofSize: 12),
                    color: NSColor.labelColor,
                    alignment: .left,
                    toolTip: tip
                )
            case "retrieval":
                return reusedLabel(
                    in: tableView,
                    column: "log.retrieval",
                    text: receipt.retrievalText,
                    font: NSFont.systemFont(ofSize: 11),
                    color: NSColor.secondaryLabelColor,
                    alignment: .left,
                    toolTip: tip
                )
            default:
                return reusedLabel(
                    in: tableView,
                    column: "log.reply",
                    text: receipt.reply,
                    font: NSFont.systemFont(ofSize: 12),
                    color: receipt.reply.isEmpty ? NSColor.secondaryLabelColor : NSColor.labelColor,
                    alignment: .left,
                    toolTip: tip
                )
            }
        }
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
                color: NSColor.secondaryLabelColor,
                // 자릿수가 다른 숫자를 가운데에 두면 오른쪽 끝이 어긋나
                // 1429와 5를 눈으로 비교할 수 없다. 오른쪽 정렬하면 같은
                // 자리의 숫자끼리 세로로 맞는다 (2026-09-16, 6 Pro 지적).
                alignment: .right
            )
        case "live":
            return reusedLamp(
                in: tableView,
                column: "live",
                // 등록과 실행을 한 색으로 뭉치면, 목록에만 넣어 둔 방과 지금
                // 실제로 도는 방이 똑같이 초록으로 보인다. 등록 여부는 오른쪽
                // "추가됨" 칸이 이미 말하므로, 여기는 실제 동작만 보여 준다
                // (2026-09-16, 6 Pro 지적).
                on: chat.live,
                color: NSColor.systemGreen,
                interactive: true,
                toolTip: chat.live
                    ? "지금 동작 중입니다. 끄려면 답변·긱뉴스를 끄세요"
                    : (chat.catalog
                        ? "목록에만 있고 아직 돌지 않습니다. 누르면 목록에서 뺍니다"
                        : "답변 대상이 아닙니다. 누르면 켭니다")
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
        if table === logTable {
            let row = logTable?.selectedRow ?? -1
            if row >= 0, row < displayedReceipts.count {
                selectedReceiptKey = displayedReceipts[row].key
            }
            applyLogDetail()
            return
        }
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
        // 표 240 + 그래프 260 + 편집 270에 머리말과 사이 여백을 더하면 세로로
        // 974pt가 필요하다. 380pt까지 줄어들면 아래 편집 칸이 통째로 사라진다
        // (2026-09-16).
        let window = Chrome.operatorWindow(
            title: "지식 그래프 (대화 기억)",
            // 감사에서 스택이 974pt를 차지한다. 720으로 열면 표와 그래프가
            // 서로 높이를 빼앗아 둘 다 좁아진다 (2026-09-16).
            size: NSSize(width: 980, height: 1006),
            autosave: "AutoReplyVector",
            minimum: NSSize(width: 700, height: 700)
        )
        window.title = "지식 그래프 (대화 기억)"
        window.delegate = self
        let content = NSView()
        window.contentView = content

        // 창이 무엇을 하는지 한 줄. 개념 이름을 나열하던 문장은 아래 표에
        // 같은 이름이 이미 있고, 답변 보장은 여기서 약속할 일이 아니다
        // (2026-09-16).
        let hint = Chrome.hint("대화에서 정립된 개념과 관계를 모아 둡니다. 답변은 여기서 검색합니다.")
        vectorHint = hint

        let vectorEmpty = Chrome.statusLabel(size: 12, lines: 3)
        vectorEmptyLabel = vectorEmpty
        let vectorEmptyState = EmptyStateView(symbolName: "brain")
        vectorEmptyState.isHidden = true
        self.vectorEmptyState = vectorEmptyState

        let chatLabel = Chrome.label("보기", size: 11, color: .secondaryLabelColor, lines: 1)
        chatLabel.setContentHuggingPriority(.required, for: .horizontal)
        let source = NSPopUpButton(frame: .zero, pullsDown: false)
        source.translatesAutoresizingMaskIntoConstraints = false
        source.addItems(withTitles: vectorSourceTitles)
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
        // 여섯 컨트롤을 한 줄에 밀어 넣으면 좁은 창에서 라벨이 잘리고 칸이
        // 들쭉날쭉해진다. 고르는 줄과 찾는 줄로 나눈다 (2026-09-16).
        let pickRow = Chrome.hstack([chatLabel, source, topic, chat, Chrome.spacer()], spacing: 8)
        let findRow = Chrome.hstack([search, findButton], spacing: 8)
        let toolbarContent = Chrome.vstack([pickRow, findRow], spacing: 8)

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
        vectorPager = pager

        // 신경망 보기: 노드가 뉴런, 관계가 시냅스다. 표와 같은 데이터를 쓰므로
        // 두 보기가 서로 다른 말을 하지 않는다 (2026-09-16).
        let graph = KnowledgeGraphView(frame: .zero)
        graph.translatesAutoresizingMaskIntoConstraints = false
        // 처음에는 그래프를 그리지 않지만 숨기지는 않는다. 숨김은 부모
        // 스택이 혼자 관리하는데, 예전에는 여기서 자식만 숨겨 두어 스택을
        // 보여 준 뒤에도 그래프가 영영 나타나지 않았다 (2026-09-16).
        graph.onSelect = { [weak self] node in
            self?.selectVectorRow(forKnowledgeNode: node)
        }
        vectorGraphView = graph
        let graphHint = Chrome.hint(
            "크기는 중요도, 선 굵기는 관계 강도입니다. 흐린 뉴런은 아직 근거가 없습니다.",
            size: 11
        )
        vectorGraphHint = graphHint
        // 실패했을 때만 나타나는 재시도 버튼. 빈 상태에는 누를 것이 없으므로
        // 숨겨 두고, 오류·오래된 그림일 때만 보여 준다 (2026-09-17, 6 Pro 지적).
        let graphRetry = Chrome.roundedButton(
            "지식 그래프 다시 읽기",
            target: self,
            action: #selector(vectorGraphRetryClicked)
        )
        graphRetry.toolTip = "지식 그래프를 다시 읽습니다. 색인은 그대로 두고 화면만 새로 그립니다."
        graphRetry.isHidden = true
        vectorGraphRetryButton = graphRetry
        let graphHintRow = Chrome.hstack([graphHint, Chrome.spacer(), graphRetry], spacing: 8)
        let graphStack = Chrome.vstack([graphHintRow, graph], spacing: 6)
        vectorGraphStack = graphStack
        let graphHeight = graph.heightAnchor.constraint(greaterThanOrEqualToConstant: 260)
        graphHeight.isActive = true
        vectorGraphHeightConstraint = graphHeight

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
            // 글자 열의 머리글은 본문과 같이 왼쪽에 붙인다 (2026-09-16).
            let textColumn = spec.0 == "chat" || spec.0 == "user" || spec.0 == "preview" || spec.0 == "topics"
            Chrome.addColumn(
                table,
                id: spec.0,
                title: spec.1,
                width: spec.2,
                minWidth: 72,
                alignment: textColumn ? .left : .center
            )
        }
        vectorTable = table
        vectorTableScroll = scroll

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
        let metaTop = Chrome.hstack([userLabel, user, dateLabel, date, topicLabel, topicsField, Chrome.spacer()], spacing: 8)
        let metaBottom = Chrome.hstack([embedLabel, embed, Chrome.spacer()], spacing: 8)
        let metaContent = Chrome.vstack([metaTop, metaBottom], spacing: 8)

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
        // 지식 그래프 창의 편집 단추. 숨은 "기본값 복원"은 스택이 빼므로
        // 보이는 단추 셋이 창 폭을 나눠 갖는다 (2026-09-16).
        let formActions = Chrome.actionRow([addButton, saveButton, deleteButton, restoreButton], spacing: 8)

        // 중첩 테두리와 불필요한 중복 설명을 제거하고, 도구와 쪽수 넘김을 하나의 단일 카드로 정돈한다 (2026-09-17).
        let headerContent = Chrome.vstack([toolbarContent, pager], spacing: 10)
        let headerCard = Chrome.card(headerContent, padding: 12)

        // 이 카드가 지금 무엇을 보여 주는지 한 줄로 말한다. 그래프에서 뉴런을
        // 고르면 편집할 것이 아니라 근거를 보는 자리라, 제목이 같은 카드를
        // 두 가지 뜻으로 읽히게 두면 무엇을 하는 화면인지 알 수 없다
        // (2026-09-17).
        let editTitle = Chrome.label("고른 기억", size: 11, weight: .semibold, color: .secondaryLabelColor, lines: 1)
        vectorEditCardTitle = editTitle
        let editBody = Chrome.vstack([editTitle, metaContent, editor, formActions], spacing: 10)
        let editCard = Chrome.card(editBody, padding: 12)
        // 편집 폼은 고른 줄이 있을 때만 펼친다. 예전에는 아무것도 고르지
        // 않았는데도 이름·시각·주제·벡터·내용과 저장·삭제가 늘 자리를
        // 차지해, 창에서 가장 큰 덩어리가 "아직 아무것도 아닌 것"이었다
        // (2026-09-16, 6 Pro 지적).
        editCard.isHidden = true
        vectorEditCard = editCard

        // 빈 상태 판은 표와 같은 자리를 쓰되, 스택 안에서는 표 바로 앞에 둔다.
        // 둘 중 하나만 보이므로 화면에는 한 자리만 남는다 (2026-09-16).
        let stack = Chrome.vstack([headerCard, vectorEmpty, vectorEmptyState, scroll, graphStack, editCard], spacing: 10)
        vectorStack = stack
        Chrome.fill(stack, in: content)
        NSLayoutConstraint.activate([
            headerCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            headerContent.widthAnchor.constraint(equalTo: headerCard.widthAnchor, constant: -24),
            toolbarContent.widthAnchor.constraint(equalTo: headerContent.widthAnchor),
            pickRow.widthAnchor.constraint(equalTo: toolbarContent.widthAnchor),
            findRow.widthAnchor.constraint(equalTo: toolbarContent.widthAnchor),
            pager.widthAnchor.constraint(equalTo: headerContent.widthAnchor),
            vectorEmpty.widthAnchor.constraint(equalTo: stack.widthAnchor),
            vectorEmptyState.widthAnchor.constraint(equalTo: stack.widthAnchor),
            // 빈 상태 판은 표와 같은 높이를 차지한다. 표를 숨긴 자리가 그대로
            // 빈 띠로 남지 않게 하려면 높이가 같아야 한다 (2026-09-16).
            vectorEmptyState.heightAnchor.constraint(greaterThanOrEqualToConstant: 240),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            graphStack.widthAnchor.constraint(equalTo: stack.widthAnchor),
            graphHint.widthAnchor.constraint(equalTo: graphStack.widthAnchor),
            graph.widthAnchor.constraint(equalTo: graphStack.widthAnchor),
            editCard.widthAnchor.constraint(equalTo: stack.widthAnchor),
            editBody.widthAnchor.constraint(equalTo: editCard.widthAnchor, constant: -24),
            editTitle.widthAnchor.constraint(equalTo: editBody.widthAnchor),
            metaContent.widthAnchor.constraint(equalTo: editBody.widthAnchor),
            metaTop.widthAnchor.constraint(equalTo: metaContent.widthAnchor),
            metaBottom.widthAnchor.constraint(equalTo: metaContent.widthAnchor),
            editor.widthAnchor.constraint(equalTo: editBody.widthAnchor),
            formActions.widthAnchor.constraint(equalTo: editBody.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 240),
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
        applyVectorLayout()
    }

    func loadVectorReport(_ extra: [String]) -> VectorReport? {
        let timeout: TimeInterval = extra.contains("vector-list") ? (extra.contains("references") ? 45 : 20) : 8
        guard let data = runPython(extra, timeout: timeout) else { return nil }
        return try? JSONDecoder().decode(VectorReport.self, from: data)
    }

    func refreshVectorList() {
        ensureVectorWindow()
        updateVectorGraphVisibility()
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
            updateVectorEmptyState()
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
        updateVectorEmptyState()
    }

    /// 표가 비면 회색 띠 대신 이유를 적는다 (2026-09-16).
    func updateVectorEmptyState() {
        guard let label = vectorEmptyLabel else { return }
        label.stringValue = ""
        vectorEmptyState?.titleText = vectorSourceKind == "knowledge_graph"
            ? "아직 그릴 뉴런이 없습니다"
            : "이 조건에 보여 줄 기억이 없습니다"
        vectorEmptyState?.detailText = vectorSourceKind == "knowledge_graph"
            ? "대화가 쌓이면 개념이 뉴런으로, 관계가 시냅스로 이어집니다."
            : "위에서 다른 보기나 주제를 골라 보세요."
        applyVectorLayout()
    }

    /// 이 창에 무엇을 보여 줄지 한 곳에서 정한다.
    ///
    /// 예전에는 표·그래프·편집 폼이 한 화면에 겹쳐 있었다. 셋은 같은 자료를
    /// 보는 세 가지 방법이라, 다 펼치면 각자 좁아지고 무엇을 해야 하는지도
    /// 흐려진다. 지금은 보기 선택이 주 화면을 정하고, 편집 폼은 고른 줄이
    /// 있을 때만 따라 나온다 (2026-09-16, 6 Pro 지적).
    func applyVectorLayout() {
        let graphMode = currentVectorSource() == "knowledge_graph"
        let graphHasNodes = !(vectorGraphView?.nodes.isEmpty ?? true)
        // 그래프 보기에서 뉴런이 없으면 표의 빈 상태 판이 대신 이유를 적는다.
        let showsGraph = graphMode && graphHasNodes
        // 그래프를 못 읽었으면 뉴런이 하나도 없어도 안내 줄과 재시도 버튼은
        // 보여야 한다. 스택을 통째로 숨기면 그 안의 재시도 버튼까지 사라져,
        // 처음 실패한 사람은 다시 시도할 방법이 없다 (2026-09-17, 6 Pro 지적).
        let graphFailed = graphMode
            && (vectorGraphPhase == .error || vectorGraphPhase == .stale)
        let showsGraphPanel = showsGraph || graphFailed
        let showsTable = !showsGraphPanel

        vectorGraphStack?.isHidden = !showsGraphPanel
        // 캔버스는 그릴 뉴런이 있을 때만 펼친다. 없으면 안내 줄만 남기고,
        // 최소 높이 제약도 함께 꺼서 빈 판이 자리를 차지하지 않게 한다.
        vectorGraphView?.isHidden = !showsGraph
        vectorGraphHeightConstraint?.isActive = showsGraph
        vectorTableScroll?.isHidden = !showsTable || displayedVectors.isEmpty
        vectorEmptyState?.isHidden = !showsTable || !displayedVectors.isEmpty
        // 쪽수 넘김은 목록에만 뜻이 있다. 그래프는 한 번에 다 그리므로
        // "기록 없음" 옆에 이전·다음이 놓이면 무엇을 넘기는지 알 수 없다
        // (2026-09-16, 6 Pro 지적).
        vectorPager?.isHidden = !showsTable

        let selectedRow = vectorTable?.selectedRow ?? -1
        let hasSelection = showsTable && selectedRow >= 0 && selectedRow < displayedVectors.count
        // 그래프에서도 고른 뉴런의 근거를 펼친다.
        //
        // 예전에는 표에서 고른 줄이 있을 때만 편집 카드를 열었다. 그래서
        // 그래프에서 뉴런을 눌러도 아무 일도 일어나지 않아, 눌러 보라고
        // 적어 둔 안내와 화면이 서로 다른 말을 했다 (2026-09-17).
        let hasGraphSelection = showsGraph && vectorGraphView?.selectedNodeId != nil
        vectorEditCard?.isHidden = !(hasSelection || hasGraphSelection)
        // 그래프 보기에서 이 카드는 "편집"이 아니라 "근거"다. 무엇을 보는
        // 중인지에 따라 제목이 달라야 같은 카드가 두 가지로 읽히지 않는다.
        vectorEditCardTitle?.stringValue = hasGraphSelection
            ? "고른 뉴런의 근거"
            : "고른 기억"
        fitVectorWindow()
    }

    /// 이 창은 무엇을 보여 주느냐에 따라 필요한 높이가 크게 달라진다.
    ///
    /// 표·그래프·편집 폼이 모두 펼쳐지던 시절에 맞춰 둔 1006pt를 그대로
    /// 두면, 하나만 보여 줄 때는 남는 높이가 그래프 캔버스로 흘러 들어가
    /// 뉴런이 위아래로 흩어지고 빈 띠가 생긴다. 내용에 맞춰 줄이되, 사용자가
    /// 직접 늘린 창은 건드리지 않는다 (2026-09-16).
    func fitVectorWindow() {
        guard !vectorFitting,
              !vectorWindowUserResized,
              let window = vectorWindow,
              let stack = vectorStack,
              let content = window.contentView else { return }
        vectorFitting = true
        defer { vectorFitting = false }
        // 오래된 autosave 값이 현재 최소 크기보다 작을 수도 있다. 그 상태로
        // 높이만 맞추면 편집 폼의 고정 폭 필드가 눌려 이름·시각·주제 라벨이
        // 사라진다. 자동 맞춤을 하는 동안에는 선언한 최소 프레임을 지킨다.
        if window.frame.width < window.minSize.width {
            var frame = window.frame
            frame.size.width = window.minSize.width
            window.setFrame(frame, display: false, animate: false)
            content.layoutSubtreeIfNeeded()
        }
        let minimumContentHeight = window.contentRect(
            forFrameRect: NSRect(origin: .zero, size: window.minSize)
        ).height
        // 배치가 한 번에 수렴하지 않는다. 줄인 뒤 다시 재서 맞춘다.
        for _ in 0..<3 {
            content.layoutSubtreeIfNeeded()
            stack.layoutSubtreeIfNeeded()
            let needed = stack.fittingSize.height
            guard needed > 1 else { return }
            // 그래프 보기에서는 캔버스가 정사각형에 가까워야 뉴런이 골고루
            // 퍼진다. 창 폭에서 여백을 뺀 만큼을 캔버스 높이로 잡는다.
            //
            // 다만 실제로 그릴 뉴런이 있을 때만 그렇다. 보기만 지식
            // 그래프이고 그릴 것이 없으면, 이 높이가 그대로 남아 빈 상태
            // 판이 704pt짜리 빈 카드가 되었다 (2026-09-16).
            let showsGraph = currentVectorSource() == "knowledge_graph"
                && !(vectorGraphView?.nodes.isEmpty ?? true)
            var compactNeeded = needed
            if showsGraph, let graph = vectorGraphView {
                // 그래프는 남는 높이를 전부 먹는 뷰라, 복원된 큰 프레임에서
                // fittingSize를 재면 그 높이가 다시 목표값이 된다. 그래프의
                // 제약상 최소 높이인 260pt를 넘는 부분만 빼고, 아래에서 창의
                // 실제 최소 높이를 다시 보장한다. 편집 카드가 열려 있으면 그
                // 카드의 fittingSize는 그대로 남으므로 함께 들어간다.
                compactNeeded -= max(0, graph.bounds.height - 260)
            } else if let scroll = vectorTableScroll, !scroll.isHidden {
                compactNeeded -= max(0, scroll.bounds.height - 240)
            } else if let empty = vectorEmptyState, !empty.isHidden {
                compactNeeded -= max(0, empty.bounds.height - 240)
            }
            let desired = min(
                max(compactNeeded + 32, minimumContentHeight),
                Self.vectorWindowHeightLimit
            )
            let current = content.bounds.height
            guard abs(current - desired) > 12 else { return }
            var frame = window.frame
            let delta = current - desired
            if delta > 0 {
                // 줄일 때는 위쪽 모서리를 고정한다. 아래에서 줄이면 창이 화면
                // 밖으로 밀린다.
                frame.size.height -= delta
                frame.origin.y += delta
            } else {
                // 늘릴 때는 화면 위쪽을 넘지 않게 막는다.
                let grown = min(-delta, frame.origin.y)
                guard grown > 0 else { return }
                frame.size.height += grown
                frame.origin.y -= grown
            }
            window.setFrame(frame, display: false, animate: false)
            content.layoutSubtreeIfNeeded()
        }
    }

    /// 그래프 보기는 표와 같은 새로고침에서 함께 갱신된다. 표를 다시 그릴 때
    /// 매번 그래프를 새로 읽으면 2초마다 프로세스가 하나 더 뜨므로, 지식
    /// 그래프를 보고 있을 때만 읽는다 (2026-09-16).
    func refreshVectorGraphIfVisible() {
        updateVectorGraphVisibility()
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

    /// 신경망 보기는 지식 그래프를 고를 때만 켠다. 다른 목록은 노드·관계가
    /// 아니라서 같은 그림으로 그리면 없는 관계를 그리게 된다.
    func updateVectorGraphVisibility() {
        let shows = currentVectorSource() == "knowledge_graph"
        guard shows else {
            applyVectorLayout()
            return
        }
        applyVectorLayout()
        // 창은 2초마다 새로 그려진다. 그때마다 그래프를 다시 읽으면 파이썬
        // 프로세스가 계속 뜨고 힘 배치도 다시 흔들린다. 목록을 고른 직후와
        // 30초가 지난 뒤에만 다시 읽는다 (2026-09-16).
        let now = Date()
        let stale = now.timeIntervalSince(lastVectorGraphReadAt) >= 30
        if lastVectorGraphSource != vectorSourceKind || stale {
            refreshKnowledgeGraph()
        }
    }

    /// 뉴런이 하나도 없으면 신경망 보기를 감춘다.
    ///
    /// 보기를 켜 두고 그래프가 비어 있으면 260pt짜리 빈 캔버스만 남아 창
    /// 아래가 통째로 비어 보인다. 표와 편집기는 그대로 두고 그래프만
    /// 감추며, 감출 때는 힌트 줄에 왜 안 보이는지 적는다 (2026-09-16).
    func hideEmptyKnowledgeGraph() {
        guard let graph = vectorGraphView else { return }
        // 실패 뒤에 이 함수가 불리면 방금 쓴 오류 안내를 "뉴런이 없습니다"로
        // 덮어써 조회 실패가 데이터 없음으로 보인다. 오류·읽는 중에는
        // 문구를 건드리지 않는다 (2026-09-17, 6 Pro 지적).
        if graph.nodes.isEmpty, currentVectorSource() == "knowledge_graph" {
            switch vectorGraphPhase {
            case .error, .loading:
                break
            case .stale:
                vectorGraphHint?.stringValue =
                    "지식 그래프를 다시 읽지 못했습니다. 화면은 마지막으로 읽은 그림입니다."
            default:
                vectorGraphPhase = .empty
                vectorGraphHint?.stringValue =
                    "아직 그릴 뉴런이 없습니다. 메시지를 읽어 지식 그래프를 채우면 여기에 신경망으로 나타납니다."
            }
        }
        applyVectorGraphRetryVisibility()
        applyVectorLayout()
    }

    /// 실패했을 때만 재시도 버튼을 보여 준다. 빈 상태에는 누를 것이 없다.
    func applyVectorGraphRetryVisibility() {
        guard let button = vectorGraphRetryButton else { return }
        let show = currentVectorSource() == "knowledge_graph"
            && (vectorGraphPhase == .error || vectorGraphPhase == .stale)
        button.isHidden = !show
    }

    /// 실패 안내와 재시도 경로를 함께 세운다.
    func presentVectorGraphError(hasPreviousPicture: Bool) {
        vectorGraphPhase = hasPreviousPicture ? .stale : .error
        if hasPreviousPicture {
            vectorGraphHint?.stringValue =
                "지식 그래프를 다시 읽지 못했습니다. 화면은 마지막으로 읽은 그림입니다."
        } else {
            vectorGraphHint?.stringValue =
                "지식 그래프를 읽지 못했습니다. 색인이 아직 없는 것과는 다릅니다. 다시 시도해 주세요."
        }
        applyVectorGraphRetryVisibility()
        // 재시도 버튼의 hidden 만 바꾸면, 스택이 숨은 상태에서는 아무것도
        // 보이지 않는다. 화면 구성을 함께 다시 계산해 안내 줄을 펼친다
        // (2026-09-17, 6 Pro 지적).
        applyVectorLayout()
    }

    /// 그래프 전체를 한 번에 읽는다. 목록과 달리 쪽 나눔이 없어야 힘 배치가
    /// 안정적이고, 읽는 동안에는 이전 그림을 그대로 둔다.
    func refreshKnowledgeGraph() {
        // 이 가드는 보기 선택만 본다. 스택의 숨김 여부를 보면, 뉴런이 하나도
        // 없어 그래프를 감춘 상태에서 다시 읽으려는 순간 영영 돌아오지
        // 못한다 (2026-09-16).
        guard currentVectorSource() == "knowledge_graph" else { return }
        lastVectorGraphReadAt = Date()
        lastVectorGraphSource = vectorSourceKind
        vectorGraphPhase = .loading
        applyVectorGraphRetryVisibility()
        vectorGraphToken += 1
        let token = vectorGraphToken
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let data = self.runPython(["--action", "knowledge-graph"], timeout: 25)
            let report = data.flatMap { try? JSONDecoder().decode(KnowledgeGraphReport.self, from: $0) }
            DispatchQueue.main.async {
                guard token == self.vectorGraphToken else { return }
                guard let report, report.ok else {
                    // 읽지 못했다고 이전 그림을 지우지 않는다.
                    //
                    // 지우면 한 번의 시간 초과가 화면을 "뉴런이 하나도 없는
                    // 그래프"로 바꿔, 색인이 멀쩡한데도 사용자에게는 그래프가
                    // 사라진 것으로 보인다. 그림은 그대로 두고 그 위에
                    // 마지막으로 읽은 시각을 말한다 (2026-09-17).
                    let hasPicture = !(self.vectorGraphView?.nodes.isEmpty ?? true)
                    self.presentVectorGraphError(hasPreviousPicture: hasPicture)
                    return
                }
                self.vectorGraphView?.applySnapshot(nodes: report.nodes, edges: report.edges)
                let total = report.node_count
                let grounded = report.grounded_nodes
                // 뉴런이 없으면 빈 캔버스를 남기지 않는다.
                if report.nodes.isEmpty {
                    self.vectorGraphPhase = .empty
                    self.hideEmptyKnowledgeGraph()
                    return
                }
                self.vectorGraphPhase = .ready
                self.applyVectorGraphRetryVisibility()
                self.vectorGraphTotal = total
                self.vectorGraphGrounded = grounded
                self.vectorGraphIndexedAt = report.indexed_at ?? 0
                self.vectorGraphIsStale = report.stale ?? false
                self.applyVectorGraphHint(total: total, grounded: grounded)
            }
        }
    }

    /// 지금 그래프가 무엇을 보여 주는지 한 줄로 말한다.
    func applyVectorGraphHint(total: Int, grounded: Int) {
        guard let graph = vectorGraphView else { return }
        if let label = graph.focusedNodeLabel, graph.isFocused {
            vectorGraphHint?.stringValue =
                "\(label)을(를) 중심으로 펼쳤습니다. 이어진 뉴런만 남았습니다. "
                + "빈 곳을 누르면 전체 그림으로 돌아갑니다."
            return
        }
        // 색인이 언제 기준인지 붙인다. 카카오톡 DB는 몇 분마다 다시 읽히는데,
        // 그 사실이 화면에 없으면 방금 한 대화가 왜 그래프에 없는지 알 길이
        // 없다 (2026-09-17, 사용자 지시).
        var head = "뉴런 \(total)개 중 \(grounded)개가 원문으로 확인되었습니다."
        if let stamp = vectorGraphIndexText {
            head += " 색인 \(stamp)."
        }
        vectorGraphHint?.stringValue =
            head + " 크기는 중요도, 선 굵기는 관계 강도이고, 밝은 뉴런을 누르면 근거가 나옵니다."
    }

    /// 색인 시각을 사람이 읽는 말로. 아직 색인 전이면 그렇게 말한다.
    var vectorGraphIndexText: String? {
        guard vectorGraphIndexedAt > 0 else { return nil }
        let seconds = Int(Date().timeIntervalSince1970) - vectorGraphIndexedAt
        if vectorGraphIsStale {
            // 재색인이 뒤에서 도는 중이다. 지금 보이는 그림은 조금 오래됐다.
            return "갱신 중 · 지금 화면은 \(vectorGraphAgeText(seconds))"
        }
        if seconds < 60 { return "방금 전" }
        return vectorGraphAgeText(seconds)
    }

    private func vectorGraphAgeText(_ seconds: Int) -> String {
        if seconds < 3600 { return "\(max(seconds, 1) / 60)분 전" }
        if seconds < 86_400 { return "\(seconds / 3600)시간 전" }
        return "\(seconds / 86_400)일 전"
    }

    /// 그래프에서 고른 뉴런과 같은 행을 표에서도 고른다. 두 보기가 같은
    /// 데이터를 가리키므로, 표에 없으면 선택만 바꾸지 않고 그대로 둔다.
    func selectVectorRow(forKnowledgeNode node: KnowledgeNode?) {
        guard let node else {
            // 빈 공간 클릭으로 선택이 해제된 경우 근거 카드를 즉시 닫고,
            // 힌트 줄도 전체 그림 기준으로 되돌린다 (2026-09-17, 6 Pro 지적).
            applyVectorGraphHint(total: vectorGraphTotal, grounded: vectorGraphGrounded)
            applyVectorLayout()
            return
        }
        if let index = displayedVectors.firstIndex(where: { $0.row_key == node.id }) {
            vectorTable?.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false)
            vectorTable?.scrollRowToVisible(index)
        }
        // 표에 있든 없든 선택된 노드의 근거 카드를 채우고 즉시 펼친다 (2026-09-17, 6 Pro 지적).
        vectorUserField?.stringValue = node.label
        vectorTopicsField?.stringValue = node.category
        let evidence = node.evidence
        var lines = [node.description, ""]
        if evidence.grounded {
            lines.append("근거: 원문 메시지 \(evidence.source_event_ids.count)건 (방 \(evidence.chat_id))")
            if let when = evidence.confirmed_at { lines.append("확인 시각: \(when)") }
        } else {
            lines.append("근거: 아직 원문 메시지에서 확인되지 않은 초기 노드")
        }
        if evidence.retracted { lines.append("상태: 철회됨") }
        lines.append("")
        lines.append(contentsOf: node.facts.map { "• \($0)" })
        vectorMessageView?.string = lines.joined(separator: "\n")
        vectorEmbeddingField?.stringValue = "지식 그래프 노드 \(node.id)"
        // 확대가 끝난 뒤 힌트 줄이 "무엇을 보고 있는지"를 말한다.
        // 선택 직후에는 아직 확대 중이므로 한 박자 뒤에 다시 쓴다.
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.applyVectorGraphHint(
                total: self.vectorGraphTotal,
                grounded: self.vectorGraphGrounded
            )
        }
        applyVectorLayout()
    }

    /// 사용자가 그래프를 다시 읽으라고 했을 때. 캐시 시각을 지워 다음 읽기가
    /// 실제로 파이썬을 부르게 한다 (2026-09-17).
    @objc func vectorGraphRetryClicked() {
        lastVectorGraphReadAt = Date.distantPast
        lastVectorGraphSource = ""
        vectorGraphPhase = .loading
        vectorGraphHint?.stringValue = "지식 그래프를 다시 읽는 중…"
        applyVectorGraphRetryVisibility()
        refreshKnowledgeGraph()
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

    func applyCatalogSnapshot(_ data: Data?, modelToken: Int) {
        if let data, let model = try? JSONDecoder().decode(MenubarModel.self, from: data) {
            apply(model, modelToken: modelToken)
            return
        }
        refresh()
    }

    func removeRoomFromCatalog(_ chat: AvailableChat) {
        rememberRoomsSelection()
        applyOptimisticChat(chat.updating(catalog: false, autoReply: false, geeknews: false))
        // 채팅방 설정 작업도 시작 시점의 변경 세대를 캡처해, 늦게 도착한 스냅샷이
        // 이미 완료된 모델 선택을 덮지 않게 한다.
        let fetchToken = modelChangeToken
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--catalog-delete", String(chat.chat_id)], timeout: 12)
            DispatchQueue.main.async { self?.applyCatalogSnapshot(data, modelToken: fetchToken) }
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
        // The host needs the room title to attest an unnamed group room with a
        // bind: selector; without it a host restart drops the room.
        let payload: [String: Any] = [
            "chat_id": chat.chat_id,
            "auto_reply": autoReply,
            "geeknews": geeknews,
            "title": chat.title,
        ]
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: data, encoding: .utf8) else { return }
        rememberRoomsSelection()
        applyOptimisticChat(chat.updating(catalog: true, autoReply: autoReply, geeknews: geeknews))
        // 설정 변경 응답도 시작 시점의 세대를 캡처해 전달한다.
        let fetchToken = modelChangeToken
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = self?.runPython(["--catalog-upsert", json], timeout: 12)
            DispatchQueue.main.async { self?.applyCatalogSnapshot(data, modelToken: fetchToken) }
        }
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
            reply_model_providers: nil,
            reply_model_fallbacks: nil,
            reply_receipts: nil,
            ondevice_hardware: nil
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
    /// 편집 카드의 제목. 표에서 고른 기억인지 그래프에서 고른 뉴런인지에
    /// 따라 달라진다 (2026-09-17).
    var vectorEditCardTitle: NSTextField?
