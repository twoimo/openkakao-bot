"""Golden dataset extraction for on-device reply fine-tuning.

Turns the room history into supervised (prompt, completion) pairs. Two sources
are joined:

1. reply-evidence.jsonl records every automatic turn the worker already decided
   on. Rows with status "sent" carry the reply that actually went out, so they
   are the highest-confidence completions available.
2. context.sqlite3 holds the room transcript. The self author's own messages
   are the human gold answers: they are what the operator really typed, with no
   model in the loop.

Every emitted row records where it came from, which room it belongs to, the
conversation window that precedes it, and a deterministic content hash used for
de-duplication. Nothing here touches the network, and the context database is
opened read-only so a running worker is never blocked (2026-09-17).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

SCHEMA_VERSION = 1

# A completion shorter than this is usually an acknowledgement ("ㅇㅇ", "ㄹㅇ")
# that teaches the model nothing about style or content.
MIN_COMPLETION_CHARS = 4
MIN_PROMPT_CHARS = 2
# Long completions are usually pasted documents or link dumps. They dominate
# the loss if left in, so they are capped out of the supervised set.
MAX_COMPLETION_CHARS = 400
MAX_PROMPT_CHARS = 1200

# Placeholder text the importer substitutes for non-text payloads.
_PLACEHOLDER_PATTERN = re.compile(r"^\(?(이모티콘|사진|동영상|파일|음성|지도|연락처)\)?")
# A bare URL line carries no teachable language.
_URL_ONLY_PATTERN = re.compile(r"^(?:https?://|www\.)\S+$")
_REPEAT_PATTERN = re.compile(r"(.)\1{4,}")
# The room importer rewrites some payloads into a fixed summary block. Those
# blocks are machine prose, not the operator's own phrasing, so they must never
# be learned as a gold answer (2026-09-17).
_SUMMARY_BLOCK_PATTERN = re.compile(r"^\[설명자료\]|무엇을:|어떻게:|핵심:주장:")
# Automated feeds post under the operator's nickname in a few rooms.
_BOT_PREFIXES = ("GeekNews TOP5", "GeekNews TOP", "[GeekNews]")

DEFAULT_SELF_AUTHORS: tuple[str, ...] = ("최연우",)
# KakaoTalk users split one thought across several sends. Merging consecutive
# self messages inside this gap produces the answer the operator actually meant
# instead of a nine-character fragment (2026-09-17).
DEFAULT_MERGE_GAP_SECONDS = 180


def _parse_ts(value: Any) -> float:
    """Parse the transcript timestamp into epoch seconds, 0.0 when unknown."""
    text = str(value or "").strip()
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(text[: len(fmt) + 2].strip(), fmt))
        except (ValueError, OverflowError):
            continue
    return 0.0
DEFAULT_CONTEXT_DB = "context.sqlite3"
DEFAULT_EVIDENCE_NAME = "reply-evidence.jsonl"


def _norm_text(value: Any) -> str:
    """Collapse whitespace so identical messages hash identically."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _content_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


def _is_teachable(text: str) -> bool:
    """Reject rows that carry no language the model can learn from."""
    if not text:
        return False
    if _PLACEHOLDER_PATTERN.match(text):
        return False
    if _URL_ONLY_PATTERN.match(text):
        return False
    if _SUMMARY_BLOCK_PATTERN.search(text):
        return False
    if text.startswith(_BOT_PREFIXES):
        return False
    # A message that is mostly link text teaches URL formatting, not speech.
    link_chars = sum(len(m) for m in re.findall(r"https?://\S+", text))
    if link_chars and link_chars > len(text) * 0.5:
        return False
    # "ㅋㅋㅋㅋㅋㅋ" and "ㅠㅠㅠㅠㅠ" are pure affect; they inflate the set
    # without teaching phrasing.
    compact = text.replace(" ", "")
    if _REPEAT_PATTERN.fullmatch(compact):
        return False
    if len(set(compact)) <= 1:
        return False
    return True


@dataclass
class GoldenPair:
    """One supervised example plus the provenance a trainer needs."""

    source: str
    room: str
    prompt: str
    completion: str
    chat_id: str = ""
    window: list[dict[str, str]] = field(default_factory=list)
    recorded_at: str = ""
    model: str = ""
    row_id: int = 0
    pair_id: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "pair_id": self.pair_id,
            "source": self.source,
            "room": self.room,
            "chat_id": self.chat_id,
            "prompt": self.prompt,
            "completion": self.completion,
            "window": self.window,
            "recorded_at": self.recorded_at,
            "model": self.model,
        }


def _window_from_rows(
    rows: Sequence[tuple[str, str, str]], limit: int
) -> list[dict[str, str]]:
    window: list[dict[str, str]] = []
    for author, date, message in rows[-limit:]:
        text = _norm_text(message)
        if not text:
            continue
        window.append({"author": str(author or ""), "date": str(date or ""), "message": text})
    return window


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the large context database without taking a write lock."""
    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    return connection


def iter_self_pairs(
    connection: sqlite3.Connection,
    *,
    self_authors: Sequence[str],
    rooms: Sequence[str] | None,
    window_size: int,
    min_completion: int,
    max_completion: int,
    limit: int,
    merge_gap: float = DEFAULT_MERGE_GAP_SECONDS,
) -> Iterator[GoldenPair]:
    """Yield (preceding window, self message run) pairs from the transcript."""
    authors = [a for a in (str(x).strip() for x in self_authors) if a]
    if not authors:
        return

    room_clause = ""
    params: list[Any] = []
    if rooms:
        room_list = [str(r).strip() for r in rooms if str(r).strip()]
        if room_list:
            room_clause = " WHERE chat IN (" + ",".join("?" for _ in room_list) + ")"
            params.extend(room_list)

    # Every participant is read, not only the self author: the prompt window is
    # made of what other people said right before the gold answer. Filtering the
    # self author in SQL left the window empty and produced no pairs at all
    # (2026-09-17).
    query = (
        "SELECT id, chat, date, user_name, message FROM context_messages"
        f"{room_clause} ORDER BY chat, id"
    )

    emitted = 0
    current_room = ""
    window: list[tuple[str, str, str]] = []
    # Buffered run of consecutive self messages that forms one answer.
    pending: list[str] = []
    pending_row_id = 0
    pending_started_at = ""
    pending_last_ts = 0.0

    def _flush() -> GoldenPair | None:
        """Turn the buffered run into a pair, or drop it when unusable."""
        if not pending or not window:
            return None
        completion = _norm_text(" ".join(pending))[:max_completion]
        if len(completion) < min_completion or not _is_teachable(completion):
            return None
        prompt = _norm_text(" ".join(entry[2] for entry in window[-window_size:]))
        prompt = prompt[:MAX_PROMPT_CHARS]
        if len(prompt) < MIN_PROMPT_CHARS:
            return None
        return GoldenPair(
            source="self_history",
            room=current_room,
            prompt=prompt,
            completion=completion,
            window=_window_from_rows(window, window_size),
            recorded_at=pending_started_at,
            row_id=pending_row_id,
        )

    for row in connection.execute(query, params):
        room = str(row["chat"] or "")
        if room != current_room:
            current_room = room
            window = []
            pending = []

        text = _norm_text(row["message"])
        author = str(row["user_name"] or "")
        row_ts = _parse_ts(row["date"])
        is_self = author in authors

        if is_self:
            # Continue the run only when it belongs to the same answer burst.
            if pending and pending_last_ts and row_ts and row_ts - pending_last_ts > merge_gap:
                completed = _flush()
                if completed is not None:
                    emitted += 1
                    yield completed
                    if limit and emitted >= limit:
                        return
                window = []
            if not pending:
                pending_row_id = int(row["id"] or 0)
                pending_started_at = str(row["date"] or "")
            if text:
                pending.append(text)
            pending_last_ts = row_ts or pending_last_ts
            continue

        # Another speaker arrived: the run is over, and their message starts the
        # next prompt window.
        if pending:
            completed = _flush()
            if completed is not None:
                emitted += 1
                yield completed
                if limit and emitted >= limit:
                    return
            pending = []
            window = []

        if text and not _SUMMARY_BLOCK_PATTERN.search(text):
            window.append((author, str(row["date"] or ""), text))
        if len(window) > window_size * 4:
            window = window[-window_size * 2 :]

    completed = _flush()
    if completed is not None:
        yield completed


def _sent_log_ids(evidence_paths: Sequence[Path]) -> list[int]:
    """Collect the log ids of confirmed sends so they can be resolved at once."""
    ids: list[int] = []
    for path in evidence_paths:
        if not path.exists():
            continue
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if str(record.get("status") or "") != "sent":
                    continue
                try:
                    log_id = int(record.get("log_id"))
                except (TypeError, ValueError):
                    continue
                if log_id > 0:
                    ids.append(log_id)
    return ids


def resolve_inbound_messages(
    connection: sqlite3.Connection, log_ids: Sequence[int]
) -> dict[int, dict[str, str]]:
    """Resolve an inbound message for each log id through the live-event join.

    The evidence ledger stores the outgoing reply but not always the message
    that triggered it. context_live_events maps the KakaoTalk log id onto the
    transcript row, which is where the inbound text still lives (2026-09-17).
    """
    lookup: dict[int, dict[str, str]] = {}
    unique = sorted({int(x) for x in log_ids if int(x) > 0})
    # SQLite caps a statement's variable count; chunk well under it.
    chunk_size = 400
    for start in range(0, len(unique), chunk_size):
        chunk = unique[start : start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        query = (
            "SELECT e.log_id AS log_id, e.sender_name AS sender_name, "
            "m.chat AS chat, m.date AS date, m.message AS message "
            "FROM context_live_events e JOIN context_messages m "
            "ON m.id = e.context_message_id "
            f"WHERE e.log_id IN ({placeholders})"
        )
        try:
            rows = connection.execute(query, chunk).fetchall()
        except sqlite3.Error:
            return lookup
        for row in rows:
            try:
                log_id = int(row["log_id"])
            except (TypeError, ValueError):
                continue
            lookup[log_id] = {
                "message": _norm_text(row["message"]),
                "chat": str(row["chat"] or ""),
                "date": str(row["date"] or ""),
                "author": str(row["sender_name"] or ""),
            }
    return lookup


def iter_evidence_pairs(
    evidence_path: Path,
    *,
    window_size: int,
    min_completion: int,
    max_completion: int,
    limit: int,
    inbound_lookup: dict[int, dict[str, str]] | None = None,
) -> Iterator[GoldenPair]:
    """Yield pairs from confirmed automatic sends in the evidence ledger."""
    if not evidence_path.exists():
        return

    emitted = 0
    try:
        handle = evidence_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if str(record.get("status") or "") != "sent":
                continue

            completion = _norm_text(record.get("reply"))[:max_completion]
            if not (min_completion <= len(completion) <= max_completion):
                continue
            if not _is_teachable(completion):
                continue

            inbound = _norm_text(record.get("message"))
            resolved: dict[str, str] = {}
            if inbound_lookup:
                try:
                    resolved = inbound_lookup.get(int(record.get("log_id")), {})
                except (TypeError, ValueError):
                    resolved = {}
                if not inbound:
                    inbound = _norm_text(resolved.get("message"))
            recent = record.get("recent_conversation")
            window_rows: list[tuple[str, str, str]] = []
            if isinstance(recent, list):
                for entry in recent:
                    if not isinstance(entry, dict):
                        continue
                    window_rows.append(
                        (
                            str(entry.get("author") or entry.get("sender") or ""),
                            str(entry.get("date") or entry.get("sent_at") or ""),
                            _norm_text(entry.get("message")),
                        )
                    )
            prompt = inbound or _norm_text(" ".join(row[2] for row in window_rows[-window_size:]))
            prompt = prompt[:MAX_PROMPT_CHARS]
            if len(prompt) < MIN_PROMPT_CHARS:
                continue

            emitted += 1
            yield GoldenPair(
                source="auto_reply_sent",
                room=str(record.get("chat") or resolved.get("chat") or ""),
                prompt=prompt,
                completion=completion,
                window=_window_from_rows(window_rows, window_size),
                recorded_at=str(record.get("recorded_at") or resolved.get("date") or ""),
                model=str(record.get("model") or ""),
            )
            if limit and emitted >= limit:
                return


def _evidence_paths(state_root: Path) -> list[Path]:
    """Find every room evidence ledger under a state root."""
    paths: list[Path] = []
    rooms_dir = state_root / "rooms"
    if rooms_dir.is_dir():
        for room_dir in sorted(rooms_dir.iterdir()):
            candidate = room_dir / DEFAULT_EVIDENCE_NAME
            if candidate.is_file():
                paths.append(candidate)
    legacy = state_root / DEFAULT_EVIDENCE_NAME
    if legacy.is_file():
        paths.append(legacy)
    return paths


def extract_golden_dataset(
    *,
    state_root: Path,
    context_db: Path,
    self_authors: Sequence[str] = DEFAULT_SELF_AUTHORS,
    rooms: Sequence[str] | None = None,
    window_size: int = 6,
    min_completion: int = MIN_COMPLETION_CHARS,
    max_completion: int = MAX_COMPLETION_CHARS,
    limit: int = 0,
    merge_gap: float = DEFAULT_MERGE_GAP_SECONDS,
) -> tuple[list[GoldenPair], dict[str, Any]]:
    """Build the de-duplicated golden set and a summary of what was dropped."""
    pairs: list[GoldenPair] = []
    seen: set[str] = set()
    stats: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "self_authors": list(self_authors),
        "rooms": list(rooms or []),
        "window_size": window_size,
        "min_completion_chars": min_completion,
        "max_completion_chars": max_completion,
        "merge_gap_seconds": merge_gap,
        "evidence_files": 0,
        "evidence_rows": 0,
        "self_rows": 0,
        "duplicates": 0,
        "context_db": str(context_db),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    def _absorb(source_iter: Iterable[GoldenPair], counter_key: str) -> None:
        for pair in source_iter:
            pair.pair_id = _content_hash(pair.prompt, pair.completion, pair.room)
            if pair.pair_id in seen:
                stats["duplicates"] += 1
                continue
            seen.add(pair.pair_id)
            stats[counter_key] += 1
            pairs.append(pair)

    if context_db.is_file():
        connection = None
        try:
            connection = _connect_readonly(context_db)
        except sqlite3.Error as exc:
            stats["context_error"] = f"{type(exc).__name__}: {exc}"
        if connection is not None:
            try:
                _absorb(
                    iter_self_pairs(
                        connection,
                        self_authors=self_authors,
                        rooms=rooms,
                        window_size=window_size,
                        min_completion=min_completion,
                        max_completion=max_completion,
                        limit=limit,
                        merge_gap=merge_gap,
                    ),
                    "self_rows",
                )
            except sqlite3.Error as exc:
                stats["context_error"] = f"{type(exc).__name__}: {exc}"
            finally:
                connection.close()
    else:
        stats["context_error"] = f"missing: {context_db}"

    evidence_files = _evidence_paths(state_root)
    stats["evidence_files"] = len(evidence_files)
    inbound_lookup: dict[int, dict[str, str]] = {}
    if evidence_files and context_db.is_file():
        sent_ids = _sent_log_ids(evidence_files)
        stats["evidence_sent_ids"] = len(sent_ids)
        if sent_ids:
            try:
                resolver = _connect_readonly(context_db)
            except sqlite3.Error:
                resolver = None
            if resolver is not None:
                try:
                    inbound_lookup = resolve_inbound_messages(resolver, sent_ids)
                finally:
                    resolver.close()
    stats["evidence_resolved"] = len(inbound_lookup)
    for path in evidence_files:
        _absorb(
            iter_evidence_pairs(
                path,
                window_size=window_size,
                min_completion=min_completion,
                max_completion=max_completion,
                limit=limit,
                inbound_lookup=inbound_lookup,
            ),
            "evidence_rows",
        )

    stats["total_pairs"] = len(pairs)
    stats["prompt_chars"] = sum(len(p.prompt) for p in pairs)
    stats["completion_chars"] = sum(len(p.completion) for p in pairs)
    return pairs, stats


def write_dataset(
    pairs: Sequence[GoldenPair], output: Path, stats: dict[str, Any]
) -> dict[str, Any]:
    """Write JSONL plus a sidecar summary. Existing files are replaced."""
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for pair in pairs:
            handle.write(json.dumps(pair.to_record(), ensure_ascii=False) + "\n")
    tmp.replace(output)
    summary_path = output.with_suffix(".summary.json")
    summary = dict(stats)
    summary["output"] = str(output)
    summary["written_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tmp_summary = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_summary.replace(summary_path)
    return summary


def _default_state_root() -> Path:
    override = os.environ.get("OPENKAKAO_STATE_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/openkakao/bujamentor"


def _db_has_transcript(path: Path) -> bool:
    """True when the file really is the transcript database.

    The state root keeps a small context.sqlite3 that only holds operator
    prompts, while the real transcript lives in the openkakao application
    support root. The name alone is not enough, so the table is probed
    (2026-09-17).
    """
    if not path.is_file():
        return False
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='context_messages'"
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        connection.close()
    return bool(row)


def _default_context_db(state_root: Path) -> Path:
    override = os.environ.get("OPENKAKAO_CONTEXT_DB")
    if override:
        return Path(override).expanduser()
    candidates = [
        state_root / DEFAULT_CONTEXT_DB,
        Path.home() / "Library/Application Support/openkakao" / DEFAULT_CONTEXT_DB,
    ]
    for candidate in candidates:
        if _db_has_transcript(candidate):
            return candidate
    return candidates[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="카카오톡 대화 기록에서 골든 데이터셋(질문-정답 쌍)을 추출합니다.",
    )
    parser.add_argument("--state-root", type=Path, default=None, help="자동답변 상태 루트")
    parser.add_argument("--context-db", type=Path, default=None, help="컨텍스트 SQLite 경로")
    parser.add_argument("--output", type=Path, default=None, help="출력 JSONL 경로")
    parser.add_argument(
        "--self-author", action="append", default=None, help="정답 발화자 (반복 가능)"
    )
    parser.add_argument("--room", action="append", default=None, help="포함할 방 이름 (반복 가능)")
    parser.add_argument("--window", type=int, default=6, help="프롬프트에 넣을 직전 메시지 수")
    parser.add_argument(
        "--min-chars", type=int, default=MIN_COMPLETION_CHARS, help="정답 최소 길이"
    )
    parser.add_argument(
        "--max-chars", type=int, default=MAX_COMPLETION_CHARS, help="정답 최대 길이"
    )
    parser.add_argument("--limit", type=int, default=0, help="소스별 최대 추출 수 (0=무제한)")
    parser.add_argument(
        "--merge-gap",
        type=float,
        default=float(DEFAULT_MERGE_GAP_SECONDS),
        help="연속 발화를 한 답변으로 묶는 최대 간격(초)",
    )
    parser.add_argument("--json", action="store_true", help="요약을 JSON으로 출력")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_root = (args.state_root or _default_state_root()).expanduser()
    context_db = (args.context_db or _default_context_db(state_root)).expanduser()
    output = args.output
    if output is None:
        output = state_root / "golden" / "reply-golden.jsonl"
    authors = tuple(args.self_author) if args.self_author else DEFAULT_SELF_AUTHORS

    pairs, stats = extract_golden_dataset(
        state_root=state_root,
        context_db=context_db,
        self_authors=authors,
        rooms=args.room,
        window_size=max(1, args.window),
        min_completion=max(1, args.min_chars),
        max_completion=max(args.min_chars, args.max_chars),
        limit=max(0, args.limit),
        merge_gap=max(0.0, args.merge_gap),
    )
    summary = write_dataset(pairs, output, stats)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"골든 데이터셋 {summary['total_pairs']}쌍 → {output}")
        print(
            f"  본인 발화 {summary['self_rows']}쌍 · 자동답변 확정 {summary['evidence_rows']}쌍 "
            f"· 중복 제거 {summary['duplicates']}건"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
