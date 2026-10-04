"""Read existing automatic-turn receipts and queues without starting workers.

Generation and delivery halves share an event identity. Queue terminal state is
authoritative; a retained delivery ledger can also attest an exact outgoing DB
row. Scheduling, a posted-slot cursor, and model output alone never prove send.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import unicodedata
from datetime import datetime
from contextlib import closing
from pathlib import Path

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 100_000
STATES = frozenset(('confirmed', 'waiting', 'failed', 'cancelled', 'unknown', 'skipped'))


def _text(value: object, limit: int = 16_000) -> str:
    return value[:limit] if isinstance(value, str) else ''


def _id(value: object) -> str:
    value = str(value) if type(value) is int else value
    return value if isinstance(value, str) and len(value) <= 19 and value.isascii() and value.isdigit() and 0 < int(value) < 2**63 else ''


def _stamp(value: object) -> float:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if isinstance(value, str) else float(value)
        return stamp if math.isfinite(stamp) and stamp > 0 else 0.
    except (TypeError, ValueError, OverflowError, OSError):
        return 0.


def _safe(path: Path) -> bool:
    return not any(p.is_symlink() for p in (path, *path.parents))


def _ledger(path: Path):
    if not _safe(path):
        raise ValueError('automation_history_path_unsafe')
    if not path.exists():
        return
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError('automation_history_source_too_large')
        remaining = info.st_size  # Do not chase a concurrently growing ledger.
        while remaining > 0:
            raw = stream.readline(min(remaining, 1024 * 1024 + 1))
            remaining -= len(raw)
            if len(raw) > 1024 * 1024:
                raise ValueError('automation_history_record_too_large')
            if not raw.endswith(b'\n'):
                continue  # A still-being-appended tail is not a committed record.
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(value, dict):
                yield value


def _queue(path: Path, room_id: str):
    if not _safe(path):
        raise ValueError('automation_history_queue_unsafe')
    if not path.exists():
        return
    if not _safe(path) or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('automation_history_queue_unsafe')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.15)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for row in db.execute('SELECT event_id,event_json,status,reply,reason,error_class,created_at,updated_at FROM reply_jobs'):
            try:
                event = json.loads(row['event_json'])
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict) or _id(event.get('chat_id')) != room_id:
                continue
            yield dict(row), event


def _context(value: object) -> list[dict]:
    result = []
    for row in value[:40] if isinstance(value, list) else []:
        if isinstance(row, str):
            result.append({'sender': '', 'text': _text(row, 2000), 'sent_at': 0})
        elif isinstance(row, dict):
            result.append({'sender': _text(row.get('author_nickname') or row.get('sender') or row.get('author'), 120),
                           'text': _text(row.get('message') or row.get('text') or row.get('content'), 2000),
                           'sent_at': _stamp(row.get('sent_at'))})
    return [row for row in result if row['text']]


def _item(room_id: str, event_id: str, records: dict) -> dict:
    generation = records.get('generation', {})
    delivery = records.get('delivery', {})
    job, event = records.get('queue', ({}, {}))
    reason = _text(job.get('reason') or generation.get('reason'), 240)
    is_news = (records.get('kind') == 'geeknews' or event.get('proactive_query') == 'geeknews-rss'
               or reason.split(':', 1)[0].strip() == 'geeknews_rss')
    kind = 'geeknews' if is_news else 'reply'
    reply = _text(job.get('reply') or generation.get('reply') or delivery.get('reply'))
    incoming = _text(event.get('message') or generation.get('message') or delivery.get('message'))
    queue_state = _text(job.get('status'), 80)
    status = queue_state or _text(generation.get('status') or delivery.get('status'), 80)
    # Historical ledger replies are 500-character previews. They cannot attest
    # a different full queue reply merely by sharing its prefix.
    exact_delivery = bool(_id(delivery.get('outgoing_log_id')) and reply and delivery.get('reply') == reply)
    confirmed = bool(reply and (queue_state == 'sent' or exact_delivery))
    if confirmed:
        outcome = 'confirmed'
    elif status in ('delivery_unknown', 'reconcile_required', 'sent'):
        outcome = 'unknown'
    elif status in ('poison', 'failed'):
        outcome = 'failed'
    elif status in ('cancelled', 'superseded') or (status == 'skipped' and any(part in reason for part in ('cancel', 'supersed', 'emergency', 'operator_pause'))):
        outcome = 'cancelled'
    elif status == 'skipped':
        outcome = 'skipped'
    elif status in ('pending', 'processing', 'scheduled', 'sending', 'deferred', 'detected', 'hooking', 'acknowledging', 'projection_pending'):
        outcome = 'waiting'
    else:
        outcome = 'unknown'
    at = max(_stamp(job.get('updated_at')), _stamp(generation.get('recorded_at')), _stamp(delivery.get('recorded_at')))
    context = _context(generation.get('recent_conversation') or event.get('recent_messages'))
    room = _text(event.get('chat_name') or generation.get('chat') or delivery.get('chat'), 160).strip()
    if not room:
        room = '대화방 · ' + room_id[-6:]
    articles = []
    if kind == 'geeknews':
        for line in (reply or incoming).splitlines():
            match = re.search(r'https://news\.hada\.io/topic\?id=([1-9][0-9]*)\b', line)
            if match:
                articles.append({'id': match.group(1), 'title': re.sub(r'^\s*\d+[.)]\s*', '', line[:match.start()]).strip(), 'url': match.group(0)})
    return {'id': room_id + ':' + event_id, 'event_id': event_id, 'room_id': room_id, 'room': room,
            'kind': kind, 'at': at, 'outcome': outcome, 'queue_state': queue_state,
            'confirmation': 'queue_terminal' if confirmed and queue_state == 'sent' else 'outgoing_db_row' if confirmed else '',
            'sender': _text(event.get('author_nickname') or generation.get('author'), 120),
            'incoming': incoming, 'reply': reply, 'context': context, 'articles': articles,
            'reason': reason, 'error': _text(job.get('error_class'), 160), 'model': _text(generation.get('model'), 240),
            'preview_only': not bool(job),
            'text_truncated': len(str(job.get('reply') or event.get('message') or '')) > 16_000,
            'slot': next((line.strip() for line in (reply or incoming).splitlines() if line.startswith('GeekNews TOP')), '')[:160]}


def read(state_root: Path, kind: str, query: str | None = None) -> dict:
    if kind not in ('reply', 'geeknews'):
        raise ValueError('automation_history_kind_invalid')
    options = json.loads(query) if query else {}
    if not isinstance(options, dict) or set(options) - {'limit', 'before', 'search', 'status', 'room'}:
        raise ValueError('automation_history_query_invalid')
    limit = options.get('limit', 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('automation_history_limit_invalid')
    search = options.get('search', '')
    status_filter = options.get('status', 'all')
    room_filter = options.get('room', '')
    if not isinstance(search, str) or len(search) > 200 or not isinstance(status_filter, str) or status_filter not in STATES | {'all'} or not isinstance(room_filter, str) or (room_filter and not _id(room_filter)):
        raise ValueError('automation_history_filter_invalid')
    before = options.get('before')
    if before is not None and (not isinstance(before, list) or len(before) != 2 or type(before[0]) not in (int, float) or not math.isfinite(before[0]) or before[0] < 0 or not isinstance(before[1], str) or len(before[1]) > 240):
        raise ValueError('automation_history_cursor_invalid')
    rooms_root = state_root / 'rooms'
    if not _safe(rooms_root):
        raise ValueError('automation_history_path_unsafe')
    if not rooms_root.exists():
        return {'ok': True, 'items': [], 'next': None, 'total': 0, 'partial': False}
    items, unavailable = [], 0
    for room in sorted(rooms_root.iterdir()):
        room_id = _id(room.name)
        if not room_id or (room_filter and room_filter != room_id):
            continue
        if room.is_symlink():
            unavailable += 1
            continue
        events: dict[str, dict] = {}
        try:
            for value in _ledger(room / 'reply-evidence.jsonl'):
                event_id = _text(value.get('event_id'), 200)
                if not event_id:
                    continue
                entry = events.setdefault(event_id, {})
                if _text(value.get('reason'), 240).split(':', 1)[0].strip() == 'geeknews_rss':
                    entry['kind'] = 'geeknews'
                field = 'delivery' if value.get('status') == 'sent' else 'generation'
                if _stamp(value.get('recorded_at')) >= _stamp(entry.get(field, {}).get('recorded_at')):
                    entry[field] = value
                if len(events) > MAX_EVENTS:
                    raise ValueError('automation_history_event_budget')
            for job, event in _queue(room / 'reply-queue.sqlite3', room_id):
                event_id = _text(job['event_id'], 200)
                if event_id:
                    events.setdefault(event_id, {})['queue'] = (job, event)
                if len(events) > MAX_EVENTS:
                    raise ValueError('automation_history_event_budget')
        except (OSError, sqlite3.Error, ValueError):
            unavailable += 1
        items.extend(_item(room_id, event_id, records) for event_id, records in events.items())
    needle = unicodedata.normalize('NFKC', search).casefold().strip()
    items = [item for item in items if item['kind'] == kind and (status_filter == 'all' or item['outcome'] == status_filter)
             and (not needle or needle in unicodedata.normalize('NFKC', ' '.join([item['room'], item['incoming'], item['reply'], *[r['text'] for r in item['context']]])).casefold())]
    items.sort(key=lambda item: (item['at'], item['id']), reverse=True)
    total = len(items)
    if before is not None:
        items = [item for item in items if (item['at'], item['id']) < tuple(before)]
    # Respect the native 4 MiB response fence by paging complete events, rather
    # than cutting a conversation context halfway through its row.
    page, size = [], 0
    for item in items[:limit]:
        row_bytes = len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
        if page and size + row_bytes > 3 * 1024 * 1024:
            break
        page.append(item); size += row_bytes
    more = len(items) > len(page)
    items = page
    return {'ok': True, 'items': items, 'total': total, 'next': [items[-1]['at'], items[-1]['id']] if more else None,
            'partial': unavailable > 0, 'unavailable_rooms': unavailable}
