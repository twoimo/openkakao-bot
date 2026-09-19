"""Golden dataset extraction for on-device reply fine-tuning.

Turns the room history into supervised (prompt, completion) pairs. Two sources
are joined:

1. reply-evidence.jsonl records every automatic turn the worker already decided
   on. A sent row proves delivery, not quality, so it only becomes gold when the
   approval ledger (golden/quality-approvals.jsonl) accepts it or the row itself
   carries a quality flag. Without a verdict the row is dropped, or kept and
   labelled model_generated under --include-unreviewed-evidence (2026-09-19).
2. context.sqlite3 holds the room transcript. The self author's own messages
   are the human gold answers: they are what the operator really typed, with no
   model in the loop.

Every emitted row records where it came from, who accepted it (quality, log_id,
event_id, reviewer), which room it belongs to, the conversation window that
precedes it, and a deterministic content hash used for de-duplication. Nothing
here touches the network, and the context database is opened read-only so a
running worker is never blocked (2026-09-17).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

SCHEMA_VERSION = 2

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

# Where the golden set records that a person looked at a model-written reply and
# accepted it. Delivery success is not a language judgement - a reply can be
# sent and still be wrong - so a sent row only becomes gold once something
# explicit says so (2026-09-19).
QUALITY_HUMAN_AUTHORED = "human_authored"
QUALITY_APPROVED = "approved"
QUALITY_UNREVIEWED = "unreviewed"
QUALITY_MODEL_GENERATED = "model_generated"
APPROVAL_FILENAME = "quality-approvals.jsonl"
_APPROVED_STATUSES = frozenset({"approved", "human_reviewed", "accepted"})
_REJECTED_STATUSES = frozenset({"rejected", "unsuitable"})
# A transcript clock outside this window is a parse accident, not a real row.
MIN_PLAUSIBLE_EPOCH = 946684800.0  # 2000-01-01
MAX_PLAUSIBLE_EPOCH = 4102444800.0  # 2100-01-01


def _plausible_epoch(epoch: Any) -> bool:
    """True when a parsed clock looks like a real transcript timestamp.

    A parse accident (1970, a year 5000 date, NaN, infinity) is reported as
    unknown so a caller never treats garbage as a valid ordering key.
    """
    try:
        value = float(epoch)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(value):
        return False
    return MIN_PLAUSIBLE_EPOCH <= value <= MAX_PLAUSIBLE_EPOCH


def parse_timestamp(value: Any) -> float:
    """Parse a transcript timestamp into epoch seconds, 0.0 when unknown.

    Both the transcript form (2026-01-01 10:00:30) and the evidence ledger form
    (2026-01-01T16:05:00+0900, sometimes with a trailing Z) are accepted. A
    value that cannot be read is reported as unknown rather than as 1970, so a
    caller can tell a missing clock apart from a real one.
    """
    text = str(value or "").strip()
    if not text:
        return 0.0
    iso = text[:-1] + "+00:00" if text[-1] in ("Z", "z") else text
    try:
        parsed: datetime | None = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None:
        try:
            epoch = (
                parsed.timestamp()
                if parsed.tzinfo is not None
                else time.mktime(parsed.timetuple())
            )
        except (OverflowError, OSError, ValueError):
            epoch = 0.0
        if _plausible_epoch(epoch):
            return float(epoch)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            epoch = time.mktime(time.strptime(text[: len(fmt)].strip(), fmt))
        except (ValueError, OverflowError, OSError):
            continue
        if _plausible_epoch(epoch):
            return float(epoch)
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


def _approval_key(value: Any) -> str:
    """Normalise a log id / event id so 555001 and 555001.0 are one key."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    if text.isdigit():
        return str(int(text))
    return text


@dataclass
class QualityApprovals:
    """Explicit human verdicts on rows that a model wrote.

    The ledger is append-only JSONL and the last verdict for a key wins, so a
    row can be approved, rejected, and approved again without rewriting what a
    reviewer already said (2026-09-19).
    """

    by_log_id: dict[str, dict[str, str]] = field(default_factory=dict)
    by_event_id: dict[str, dict[str, str]] = field(default_factory=dict)
    approved: int = 0
    rejected: int = 0
    malformed: int = 0

    def lookup(self, record: dict[str, Any]) -> dict[str, str] | None:
        """Return the last verdict for this row, or None when it is unreviewed."""
        for key, index in (("log_id", self.by_log_id), ("event_id", self.by_event_id)):
            candidate = _approval_key(record.get(key))
            if candidate and candidate in index:
                return index[candidate]
        return None

    @property
    def verdicts(self) -> int:
        return self.approved + self.rejected


def load_quality_approvals(path: Path | None) -> QualityApprovals:
    """Read the approval ledger; a missing file is an empty ledger, not an error."""
    approvals = QualityApprovals()
    if path is None or not path.is_file():
        return approvals
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return approvals
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                approvals.malformed += 1
                continue
            if not isinstance(record, dict):
                approvals.malformed += 1
                continue
            status = str(record.get("status") or "").strip().lower()
            if status not in _APPROVED_STATUSES and status not in _REJECTED_STATUSES:
                approvals.malformed += 1
                continue
            verdict = {
                "status": status,
                "reviewer": str(record.get("reviewer") or ""),
                "reviewed_at": str(record.get("reviewed_at") or record.get("recorded_at") or ""),
                "note": str(record.get("note") or ""),
            }
            if status in _APPROVED_STATUSES:
                approvals.approved += 1
            else:
                approvals.rejected += 1
            log_id = _approval_key(record.get("log_id"))
            event_id = _approval_key(record.get("event_id"))
            if log_id:
                approvals.by_log_id[log_id] = verdict
            if event_id:
                approvals.by_event_id[event_id] = verdict
    return approvals


def record_approval(
    path: Path,
    *,
    status: str,
    log_id: Any = None,
    event_id: Any = None,
    reviewer: str = "",
    note: str = "",
    recorded_at: str = "",
    ) -> dict[str, Any]:
    """Append one verdict to the approval ledger and return the stored entry."""
    normalized = str(status or "").strip().lower()
    if normalized not in _APPROVED_STATUSES and normalized not in _REJECTED_STATUSES:
        raise ValueError(f"unknown approval status: {status!r}")
    key_log = _approval_key(log_id)
    key_event = _approval_key(event_id)
    if not key_log and not key_event:
        raise ValueError("an approval needs a log_id or an event_id")
    entry = {
        "status": normalized,
        "log_id": key_log,
        "event_id": key_event,
        "reviewer": str(reviewer or ""),
        "note": str(note or ""),
        "recorded_at": recorded_at or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return entry


def gold_quality(record: dict[str, Any], approvals: QualityApprovals | None = None) -> str:
    """Classify one row: who wrote the completion, and whether it was accepted."""
    verdict = approvals.lookup(record) if approvals is not None else None
    if verdict is not None:
        return QUALITY_APPROVED if verdict["status"] in _APPROVED_STATUSES else QUALITY_UNREVIEWED
    declared = str(record.get("quality") or "").strip().lower()
    if declared in (
        QUALITY_APPROVED,
        QUALITY_HUMAN_AUTHORED,
        QUALITY_UNREVIEWED,
        QUALITY_MODEL_GENERATED,
    ):
        return declared
    if record.get("quality_approved") is True or record.get("human_reviewed") is True:
        return QUALITY_APPROVED
    source = str(record.get("source") or "")
    if source == "self_history":
        return QUALITY_HUMAN_AUTHORED
    if source == "auto_reply_sent":
        return QUALITY_MODEL_GENERATED
    return QUALITY_UNREVIEWED


def is_gold_quality(quality: str) -> bool:
    """True for a completion a person wrote or explicitly accepted."""
    return quality in (QUALITY_HUMAN_AUTHORED, QUALITY_APPROVED)


def _bump(counters: dict[str, int] | None, key: str, amount: int = 1) -> None:
    if counters is not None:
        counters[key] = counters.get(key, 0) + amount


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
    quality: str = QUALITY_HUMAN_AUTHORED
    log_id: int = 0
    event_id: str = ""
    reviewer: str = ""
    reviewed_at: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "pair_id": self.pair_id,
            "source": self.source,
            "quality": self.quality,
            "room": self.room,
            "chat_id": self.chat_id,
            "prompt": self.prompt,
            "completion": self.completion,
            "window": self.window,
            "recorded_at": self.recorded_at,
            "model": self.model,
            "log_id": self.log_id,
            "event_id": self.event_id,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
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


@contextmanager
def _connect_readonly(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Read a temporary snapshot so long scans never contend with the source DB."""
    with tempfile.TemporaryDirectory(prefix="golden-ro-copy-") as tmpdir:
        tmp_db = Path(tmpdir) / db_path.name
        try:
            shutil.copy2(db_path, tmp_db)
            wal = db_path.with_name(db_path.name + "-wal")
            shm = db_path.with_name(db_path.name + "-shm")
            for sidecar in (wal, shm):
                if sidecar.exists():
                    shutil.copy2(sidecar, Path(tmpdir) / sidecar.name)
            connection = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True, timeout=5.0)
        except (OSError, sqlite3.Error) as exc:
            raise sqlite3.OperationalError(
                "isolated read-only snapshot unavailable"
            ) from exc

        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            yield connection
        finally:
            connection.close()


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

    # Positional access keeps this working for a plain tuple connection as well
    # as the Row-factory connection the CLI opens (2026-09-17).
    for row in connection.execute(query, params):
        row_id, row_chat, row_date, row_user, row_message = row[:5]
        room = str(row_chat or "")
        if room != current_room:
            current_room = room
            window = []
            pending = []

        text = _norm_text(row_message)
        author = str(row_user or "")
        row_ts = parse_timestamp(row_date)
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
                pending_row_id = int(row_id or 0)
                pending_started_at = str(row_date or "")
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
            window.append((author, str(row_date or ""), text))
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
            row_log_id, row_sender, row_chat, row_date, row_message = row[:5]
            try:
                log_id = int(row_log_id)
            except (TypeError, ValueError):
                continue
            lookup[log_id] = {
                "message": _norm_text(row_message),
                "chat": str(row_chat or ""),
                "date": str(row_date or ""),
                "author": str(row_sender or ""),
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
    approvals: QualityApprovals | None = None,
    require_approval: bool = True,
    counters: dict[str, int] | None = None,
) -> Iterator[GoldenPair]:
    """Yield pairs from the evidence ledger, gated on an explicit verdict.

    A delivered reply is not automatically a good answer, so by default only
    rows carrying an approval verdict (or an inline quality flag) become gold.
    require_approval=False keeps the rest and labels them model_generated so an
    exploratory set is still possible, but a rejected row is always dropped and
    the two cases are never mixed silently (2026-09-19).
    """
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

            verdict = approvals.lookup(record) if approvals is not None else None
            if verdict is not None and verdict["status"] in _REJECTED_STATUSES:
                _bump(counters, "rejected")
                continue
            quality = gold_quality(record, approvals)
            if quality == QUALITY_UNREVIEWED:
                # Every row in this ledger is an automatic reply, so without a
                # verdict the honest label is model-written, not merely unseen.
                quality = QUALITY_MODEL_GENERATED
            if not is_gold_quality(quality):
                if require_approval:
                    _bump(counters, "unreviewed_skipped")
                    continue

            completion = _norm_text(record.get("reply"))[:max_completion]
            if not (min_completion <= len(completion) <= max_completion):
                continue
            if not _is_teachable(completion):
                continue

            inbound = _norm_text(record.get("message"))
            try:
                log_id = int(record.get("log_id"))
            except (TypeError, ValueError):
                log_id = 0
            resolved: dict[str, str] = {}
            if inbound_lookup:
                if log_id > 0:
                    resolved = inbound_lookup.get(log_id, {})
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

            # Counted here, not at the gate: a row that never becomes a pair
            # must not look like an accepted answer in the summary.
            _bump(counters, "approved" if is_gold_quality(quality) else "unreviewed_kept")
            emitted += 1
            yield GoldenPair(
                source="auto_reply_sent",
                room=str(record.get("chat") or resolved.get("chat") or ""),
                prompt=prompt,
                completion=completion,
                window=_window_from_rows(window_rows, window_size),
                recorded_at=str(record.get("recorded_at") or resolved.get("date") or ""),
                model=str(record.get("model") or ""),
                quality=quality,
                log_id=log_id,
                event_id=str(record.get("event_id") or ""),
                reviewer=str((verdict or {}).get("reviewer") or ""),
                reviewed_at=str((verdict or {}).get("reviewed_at") or ""),
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
    approvals_path: Path | None = None,
    require_approval: bool = True,
) -> tuple[list[GoldenPair], dict[str, Any]]:
    """Build the de-duplicated golden set and a summary of what was dropped."""
    pairs: list[GoldenPair] = []
    seen: set[str] = set()
    approval_path = (
        approvals_path
        if approvals_path is not None
        else state_root / "golden" / APPROVAL_FILENAME
    )
    approvals = load_quality_approvals(approval_path)
    stats: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "self_authors": list(self_authors),
        "rooms": list(rooms or []),
        "window_size": window_size,
        "min_completion_chars": min_completion,
        "max_completion_chars": max_completion,
        "merge_gap_seconds": merge_gap,
        "require_approval": require_approval,
        "approvals_file": str(approval_path),
        "approvals_loaded": approvals.verdicts,
        "approvals_approved": approvals.approved,
        "approvals_rejected": approvals.rejected,
        "approvals_malformed": approvals.malformed,
        "evidence_files": 0,
        "evidence_rows": 0,
        "evidence_approved": 0,
        "evidence_unreviewed_kept": 0,
        "evidence_unreviewed_skipped": 0,
        "evidence_rejected_skipped": 0,
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
        try:
            with _connect_readonly(context_db) as connection:
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
                with _connect_readonly(context_db) as resolver:
                    inbound_lookup = resolve_inbound_messages(resolver, sent_ids)
            except sqlite3.Error:
                pass
    stats["evidence_resolved"] = len(inbound_lookup)
    for path in evidence_files:
        counters: dict[str, int] = {}
        _absorb(
            iter_evidence_pairs(
                path,
                window_size=window_size,
                min_completion=min_completion,
                max_completion=max_completion,
                limit=limit,
                inbound_lookup=inbound_lookup,
                approvals=approvals,
                require_approval=require_approval,
                counters=counters,
            ),
            "evidence_rows",
        )
        stats["evidence_approved"] += counters.get("approved", 0)
        stats["evidence_unreviewed_kept"] += counters.get("unreviewed_kept", 0)
        stats["evidence_unreviewed_skipped"] += counters.get("unreviewed_skipped", 0)
        stats["evidence_rejected_skipped"] += counters.get("rejected", 0)

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
        with _connect_readonly(path) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='context_messages'"
            ).fetchone()
    except sqlite3.Error:
        return False
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
    parser.add_argument(
        "--approvals",
        type=Path,
        default=None,
        help="품질 승인 장부 경로 (기본: <state-root>/golden/quality-approvals.jsonl)",
    )
    parser.add_argument(
        "--include-unreviewed-evidence",
        action="store_true",
        help="사람이 승인하지 않은 자동답변도 포함 (model_generated로 표시)",
    )
    parser.add_argument(
        "--approve-log-id",
        action="append",
        type=int,
        default=None,
        help="이 log_id의 자동답변을 정답지로 승인 (반복 가능)",
    )
    parser.add_argument(
        "--reject-log-id",
        action="append",
        type=int,
        default=None,
        help="이 log_id의 자동답변을 정답지에서 제외 (반복 가능)",
    )
    parser.add_argument("--reviewer", default="", help="승인/제외 판단자 이름")
    parser.add_argument("--note", default="", help="승인/제외 메모")
    parser.add_argument(
        "--list-candidates",
        action="store_true",
        help="아직 승인하지 않은 자동답변 후보를 JSONL로 출력",
    )
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


def _print_candidates(
    *,
    state_root: Path,
    context_db: Path,
    approvals: QualityApprovals,
    window_size: int,
    min_completion: int,
    max_completion: int,
    limit: int,
) -> int:
    """Print the automatic replies a person has not accepted or dropped yet.

    Delivery success is the only thing the ledger proves, so the review list is
    the whole set of sent rows that carry no verdict. Each line is JSON with the
    log_id the approve/reject flags take (2026-09-19).
    """
    inbound_lookup: dict[int, dict[str, str]] = {}
    paths = _evidence_paths(state_root)
    if paths and context_db.is_file():
        sent_ids = _sent_log_ids(paths)
        if sent_ids:
            try:
                with _connect_readonly(context_db) as resolver:
                    inbound_lookup = resolve_inbound_messages(resolver, sent_ids)
            except sqlite3.Error:
                pass
    printed = 0
    for path in paths:
        for pair in iter_evidence_pairs(
            path,
            window_size=window_size,
            min_completion=min_completion,
            max_completion=max_completion,
            limit=limit,
            inbound_lookup=inbound_lookup,
            approvals=approvals,
            require_approval=False,
        ):
            if is_gold_quality(pair.quality):
                continue
            print(
                json.dumps(
                    {
                        "log_id": pair.log_id,
                        "event_id": pair.event_id,
                        "room": pair.room,
                        "recorded_at": pair.recorded_at,
                        "model": pair.model,
                        "prompt": pair.prompt,
                        "completion": pair.completion,
                    },
                    ensure_ascii=False,
                )
            )
            printed += 1
    print(
        f"# 미승인 자동답변 {printed}건 · 승인: --approve-log-id · 제외: --reject-log-id",
        file=sys.stderr,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_root = (args.state_root or _default_state_root()).expanduser()
    context_db = (args.context_db or _default_context_db(state_root)).expanduser()
    approvals_path = (
        args.approvals
        if args.approvals is not None
        else state_root / "golden" / APPROVAL_FILENAME
    ).expanduser()
    require_approval = not args.include_unreviewed_evidence
    window_size = max(1, args.window)
    min_completion = max(1, args.min_chars)
    max_completion = max(min_completion, args.max_chars)
    limit = max(0, args.limit)

    recorded: list[dict[str, Any]] = []
    for log_id in args.approve_log_id or []:
        recorded.append(
            record_approval(
                approvals_path,
                status="approved",
                log_id=log_id,
                reviewer=args.reviewer,
                note=args.note,
            )
        )
    for log_id in args.reject_log_id or []:
        recorded.append(
            record_approval(
                approvals_path,
                status="rejected",
                log_id=log_id,
                reviewer=args.reviewer,
                note=args.note,
            )
        )
    if recorded:
        print(f"품질 판정 {len(recorded)}건 기록 → {approvals_path}")

    if args.list_candidates:
        return _print_candidates(
            state_root=state_root,
            context_db=context_db,
            approvals=load_quality_approvals(approvals_path),
            window_size=window_size,
            min_completion=min_completion,
            max_completion=max_completion,
            limit=limit,
        )

    output = args.output
    if output is None:
        output = state_root / "golden" / "reply-golden.jsonl"
    authors = tuple(args.self_author) if args.self_author else DEFAULT_SELF_AUTHORS

    pairs, stats = extract_golden_dataset(
        state_root=state_root,
        context_db=context_db,
        self_authors=authors,
        rooms=args.room,
        window_size=window_size,
        min_completion=min_completion,
        max_completion=max_completion,
        limit=limit,
        merge_gap=max(0.0, args.merge_gap),
        approvals_path=approvals_path,
        require_approval=require_approval,
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
        gate = "미승인 포함" if not summary["require_approval"] else "승인분만"
        print(
            f"  자동답변 게이트({gate}): 채택 {summary['evidence_approved']}건 · "
            f"미승인 제외 {summary['evidence_unreviewed_skipped']}건 · "
            f"제외 판정 {summary['evidence_rejected_skipped']}건"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
