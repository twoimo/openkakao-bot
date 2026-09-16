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


def expected_windows() -> tuple[str, ...]:
    return EXPECTED_WINDOWS


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
        # A stack may vary spacing on purpose (a title sits closer to its
        # body), so only report a rhythm that mixes more than two values.
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
        if top is not None and bottom is not None and abs(top - bottom) > 40:
            problems.append(f"{name}: 위아래 여백이 {top}/{bottom}으로 어긋납니다")

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
