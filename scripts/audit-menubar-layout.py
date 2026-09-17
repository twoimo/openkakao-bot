#!/usr/bin/env python3
"""Menubar window layout audit: measure real rendered whitespace.

The menu extra can dump every window's view frames and a PNG of each window
(``--layout-audit DIR``). Frames alone cannot tell whether the *rendered*
result looks cramped or empty, so this reads the PNGs back and reports how
much of each window is actually covered by ink, plus the empty bands.

Usage: python3 scripts/audit-menubar-layout.py /tmp/layout-audit
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path


APPKIT_INTERNALS = (
    "NSVisualEffectView",
    "NSClipView",
    "NSScrollPocket",
    "NSScroller",
    "NSBannerView",
    "NSTableHeaderView",
    "NSTableBackgroundView",
    "NSTableRowView",
    "NSTableColumn",
    "NSHardPocketView",
    "NSButtonImageView",
)

# AppKit's own SwiftUI-backed widgets live under these hosting views. A search
# field's clear button is 15pt wide on purpose; reporting it as clipped text
# buried the labels that were actually cut off (2026-09-16).
APPKIT_HOSTED = (
    "_TtGC6AppKit",
    "SearchFieldAccessoryButton",
    "_NSKeyboardFocusClipView",
)


def is_internal(row: dict) -> bool:
    """AppKit's own scroll/table internals overlap by design.

    Only the internal view itself is skipped, never its children: the model
    window puts its cards inside a scroll view, and skipping a whole
    /NSScrollView subtree hid exactly the labels and cards that needed
    checking (2026-09-16).
    """
    kind = row["kind"]
    if kind.startswith("_") or kind.startswith("_Tt"):
        return True
    if kind.startswith(APPKIT_INTERNALS):
        return True
    return any(marker in row["path"] for marker in APPKIT_HOSTED)


def in_table_row(row: dict) -> bool:
    """Cells inside an NSTableRowView overlap the row's own background."""
    return "/NSTableRowView" in row["path"]


def hidden_ancestor(row: dict, by_path: dict[str, dict]) -> bool:
    """Whether any view above this one is hidden.

    A hidden container keeps laying its children out, and AppKit leaves the
    children's own `hidden` flag alone. Judging those children reported the
    empty-state card as spilling 17pt out of a view the operator cannot see
    (2026-09-17).
    """
    path = row["path"]
    while "/" in path:
        path = path.rsplit("/", 1)[0]
        ancestor = by_path.get(path)
        if ancestor is not None and ancestor["hidden"]:
            return True
    return False


# Containers that only position their children. Two of these can share space
# without hiding anything, so their overlap is not reported.
PLAIN_CONTAINERS = (
    "NSView",
    "NSStackView",
    "NSBox",
    "NSScrollView",
)

# A control draws its own title, and AppKit sizes that inner text view a few
# points wider than the button so the glyph edges are not cut. That is the
# control working, not content escaping (2026-09-16).
CONTROL_CHILDREN = (
    "NSButtonTextField",
    "NSButtonImageView",
    "NSTextFieldSimpleLabel",
    "_NSCoreHostingView",
    "_NSKeyboardFocusClipView",
)

# AppKit draws a control's bezel and title itself. macOS 14 builds a button
# from NSButtonBezelView plus NSButtonTextField, and those two share the
# button's box on purpose; macOS 26 uses SwiftUI-hosted views instead. Both
# are the control working, so nothing under a control is layout to judge
# (2026-09-16).
CONTROL_KINDS = (
    "NSButton",
    "NSSegmentedControl",
    "NSPopUpButton",
    "NSSearchField",
    "NSComboBox",
    "NSMenuItem",
)


def inside_control(row: dict) -> bool:
    """Whether the view is one of a control's own subviews."""
    parts = row["path"].split("/")
    return any(part.split("#")[0] in CONTROL_KINDS for part in parts[:-1])


def box(row: dict) -> tuple[float, float, float, float]:
    """Where AppKit actually puts the view: left, top, width, height.

    A control is laid out by its alignment rect, and a bezel makes that
    smaller than its frame. Comparing frames reported every pair of buttons
    in a row as overlapping by the bezel inset, and every button in a stack
    as sticking 7pt out of it, on the macOS 14 runner only (2026-09-16).
    """
    if "alignLeft" in row:
        return (row["alignLeft"], row["alignTop"], row["alignW"], row["alignH"])
    return (row["winLeft"], row["winTop"], row["w"], row["h"])


def draws_something(row: dict) -> bool:
    """Whether the view puts visible content on screen by itself."""
    if row.get("text"):
        return True
    if row.get("card"):
        return True
    return not row["kind"].startswith(PLAIN_CONTAINERS)


def scrolled(row: dict) -> bool:
    """Content inside a scroll view is legitimately larger than the window."""
    # 경로에 클래스 이름이 그대로 들어간다. 하위 클래스(TableScrollView)를
    # 쓰면 이름이 달라져 "스크롤 안쪽"이라는 표시를 못 알아본다. 종류로
    # 판단해 이름이 바뀌어도 같은 결론이 나오게 한다 (2026-09-16).
    if row["kind"].endswith("ScrollView"):
        return True
    return any(
        part.split("#", 1)[0].endswith("ScrollView")
        for part in row["path"].split("/")
    )


def read_png(path: Path) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    """Decode a non-interlaced 8-bit PNG into rows of RGBA tuples."""
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos = 8
    width = height = 0
    bit_depth = color_type = 0
    idat = bytearray()
    while pos + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[pos : pos + 4])
        kind = raw[pos + 4 : pos + 8]
        body = raw[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if bit_depth != 8 or interlace != 0:
                raise ValueError(f"{path}: unsupported PNG (depth={bit_depth}, interlace={interlace})")
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError(f"{path}: unsupported color type {color_type}")
    data = zlib.decompress(bytes(idat))
    stride = width * channels
    rows: list[list[tuple[int, int, int, int]]] = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = data[offset]
        offset += 1
        line = bytearray(data[offset : offset + stride])
        offset += stride
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = previous[i]
                upleft = previous[i - channels] if i >= channels else 0
                estimate = left + up - upleft
                pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - upleft)
                predictor = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
                line[i] = (line[i] + predictor) & 0xFF
        previous = line
        row: list[tuple[int, int, int, int]] = []
        for x in range(width):
            base = x * channels
            if channels == 4:
                row.append((line[base], line[base + 1], line[base + 2], line[base + 3]))
            elif channels == 3:
                row.append((line[base], line[base + 1], line[base + 2], 255))
            elif channels == 2:
                row.append((line[base], line[base], line[base], line[base + 1]))
            else:
                row.append((line[base], line[base], line[base], 255))
        rows.append(row)
    return width, height, rows


def background(rows: list[list[tuple[int, int, int, int]]]) -> tuple[int, int, int, int]:
    """The window background, taken from the border ring.

    Using the globally dominant color would treat a large white table as
    background and report its area as empty space.
    """
    height = len(rows)
    width = len(rows[0]) if height else 0
    counts: dict[tuple[int, int, int, int], int] = {}
    for y in range(height):
        edge_row = y < 6 or y >= height - 6
        for x in range(width):
            if not edge_row and 6 <= x < width - 6:
                continue
            pixel = rows[y][x]
            counts[pixel] = counts.get(pixel, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def differs(pixel: tuple[int, int, int, int], base: tuple[int, int, int, int], slack: int = 6) -> bool:
    """Any channel that moves counts as ink.

    The borderless menu panel renders onto a transparent surface, so its black
    text is ``(0, 0, 0, alpha)``: comparing RGB only made every glyph invisible
    and reported a panel full of text as 4% ink (2026-09-16). Alpha belongs in
    the comparison, and it also keeps light-mode white-on-white fills visible.
    """
    if abs(pixel[3] - base[3]) > slack:
        return True
    return sum(abs(a - b) for a, b in zip(pixel[:3], base[:3])) > slack * 3


def covered_by(
    band: tuple[int, int, int],
    ranges: tuple[tuple[float, float], ...],
    scale: float,
) -> bool:
    """Whether an empty band falls inside ranges that are not layout."""
    start, end = band[0], band[1]
    for left, right in ranges:
        if start >= left * scale - 1 and end <= right * scale + 1:
            return True
    return False


# Every window is filled with a 16pt inset, so the outermost strip of a window
# is padding by design. A scroller gutter sits just inside it, and the two
# together read as one long empty band even though neither is wasted layout
# (2026-09-16).
WINDOW_PADDING = 20.0


def ignored_columns(rows: list[dict]) -> dict[str, tuple[tuple[float, float], ...]]:
    """Column ranges in each window that are padding or a scroller gutter.

    The frames are in points and relative to the window, so a band measured in
    pixels is converted back with the capture scale. Adjacent ranges are
    merged, because padding next to a gutter is one visual gap.
    """
    spans: dict[str, list[tuple[float, float]]] = {}
    widths: dict[str, float] = {}
    for row in rows:
        widths.setdefault(row["window"], float(row.get("windowW") or 0.0))
        if row["hidden"] or "NSScroller" not in row["kind"]:
            continue
        left = row.get("winLeft", 0.0)
        spans.setdefault(row["window"], []).append((left, left + row["w"]))
    merged: dict[str, tuple[tuple[float, float], ...]] = {}
    for window, ranges in spans.items():
        width = widths.get(window, 0.0)
        if width > 0:
            ranges = ranges + [(0.0, WINDOW_PADDING), (width - WINDOW_PADDING, width)]
        ranges.sort()
        combined: list[tuple[float, float]] = []
        for left, right in ranges:
            if combined and left <= combined[-1][1] + 0.5:
                combined[-1] = (combined[-1][0], max(combined[-1][1], right))
            else:
                combined.append((left, right))
        merged[window] = tuple(combined)
    return merged


def bands(
    ink: list[int],
    threshold: int = 0,
    min_points: int = 24,
    scale: float = 1.0,
) -> list[tuple[int, int, int]]:
    """Return (start, end, length) of empty runs longer than min_points.

    The threshold is a point size, and a Retina capture stores two pixels per
    point. Comparing raw pixels made the same 22pt band fail at 2x and pass at
    1x, so the run length is divided by the capture scale before it is judged
    (2026-09-16). Reported lengths stay in pixels; only the decision is in
    points.
    """
    out: list[tuple[int, int, int]] = []
    start: int | None = None
    limit = min_points * scale
    for index, value in enumerate(ink):
        empty = value <= threshold
        if empty and start is None:
            start = index
        elif not empty and start is not None:
            if index - start > limit:
                out.append((start, index - 1, index - start))
            start = None
    if start is not None and len(ink) - start > limit:
        out.append((start, len(ink) - 1, len(ink) - start))
    return out


def audit(
    path: Path,
    scale: float = 0.0,
    ignore_cols: tuple[tuple[float, float], ...] = (),
) -> dict:
    """Measure one window image.

    scale is the capture scale (pixels per point). Zero means "work it out
    from the window size in layout.json", which is what analyze() passes.

    ignore_cols lists column ranges in points that are not layout at all: a
    scroll view reserves a gutter for its scroller, and the gutter is empty
    whenever the content fits. Reporting it as wasted space hid the bands that
    matter (2026-09-16).
    """
    width, height, rows = read_png(path)
    base = background(rows)
    if scale <= 0:
        scale = 1.0
    row_ink: list[int] = []
    col_ink = [0] * width
    total = 0
    min_x, min_y = width, height
    max_x = max_y = -1
    for y, row in enumerate(rows):
        count = 0
        for x, pixel in enumerate(row):
            if differs(pixel, base):
                count += 1
                col_ink[x] += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
        row_ink.append(count)
        total += count
    return {
        "size": [width, height],
        "ink_ratio": round(total / max(width * height, 1), 4),
        "content_box": [min_x, min_y, max_x, max_y] if max_x >= 0 else None,
        "top_margin": min_y if max_y >= 0 else None,
        "bottom_margin": (height - 1 - max_y) if max_y >= 0 else None,
        "left_margin": min_x if max_x >= 0 else None,
        "right_margin": (width - 1 - max_x) if max_x >= 0 else None,
        "scale": round(scale, 2),
        "empty_row_bands": bands(row_ink, scale=scale),
        "empty_col_bands": [
            band
            for band in bands(col_ink, scale=scale)
            if not covered_by(band, ignore_cols, scale)
        ],
    }


def overlaps_in(rows: list[dict]) -> list[dict]:
    """Siblings that share the same space hide each other's content."""
    found: list[dict] = []
    by_path = {row["path"]: row for row in rows}
    by_parent: dict[str, list[dict]] = {}
    for row in rows:
        # A control's own bezel and title share its box by design.
        if (
            row["hidden"]
            or hidden_ancestor(row, by_path)
            or not row["path"].count("/")
            or inside_control(row)
        ):
            continue
        by_parent.setdefault(row["path"].rsplit("/", 1)[0], []).append(row)
    for siblings in by_parent.values():
        for i in range(len(siblings)):
            for j in range(i + 1, len(siblings)):
                a, b = siblings[i], siblings[j]
                if is_internal(a) or is_internal(b):
                    continue
                # Only a pair where both views paint something can hide text.
                # Two plain containers sharing a frame (a spacer over a card,
                # a scroll pocket over its scroll view) is normal AppKit
                # structure, and reporting it buried the real overlaps
                # (2026-09-16).
                if not (draws_something(a) and draws_something(b)):
                    continue
                # A table cell sits on top of its row by design.
                if in_table_row(a) and in_table_row(b):
                    continue
                aleft, atop, aw, ah = box(a)
                bleft, btop, bw, bh = box(b)
                ox = min(aleft + aw, bleft + bw) - max(aleft, bleft)
                oy = min(atop + ah, btop + bh) - max(atop, btop)
                if ox > 4 and oy > 4:
                    found.append({
                        "window": a["window"],
                        "a": a["kind"] + " " + str(a.get("text", ""))[:24],
                        "b": b["kind"] + " " + str(b.get("text", ""))[:24],
                        "overlap": [round(ox, 1), round(oy, 1)],
                    })
    return found


def spills_in(rows: list[dict]) -> list[dict]:
    """Children that stick out of their parent's box.

    This is the general form of the collapsed-card bug: a container that
    reports no height while its content keeps its own size draws the content
    outside the card, which is what made every window look broken
    (2026-09-16). Scroll documents and table rows are meant to be larger, so
    they are skipped.
    """
    by_path = {row["path"]: row for row in rows}
    found: list[dict] = []
    for row in rows:
        # A control's bezel sits outside its alignment rect, so a button in a
        # stack reports a frame that is 7pt taller than the slot the stack
        # gave it. That is the control drawing its own edge, not content
        # escaping (2026-09-16).
        if (
            row["hidden"]
            or hidden_ancestor(row, by_path)
            or not row["path"].count("/")
            or inside_control(row)
        ):
            continue
        parent = by_path.get(row["path"].rsplit("/", 1)[0])
        if parent is None or is_internal(parent) or is_internal(row):
            continue
        if scrolled(parent) or "/NSTableView" in parent["path"]:
            continue
        if row["kind"].startswith(CONTROL_CHILDREN) or parent["kind"].startswith("NSButton"):
            continue
        pleft, ptop, pw, ph = box(parent)
        cleft, ctop, cw, ch = box(row)
        left = pleft - cleft
        right = (cleft + cw) - (pleft + pw)
        top = ptop - ctop
        bottom = (ctop + ch) - (ptop + ph)
        worst = max(left, right, top, bottom)
        if worst > 2:
            found.append({
                "window": row["window"],
                "parent": parent["kind"],
                "child": row["kind"],
                "out": round(worst, 1),
                "parentBox": [pleft, ptop, pw, ph],
                "childBox": [cleft, ctop, cw, ch],
            })
    return found


def analyze(directory: Path) -> dict:
    """Measure an audit directory. Raises ValueError when it is unusable."""
    if not directory.is_dir():
        raise ValueError(f"no such audit directory: {directory}")
    layout = directory / "layout.json"
    if not layout.exists():
        raise ValueError(f"layout.json missing in {directory}")
    rows = json.loads(layout.read_text(encoding="utf-8"))["windows"]
    # Capture scale: a Retina window image is larger than the window's own
    # point size. Without this the same gap is judged differently on a 1x and
    # a 2x machine (2026-09-16).
    point_sizes: dict[str, float] = {}
    for row in rows:
        if row.get("windowH"):
            point_sizes.setdefault(row["window"], float(row["windowH"]))
    images: dict[str, dict] = {}
    ignored = ignored_columns(rows)
    for png in sorted(directory.glob("*.png")):
        scale = 1.0
        points = point_sizes.get(png.stem, 0.0)
        if points > 0:
            _, height, _ = read_png(png)
            scale = max(height / points, 0.1)
        images[png.stem] = audit(png, scale=scale, ignore_cols=ignored.get(png.stem, ()))
    if not images:
        raise ValueError(f"no window images in {directory}")
    return {
        "images": images,
        "overflow": [
            {k: row[k] for k in ("window", "path", "kind", "overRight", "overBottom", "underLeft", "underTop") if k in row}
            for row in rows
            if any(key in row for key in ("overRight", "overBottom", "underLeft", "underTop"))
            and not is_internal(row)
            and not scrolled(row)
        ],
        # 어떤 카드가 내용을 담고도 납작해지면 안 된다. CardView만 보면 같은
        # 실수가 다른 컨테이너에서 되풀이돼도 못 잡는다 (2026-09-16).
        "collapsed_cards": [
            {k: row[k] for k in ("window", "path", "kind", "w", "h", "card") if k in row}
            for row in rows
            if not row["hidden"]
            and row["h"] < 20
            and (row["kind"].endswith("CardView") or row.get("card"))
        ],
        # A one-line label inside a fixed-width column is clipped on purpose
        # (the table truncates it and the full text is in the tooltip), so the
        # report keeps the two cases apart.
        "clipped": [
            {k: row[k] for k in ("window", "path", "kind", "text", "w", "needW", "clipped") if k in row}
            for row in rows
            if not row["hidden"] and row.get("clipped") and not is_internal(row)
        ],
        "overlaps": overlaps_in(rows),
        "spills": spills_in(rows),
        "rows": rows,
    }


def main(argv: list[str]) -> int:
    directory = Path(argv[1] if len(argv) > 1 else "/tmp/layout-audit")
    try:
        result = analyze(directory)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result["images"], indent=2, ensure_ascii=False))
    print("\noverflow:", json.dumps(result["overflow"], ensure_ascii=False))
    print("collapsed cards:", json.dumps(result["collapsed_cards"], ensure_ascii=False))
    print("clipped text:", json.dumps(result["clipped"], ensure_ascii=False, indent=1))
    overlaps = result["overlaps"]
    print("overlapping siblings:", len(overlaps))
    for item in overlaps[:12]:
        print("  ", json.dumps(item, ensure_ascii=False))
    spills = result["spills"]
    print("content spilling out of its parent:", len(spills))
    for item in spills[:12]:
        print("  ", json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
