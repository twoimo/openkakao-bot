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
)


def is_internal(row: dict) -> bool:
    """AppKit's own scroll/table internals overlap by design."""
    kind = row["kind"]
    if kind.startswith("_") or kind.startswith("_Tt"):
        return True
    if kind.startswith(APPKIT_INTERNALS):
        return True
    # Views scrolled by an NSScrollView legitimately extend past the window.
    return "/NSScrollView" in row["path"]


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
    return sum(abs(a - b) for a, b in zip(pixel[:3], base[:3])) > slack * 3


def bands(ink: list[int], threshold: int = 0) -> list[tuple[int, int, int]]:
    """Return (start, end, length) of empty runs longer than 24 rows/columns."""
    out: list[tuple[int, int, int]] = []
    start: int | None = None
    for index, value in enumerate(ink):
        empty = value <= threshold
        if empty and start is None:
            start = index
        elif not empty and start is not None:
            if index - start > 24:
                out.append((start, index - 1, index - start))
            start = None
    if start is not None and len(ink) - start > 24:
        out.append((start, len(ink) - 1, len(ink) - start))
    return out


def audit(path: Path) -> dict:
    width, height, rows = read_png(path)
    base = background(rows)
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
        "empty_row_bands": bands(row_ink),
        "empty_col_bands": bands(col_ink),
    }


def main(argv: list[str]) -> int:
    directory = Path(argv[1] if len(argv) > 1 else "/tmp/layout-audit")
    report: dict[str, dict] = {}
    for png in sorted(directory.glob("*.png")):
        report[png.stem] = audit(png)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    layout = directory / "layout.json"
    if layout.exists():
        rows = json.loads(layout.read_text(encoding="utf-8"))["windows"]
        overflow = [
            {k: row[k] for k in ("window", "path", "kind", "overRight", "overBottom") if k in row}
            for row in rows
            if any(key in row for key in ("overRight", "overBottom"))
        ]
        collapsed = [
            {k: row[k] for k in ("window", "path", "kind", "w", "h")}
            for row in rows
            if not row["hidden"]
            and row["kind"].endswith("CardView")
            and row["h"] < 20
        ]
        print("\noverflow:", json.dumps(overflow, ensure_ascii=False))
        print("collapsed cards:", json.dumps(collapsed, ensure_ascii=False))
        clipped = [
            {k: row[k] for k in ("window", "kind", "text", "w", "needW", "clipped") if k in row}
            for row in rows
            if not row["hidden"] and row.get("clipped") and not is_internal(row)
        ]
        print("clipped text:", json.dumps(clipped, ensure_ascii=False, indent=1))
        # 같은 부모 안에서 형제가 겹치는지: 두 카드가 같은 자리를 차지하면 글자가 지워진다.
        overlaps: list[dict] = []
        by_parent: dict[str, list[dict]] = {}
        for row in rows:
            if row["hidden"] or not row["path"].count("/"):
                continue
            by_parent.setdefault(row["path"].rsplit("/", 1)[0], []).append(row)
        for parent, siblings in by_parent.items():
            for i in range(len(siblings)):
                for j in range(i + 1, len(siblings)):
                    a, b = siblings[i], siblings[j]
                    if is_internal(a) or is_internal(b):
                        continue
                    if a["kind"] == b["kind"]:
                        continue
                    if a["kind"].startswith("_") or b["kind"].startswith("_"):
                        continue
                    ox = min(a["winLeft"] + a["w"], b["winLeft"] + b["w"]) - max(a["winLeft"], b["winLeft"])
                    oy = min(a["winTop"] + a["h"], b["winTop"] + b["h"]) - max(a["winTop"], b["winTop"])
                    if ox > 4 and oy > 4:
                        overlaps.append({
                            "window": a["window"],
                            "a": a["kind"] + " " + str(a.get("text", ""))[:24],
                            "b": b["kind"] + " " + str(b.get("text", ""))[:24],
                            "overlap": [round(ox, 1), round(oy, 1)],
                        })
        print("overlapping siblings:", len(overlaps))
        for item in overlaps[:12]:
            print("  ", json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
