#!/usr/bin/env python3
"""Fail when a menubar window renders with dead space or hidden content.

``audit-menubar-layout.py`` reports; this decides. The menu extra cannot be
looked at from CI, so the measurement is the gate: a card that collapsed to
zero height, a band of nothing across a window, a sibling drawn on top of
another sibling, or a label cut off outside a table cell all fail here
(2026-09-16).

Usage: python3 scripts/check-menubar-layout.py /tmp/layout-audit
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_audit():
    path = Path(__file__).resolve().parent / "audit-menubar-layout.py"
    spec = importlib.util.spec_from_file_location("audit_menubar_layout", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# Every window the menu extra can open, plus the two states of the dropdown
# panel. The audit dumps one PNG per entry (2026-09-16).
EXPECTED_WINDOWS = (
    "jobs",
    "jobs-dark",
    "log",
    "log-dark",
    "menu-panel",
    "menu-panel-dark",
    "model",
    "model-dark",
    "rooms",
    "rooms-dark",
    "settings",
    "settings-dark",
    "vector",
    "vector-dark",
)

# 창을 만들 때 크기뿐 아니라 운영자가 줄일 수 있는 가장 작은 크기에서도
# 잰다. 마지막 열이 사라지거나 표가 눌리는 결함은 줄였을 때만 드러나서,
# 큰 크기만 보던 검사는 통과시키고 있었다 (2026-09-16).
RESIZED_SUFFIX = "-min"


def expected_windows() -> tuple[str, ...]:
    return EXPECTED_WINDOWS


def inside_scroll_view(row: dict) -> bool:
    """Whether the row lives inside a scroll view.

    Content there is allowed to be larger than its container: scrolling to it
    is the point. The path carries class names, so a subclass with a different
    name would silently stop matching and the check would start reporting
    scrollable content as clipped (2026-09-16).
    """
    if row["kind"].endswith("ScrollView"):
        return True
    return any(
        part.split("#", 1)[0].endswith("ScrollView")
        for part in row["path"].split("/")
    )


def uneven_gaps(rows: list[dict]) -> dict[str, list[float]]:
    """Sibling gaps inside one vertical stack that are not the same value.

    A stack spaces its children evenly by construction, so only the frames
    that were placed by hand can disagree. The menu panel used to mix 6, 8 and
    10 points, which reads as sloppy even though every piece fits
    (2026-09-16).
    """
    audit = load_audit()
    stacks = {
        row["path"]: row
        for row in rows
        if row.get("orientation") == "v" and not row["hidden"] and row.get("spacing")
    }
    complaints: dict[str, list[float]] = {}
    for path, stack in stacks.items():
        if "NSStackView" not in stack["kind"]:
            continue
        children = [
            row
            for row in rows
            if not row["hidden"] and row["path"].rsplit("/", 1)[0] == path
        ]
        if len(children) < 3:
            continue
        children.sort(key=lambda row: row["winTop"])
        gaps = [
            round(children[index + 1]["winTop"] - (children[index]["winTop"] + children[index]["h"]), 1)
            for index in range(len(children) - 1)
        ]
        distinct = {gap for gap in gaps if gap > 0}
        if len(distinct) > 2:
            complaints.setdefault(stack["window"], sorted(distinct))
    return complaints


def menu_panel_surface_violations(rows: list[dict]) -> dict[str, list[str]]:
    """Require the menu extra to contain only the Jarvis core and top-right gear."""

    complaints: dict[str, list[str]] = {}
    for window in ("menu-panel", "menu-panel-dark"):
        direct = [
            row
            for row in rows
            if row["window"] == window
            and not row["hidden"]
            and row["path"].rsplit("/", 1)[0] == window
        ]
        issues: list[str] = []
        cores = [row for row in direct if row["kind"].endswith("JarvisCoreView")]
        buttons = [row for row in direct if row["kind"].endswith("Button")]
        gears = [row for row in buttons if row.get("identifier") == "gear"]

        if len(cores) != 1:
            issues.append(f"JarvisCoreView={len(cores)}")
        if len(gears) != 1:
            issues.append(f"gear={len(gears)}")
        if len(buttons) != 1:
            issues.append(f"buttons={len(buttons)}")

        forbidden_titles = {"즉시 답장 보내기", "긱뉴스 바로 전송"}
        forbidden_markers = ("room-popup", "health-row", "tile-", "settings-instant-")
        for row in direct:
            if row.get("text") in forbidden_titles:
                issues.append(f"action:{row.get('text')}")
            if any(marker in row["path"] for marker in forbidden_markers):
                issues.append(f"legacy:{row['path']}")

        if issues:
            complaints[window] = issues
    return complaints


def unified_settings_identifier_violations(rows: list[dict]) -> dict[str, list[str]]:
    """Reject instant-action identifiers in the unified settings dumps."""

    complaints: dict[str, list[str]] = {}
    for window in ("settings", "settings-dark"):
        issues = [
            str(row.get("identifier"))
            for row in rows
            if row["window"] == window
            and str(row.get("identifier") or "").startswith("settings-instant-")
        ]
        if issues:
            complaints[window] = issues
    return complaints


def menu_panel_source_violations() -> list[str]:
    """Freeze the source topology so custom-drawn legacy rows cannot hide from audit."""

    source = (
        Path(__file__).resolve().parents[1]
        / "macos"
        / "AutoReplyMenu"
        / "main.swift"
    ).read_text(encoding="utf-8")
    panel = source[
        source.index("final class MenuPanelView"):
        source.index("final class CenteredLabelCell")
    ]
    core = source[
        source.index("final class JarvisCoreView"):
        source.index("final class MenuPanelView")
    ]
    required = (
        "let coreView = JarvisCoreView(frame: .zero)",
        'NSUserInterfaceItemIdentifier("gear")',
        "static let panelWidth: CGFloat = 276",
        "static let panelBaseHeight: CGFloat = 260",
        "static let coreSize: CGFloat = 236",
        "static let gearSize: CGFloat = 28",
        "addSubview(coreView)",
        "addSubview(gearButton)",
        "x: (width - Self.coreSize) / 2",
        "x: width - Self.panelInset - Self.gearSize",
        "y: Self.panelInset",
    )
    forbidden = (
        "tileButtons",
        "tileClicked",
        '"room-popup"',
        "roomGridExtra",
        "layoutRoomGrid",
        "roomsExpanded",
        "drawStatusRow",
        "statusRowTop",
        "LampCell",
        "health-row",
        "즉시 답장 보내기",
        "긱뉴스 바로 전송",
    )
    issues = [f"missing:{token}" for token in required if token not in panel]
    issues.extend(f"legacy:{token}" for token in forbidden if token in panel)
    core_required = (
        "private static let gold = NSColor(",
        "private static let amber = NSColor(",
        "let speed = Self.idleSpeed + Self.activeSpeed * currentActivity",
        "spawnPulse(strength: max(clamped, 0.45))",
        "let boost = 1.0 + currentActivity * 1.7",
        "0.22 * currentActivity * near",
    )
    core_forbidden = (
        "drawBackdrop(",
        "drawLevelRing(",
        "Palette.level(level)",
        "NSGradient(",
    )
    issues.extend(f"core-missing:{token}" for token in core_required if token not in core)
    issues.extend(f"core-style:{token}" for token in core_forbidden if token in core)
    if "self?.showUnifiedSettingsWindow()" not in source:
        issues.append("gear-does-not-open-settings")
    if "func presentGearMenu()" in source:
        issues.append("legacy-gear-menu")
    return issues


def intentional_menu_panel_overlap(item: dict) -> bool:
    """The top-right gear deliberately sits over the Jarvis core's square."""

    if item.get("window") not in {"menu-panel", "menu-panel-dark"}:
        return False
    kinds = {
        str(item.get("a", "")).split(" ", 1)[0],
        str(item.get("b", "")).split(" ", 1)[0],
    }
    return any(kind.endswith("JarvisCoreView") for kind in kinds) and any(
        kind.endswith("Button") for kind in kinds
    )


def vertical_dead_bands(rows: list[dict], limit: float = 40.0) -> list[dict]:
    """Large empty vertical bands between children of one vertical stack.

    A table scroll view can be much taller than the rows it actually paints.
    Comparing only the scroll view frame to the next arranged view therefore
    misses the receipts-window regression: the stack spacing is still 10pt,
    but the last row can end hundreds of points before the following card.
    For table scroll views use the last visible row as the painted bottom.
    Stacks that themselves live inside a scroll view are skipped because empty
    space there can be reached by scrolling and is not a window-layout defect.
    """
    audit = load_audit()
    by_path = {row["path"]: row for row in rows}
    stacks = [
        row
        for row in rows
        if row.get("orientation") == "v"
        and "NSStackView" in row["kind"]
        and not row["hidden"]
        and not audit.hidden_ancestor(row, by_path)
        and not inside_scroll_view(row)
    ]
    found: list[dict] = []
    for stack in stacks:
        path = stack["path"]
        children = [
            row
            for row in rows
            if row["path"].rsplit("/", 1)[0] == path
            and not row["hidden"]
            and not audit.hidden_ancestor(row, by_path)
        ]
        children.sort(key=lambda row: float(row.get("winTop") or 0.0))
        for before, after in zip(children, children[1:]):
            before_top = float(before.get("winTop") or 0.0)
            before_bottom = before_top + float(before.get("h") or 0.0)
            if before["kind"].endswith("ScrollView"):
                prefix = before["path"] + "/"
                scroll_bottom = before_bottom
                painted_rows = [
                    row
                    for row in rows
                    if row["path"].startswith(prefix)
                    and row["kind"].endswith("RowView")
                    and not row["hidden"]
                    and not audit.hidden_ancestor(row, by_path)
                    and float(row.get("winTop") or 0.0) < scroll_bottom
                ]
                if painted_rows:
                    before_bottom = min(
                        scroll_bottom,
                        max(
                            float(row.get("winTop") or 0.0)
                            + float(row.get("h") or 0.0)
                            for row in painted_rows
                        ),
                    )
            gap = float(after.get("winTop") or 0.0) - before_bottom
            if gap > limit:
                found.append(
                    {
                        "window": stack["window"],
                        "before": before["path"].split("/", 1)[-1][:60],
                        "after": after["path"].split("/", 1)[-1][:60],
                        "gap": round(gap, 1),
                    }
                )
    return found


def clipped_at_minimum(rows: list[dict]) -> list[dict]:
    """Content that runs out of window when the window is at its smallest.

    The audit renders every window at its build size and again at its minimum.
    At the build size a wide window can absorb a long label; at the minimum it
    cannot, and the part that does not fit is simply not on screen. Only the
    minimum-size rows are judged, and rows inside a scroll view are skipped
    because scrolling to them is the point of the scroll view (2026-09-16).
    """
    found: dict[str, dict] = {}
    audit = load_audit()
    by_path = {row["path"]: row for row in rows}
    for row in rows:
        if row["hidden"] or not row["window"].endswith(RESIZED_SUFFIX):
            continue
        # 숨은 판 안쪽은 화면에 없으므로 잘림도 넘침도 아니다. AppKit은 부모를
        # 숨겨도 자식의 hidden 표시를 그대로 두기 때문에, 여기서 조상을 직접
        # 확인하지 않으면 보이지도 않는 빈 상태 카드가 결함으로 보고된다
        # (2026-09-17).
        if audit.hidden_ancestor(row, by_path):
            continue
        if inside_scroll_view(row):
            continue
        if "NSScroller" in row["kind"] or "NSTableHeaderView" in row["kind"]:
            continue
        # 검색 칸 안의 지우기 단추는 AppKit이 그린다. 그 단추의 이름은
        # "search"라, 좁은 창에서 늘 50pt쯤 잘린 것처럼 보인다. 우리가 배치한
        # 것이 아니므로 넘긴다 (2026-09-16).
        if "SearchField" in row["path"] or "SearchField" in row["kind"]:
            continue
        why = ""
        amount = 0.0
        if row.get("overRight", 0) > 2:
            why, amount = "오른쪽으로", float(row["overRight"])
        elif row.get("overBottom", 0) > 2:
            why, amount = "아래로", float(row["overBottom"])
        elif row.get("underLeft", 0) > 2:
            why, amount = "왼쪽으로", float(row["underLeft"])
        elif row.get("underTop", 0) > 2:
            why, amount = "위로", float(row["underTop"])
        elif row.get("clipped", 0) > 2:
            why, amount = "글자가", float(row["clipped"])
        if not why:
            continue
        key = f"{row['window']}:{row['path']}"
        if amount <= found.get(key, {}).get("amount", 0):
            continue
        found[key] = {
            "window": row["window"],
            "path": row["path"].split("/", 1)[-1][:60],
            "why": why,
            "amount": amount,
        }
    return list(found.values())


def unreachable_columns(rows: list[dict]) -> list[dict]:
    """Tables whose columns cannot all fit in the width they were given.

    A table clips its text on purpose and the full value lives in the tooltip,
    so ordinary truncation is not a defect. This is different: when the sum of
    the columns' minimum widths exceeds the clip view, the last column is
    pushed out of the table entirely, and with no horizontal scroller there is
    no way to scroll to it. The receipts window shipped that way - its six
    columns need about 624pt and the window's own minimum width left about
    528pt, so the 답변 column was unreachable (2026-09-16).
    """
    found: dict[str, dict] = {}
    by_path = {row["path"]: row for row in rows}
    for row in rows:
        if row["hidden"] or row["kind"] != "NSTableView":
            continue
        path = row["path"]
        # 표는 스크롤 뷰 안에 산다. 클립 뷰는 표의 자식이 아니라 부모라,
        # 자식만 훑던 예전 검사는 클립 뷰를 한 번도 찾지 못하고 늘 통과했다
        # (2026-09-16).
        clip = by_path.get(path.rsplit("/", 1)[0])
        if clip is None or clip["kind"] != "NSClipView" or clip["hidden"]:
            continue
        # 가로 스크롤 여부는 스크롤 뷰가 직접 알려 준다. 스크롤러 뷰의
        # 프레임 모양으로 추측하면 감출 때(autohidesScrollers) 크기가 0이
        # 되어 방향을 알 수 없다 (2026-09-16).
        scroll = by_path.get(clip["path"].rsplit("/", 1)[0])
        if scroll is not None and scroll.get("hScroller"):
            continue
        if clip is None:
            continue
        columns = row.get("columns") or []
        if not columns:
            continue
        needed = sum(float(column.get("minWidth") or 0.0) for column in columns)
        needed += float(row.get("intercellSpacing") or 0.0) * max(len(columns) - 1, 0)
        available = float(clip["w"])
        if needed > available + 0.5:
            found[row["window"]] = {
                "window": row["window"],
                "table": path.split("/", 1)[-1],
                "needed": needed,
                "available": available,
            }
    return list(found.values())


def tables_past_their_clip(rows: list[dict]) -> list[dict]:
    """Tables that are laid out wider than the view they are clipped to.

    A table is drawn inside a clip view; whatever sticks out is not on screen
    and there is no way to scroll to it. The receipts window shipped that way:
    its table stayed 976pt wide while the clip view shrank to 951pt, so the
    right edge of the 답변 column was cut off. The cause was that nothing made
    the table follow its clip view, which no amount of column maths would
    reveal - the frames themselves have to be compared (2026-09-16).

    A table being wider than its clip view is not by itself a defect: the table
    keeps a margin at each side that the clip view trims. What matters is
    whether the last column fits, so that is what gets measured when the audit
    reports where the columns end. Older audits without that field fall back to
    the frame comparison.
    """
    found: list[dict] = []
    by_path = {row["path"]: row for row in rows}
    for row in rows:
        if row["hidden"] or row["kind"] != "NSTableView":
            continue
        clip = by_path.get(row["path"].rsplit("/", 1)[0])
        if clip is None or clip["kind"] != "NSClipView":
            continue
        if "columnsRight" in row:
            # 열이 실제로 끝나는 자리. 표가 스스로 넓어진 여백은 빼고 본다.
            table_right = float(row.get("winLeft") or 0.0) + float(row["columnsRight"])
        else:
            table_right = float(row.get("winLeft") or 0.0) + float(row["w"])
        clip_right = float(clip.get("winLeft") or 0.0) + float(clip["w"])
        over = table_right - clip_right
        if over > 1:
            found.append({
                "window": row["window"],
                "table": row["path"].split("/", 1)[-1][:60],
                "over": round(over, 1),
            })
    return found


def main(argv: list[str]) -> int:
    directory = Path(argv[1] if len(argv) > 1 else "/tmp/layout-audit")
    audit = load_audit()
    try:
        result = audit.analyze(directory)
    except ValueError as error:
        print(f"layout audit unusable: {error}", file=sys.stderr)
        return 2

    problems: list[str] = []
    # The audit has to see every window. A window that failed to build would
    # otherwise shrink the sample silently and the gate would pass on a
    # smaller set (2026-09-16).
    expected = expected_windows()
    missing = sorted(set(expected) - set(result["images"]))
    if missing:
        problems.append(f"감사에 빠진 창: {', '.join(missing)}")
    if len(result["rows"]) < 200:
        problems.append(f"뷰가 {len(result['rows'])}개뿐입니다. 창이 제대로 만들어지지 않았습니다")
    for name, image in sorted(result["images"].items()):
        if image["content_box"] is None:
            problems.append(f"{name}: 아무것도 그려지지 않았습니다")
            continue
        for start, end, length in image["empty_row_bands"]:
            problems.append(f"{name}: {start}~{end}행이 비어 있습니다 ({length}pt)")
        for start, end, length in image["empty_col_bands"]:
            problems.append(f"{name}: {start}~{end}열이 비어 있습니다 ({length}pt)")
        # 창 가장자리에 붙은 여백은 정상이지만, 위아래 어느 한쪽만 두꺼우면
        # 내용이 한쪽으로 쏠려 보인다.
        top, bottom = image["top_margin"], image["bottom_margin"]
        # 여백은 픽셀이다. 40을 그대로 쓰면 Retina(2x)에서 20pt 기준이 되어
        # 같은 그림이 1x에서는 통과하고 2x에서는 실패한다. 포인트로 환산해서
        # 판단한다 (2026-09-16).
        scale = float(image.get("scale") or 1.0)
        limit = 40 * max(scale, 0.1)
        if top is not None and bottom is not None and abs(top - bottom) > limit:
            problems.append(f"{name}: 위아래 여백이 {top}/{bottom}으로 어긋납니다")

    # 열 최소 너비의 합이 표가 실제로 가진 폭보다 크면 마지막 열은 영영 보이지
    # 않는다. 표 안 글자 잘림은 툴팁이 있어 정상으로 넘기지만, 이 경우는
    # 넘길 수 없다: 가로 스크롤이 없으면 값을 읽을 방법이 아예 없다
    # (2026-09-16).
    for item in unreachable_columns(result["rows"]):
        problems.append(
            f"{item['window']}: {item['table']}의 열 최소 너비 합 {item['needed']:.0f}pt가 "
            f"표 폭 {item['available']:.0f}pt보다 큽니다. 마지막 열을 볼 수 없습니다"
        )

    # 가장 작은 크기에서 안쪽 내용이 창 밖으로 나가면 운영자는 그 부분을
    # 영영 볼 수 없다. 스크롤 안쪽은 원래 넘칠 수 있으므로 빼고 본다.
    for item in clipped_at_minimum(result["rows"]):
        problems.append(
            f"{item['window']}: 창을 최소 크기로 줄이면 {item['path']}가 "
            f"{item['why']} {item['amount']:.0f}pt 잘립니다"
        )

    # 표가 클립 뷰보다 넓으면 오른쪽 끝은 화면에 없고, 스크롤해서 볼 수도
    # 없다. 열 폭 계산이 아니라 프레임을 직접 비교해야 잡힌다 (2026-09-16).
    for item in tables_past_their_clip(result["rows"]):
        problems.append(
            f"{item['window']}: {item['table']}가 클립 뷰보다 {item['over']:.0f}pt 넓습니다. "
            f"오른쪽 끝을 볼 수 없습니다"
        )

    # 세로 스택의 다음 카드까지 40pt 넘게 비면 내용이 끊겨 보인다. 표는
    # 스크롤 프레임이 아니라 마지막으로 실제 그린 행의 아래쪽을 기준으로
    # 재서, 짧은 목록 아래에 큰 빈 꼬리가 생기는 회귀도 잡는다.
    for item in vertical_dead_bands(result["rows"]):
        problems.append(
            f"{item['window']}: {item['before']}와 {item['after']} 사이에 "
            f"{item['gap']:.0f}pt 빈 세로 띠가 있습니다"
        )

    # 같은 부모 안에서 형제 사이 간격이 제각각이면 눈에 띄게 고르지 않다.
    # 예전 메뉴 패널이 6/8/10을 섞어 썼다 (2026-09-16).
    for window, gaps in uneven_gaps(result["rows"]).items():
        problems.append(f"{window}: 형제 간격이 고르지 않습니다 {gaps}")

    # menu extra는 Jarvis 코어와 우측 상단 gear 두 요소만 가진다.
    for window, issues in menu_panel_surface_violations(result["rows"]).items():
        problems.append(f"{window}: core+gear 구성 위반 {issues}")
    for window, issues in unified_settings_identifier_violations(result["rows"]).items():
        problems.append(f"{window}: 즉시 실행 identifier 금지 위반 {issues}")
    for issue in menu_panel_source_violations():
        problems.append(f"menu-panel: 소스 구성 위반 {issue}")

    for card in result["collapsed_cards"]:
        problems.append(f"{card['window']}: 카드 높이가 {card['h']}pt로 붕괴했습니다")
    for item in result["overflow"]:
        problems.append(f"{item['window']}: {item['path']}가 창 밖으로 나갔습니다 {item}")
    for item in result["overlaps"]:
        if intentional_menu_panel_overlap(item):
            continue
        problems.append(f"{item['window']}: {item['a']} 위에 {item['b']}가 겹칩니다 {item['overlap']}")
    for item in result["spills"]:
        problems.append(
            f"{item['window']}: {item['parent']} 밖으로 {item['child']}가 {item['out']}pt 나갔습니다"
        )
    for item in result["clipped"]:
        # A table column truncates on purpose: the full text is in the tooltip
        # and the column width is the operator's choice.
        #
        # The marker is the table itself, not the row class: the row view is
        # ours (`StripedRowView`), so matching on AppKit's name silently stopped
        # filtering and reported 149 deliberate truncations as defects
        # (2026-09-17).
        if "/NSTableView" in item.get("path", ""):
            continue
        problems.append(
            f"{item['window']}: {item.get('text', item['kind'])[:40]!r}가 {item['clipped']}pt 잘렸습니다"
        )

    if problems:
        for line in problems:
            print("layout:", line, file=sys.stderr)
        return 1
    print(f"layout ok: {len(result['images'])} windows, {len(result['rows'])} views")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
