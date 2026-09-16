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
    "doctor",
    "doctor-dark",
    "jobs",
    "jobs-dark",
    "log",
    "log-dark",
    "menu-panel",
    "menu-panel-dark",
    "menu-panel-rooms",
    "menu-panel-rooms-dark",
    "model",
    "model-dark",
    "rooms",
    "rooms-dark",
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


def clipped_at_minimum(rows: list[dict]) -> list[dict]:
    """Content that runs out of window when the window is at its smallest.

    The audit renders every window at its build size and again at its minimum.
    At the build size a wide window can absorb a long label; at the minimum it
    cannot, and the part that does not fit is simply not on screen. Only the
    minimum-size rows are judged, and rows inside a scroll view are skipped
    because scrolling to them is the point of the scroll view (2026-09-16).
    """
    found: dict[str, dict] = {}
    for row in rows:
        if row["hidden"] or not row["window"].endswith(RESIZED_SUFFIX):
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

    # 같은 부모 안에서 형제 사이 간격이 제각각이면 눈에 띄게 고르지 않다.
    # 예전 메뉴 패널이 6/8/10을 섞어 썼다 (2026-09-16).
    for window, gaps in uneven_gaps(result["rows"]).items():
        problems.append(f"{window}: 형제 간격이 고르지 않습니다 {gaps}")

    for card in result["collapsed_cards"]:
        problems.append(f"{card['window']}: 카드 높이가 {card['h']}pt로 붕괴했습니다")
    for item in result["overflow"]:
        problems.append(f"{item['window']}: {item['path']}가 창 밖으로 나갔습니다 {item}")
    for item in result["overlaps"]:
        problems.append(f"{item['window']}: {item['a']} 위에 {item['b']}가 겹칩니다 {item['overlap']}")
    for item in result["spills"]:
        problems.append(
            f"{item['window']}: {item['parent']} 밖으로 {item['child']}가 {item['out']}pt 나갔습니다"
        )
    for item in result["clipped"]:
        # A table column truncates on purpose: the full text is in the tooltip
        # and the column width is the operator's choice.
        if "/NSTableRowView" in item.get("path", ""):
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
