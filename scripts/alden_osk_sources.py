"""Private OSK _sources records for imported Kakao corpus, never agent rounds.

Every published row is retained. Immutable text chunks supply exact heading
coordinates; source IDs and original roles stay separate from agent authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import statistics
import math
import unicodedata
import re
from array import array
from contextlib import closing
from pathlib import Path

SCHEMA = 1
CHUNK_ROWS = 512
CHUNK_BYTES = 1024 * 1024


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(path: Path, *, directory: bool = False) -> Path:
    path = path.absolute()
    if '..' in path.parts:
        raise RuntimeError('osk_source_path_invalid')
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise RuntimeError('osk_source_symlink')
    if directory:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not (path.is_dir() if directory else path.is_file()):
        raise RuntimeError('osk_source_path_invalid')
    return path


def _json_value(value):
    # SQLite blobs remain lossless and explicitly typed, not fabricated speech.
    return {'sqlite_blob_hex': value.hex()} if isinstance(value, bytes) else value


def _immutable(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise RuntimeError('osk_source_symlink')
    if path.exists():
        if path.read_bytes() != data:
            raise RuntimeError('osk_source_bytes_changed')
        return
    fd, temporary = tempfile.mkstemp(prefix='.raw-source-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        # Exclusive publication: an existing source is never replaced.
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != data:
            raise RuntimeError('osk_source_publication_conflict')
    finally:
        Path(temporary).unlink(missing_ok=True)


def export_corpus(snapshot: Path, vault: Path, generation: str, filter_text,
                  *, cancelled=lambda: False, progress=lambda count: None) -> dict:
    """Export the full consistent published corpus into an isolated generation.

    Callers stage and verify before making this generation visible. This does
    not contact the original Kakao database or write a knowledge/approval node.
    """
    if len(generation) not in (32, 64) or any(c not in '0123456789abcdef' for c in generation):
        raise ValueError('osk_source_generation_invalid')
    snapshot = _safe(snapshot); vault = _safe(vault, directory=True)
    home = _safe(vault / '_sources' / 'kakao' / generation, directory=True)
    index_path = home / 'source-index.sqlite3'
    if index_path.exists():
        raise RuntimeError('osk_source_generation_exists')
    records = []; entries = []; size = 0; count = 0; chunks = 0
    with sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True, timeout=.2) as source, sqlite3.connect(index_path) as index:
        source.execute('PRAGMA query_only=ON')
        source.set_progress_handler(lambda: int(cancelled()), 1000)
        account = source.execute("SELECT value FROM corpus_meta WHERE key='account'").fetchone()
        if account is None or len(account[0]) != 64 or any(c not in '0123456789abcdef' for c in account[0]):
            raise RuntimeError('osk_source_account_invalid')
        account = account[0]
        index.execute('CREATE TABLE source_refs(source_id TEXT,row_id INTEGER,record_sha256 TEXT,coordinate TEXT,PRIMARY KEY(source_id,row_id))')
        index.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')

        def flush():
            nonlocal records, entries, size, chunks
            if not records: return
            data = ''.join(records).encode('utf-8'); filename = _hash(data) + '.txt'
            _immutable(home / filename, data)
            relative = (home / filename).relative_to(vault).as_posix()
            index.executemany('INSERT INTO source_refs VALUES(?,?,?,?)', [(key, row_id, sha, '[[' + relative + '#' + heading + ']]') for key, row_id, sha, heading in entries])
            index.commit(); chunks += 1; records = []; entries = []; size = 0

        cursor = source.execute('SELECT * FROM alden_messages ORDER BY id')
        columns = [column[0] for column in cursor.description]
        for values in cursor:
            if cancelled(): raise RuntimeError('osk_source_cancelled')
            row = dict(zip(columns, map(_json_value, values)))
            room, log = str(row.get('chat_id', '')), str(row.get('log_id', ''))
            source_id = 'kakao:' + account + ':room:' + room + ':log:' + log
            # Preserve malformed/duplicate source identities as distinct rows.
            # A consumer receives every coordinate and cannot silently merge.
            role = 'outgoing_unclassified' if row.get('is_self') else 'peer_history' if str(row.get('author_id', '')).isdigit() and int(row['author_id']) > 0 else 'system_history'
            raw = json.dumps({'record_kind': 'external_kakao_message', 'source_id': source_id,
                              'source_role': role, 'original': row}, ensure_ascii=False, sort_keys=True, allow_nan=False)
            filtered = filter_text(raw)[0]
            sha = _hash(filtered.encode('utf-8'))
            heading = 'message-' + str(row['id']) + '-' + sha
            block = '## ' + heading + '\n\n```json\n' + filtered + '\n```\n\n'
            if records and (len(records) >= CHUNK_ROWS or size + len(block.encode('utf-8')) > CHUNK_BYTES): flush()
            records.append(block); entries.append((source_id, row['id'], sha, heading)); size += len(block.encode('utf-8')); count += 1
            if count % 10000 == 0: progress(count)
        flush()
        metadata = {'schema': SCHEMA, 'complete': True, 'account': account, 'records': count, 'chunks': chunks,
                    'source_roles_preserved': True, 'agent_rounds_fabricated': False,
                    'content_truncated': False, 'published': False}
        index.executemany('INSERT INTO metadata VALUES(?,?)', [(key, json.dumps(value)) for key, value in metadata.items()])
        index.commit()
    index_path.chmod(0o600)
    progress(count)
    return metadata


def coordinates(index_path: Path, source_ids: list[str]) -> list[str]:
    """Resolve every exact source match; never turn an ambiguous ID into one."""
    index_path = _safe(index_path)
    with sqlite3.connect(index_path.as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
        db.execute('PRAGMA query_only=ON')
        complete = db.execute("SELECT value FROM metadata WHERE key='complete'").fetchone()
        if complete is None or json.loads(complete[0]) is not True:
            raise RuntimeError('osk_source_generation_incomplete')
        return list(dict.fromkeys(coordinate for key in source_ids for coordinate, in
                                 db.execute('SELECT coordinate FROM source_refs WHERE source_id=? ORDER BY row_id', (key,))))


def export_metadata(snapshot: Path, vault: Path, generation: str, filter_text) -> dict:
    """Add exact room/publication metadata sources without editing message chunks."""
    if not isinstance(generation, str) or len(generation) not in (32, 64) or any(c not in '0123456789abcdef' for c in generation):
        raise ValueError('osk_source_generation_invalid')
    snapshot = _safe(snapshot); vault = _safe(vault, directory=True)
    home = _safe(vault / '_sources' / 'kakao' / generation, directory=True)
    index_path = _safe(home / 'source-index.sqlite3')
    coordinates(index_path, [])  # Partial imports cannot gain metadata authority.
    blocks = []; entries = []
    with closing(sqlite3.connect(snapshot.as_uri()+'?mode=ro', uri=True, timeout=.2)) as source:
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        account = source.execute("SELECT value FROM corpus_meta WHERE key='account'").fetchone()[0]
        for table in ('alden_rooms', 'corpus_meta', 'alden_authors'):
            if not source.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone(): continue
            cursor = source.execute('SELECT * FROM ' + table + ' ORDER BY 1')
            columns = [column[0] for column in cursor.description]
            for values in cursor:
                original = dict(zip(columns, map(_json_value, values)))
                suffix = ':room:'+str(original['chat_id']) if table=='alden_rooms' else ':author:'+str(original['author_id']) if table=='alden_authors' else ':publication:'+str(original['key'])
                key = 'kakao:'+account+suffix
                text = filter_text(json.dumps({'record_kind':'external_corpus_metadata','source_id':key,'source_role':'source_metadata','table':table,'original':original},ensure_ascii=False,sort_keys=True,allow_nan=False))[0]
                sha = _hash(text.encode('utf-8')); heading = 'metadata-'+sha
                blocks.append('## '+heading+'\n\n```json\n'+text+'\n```\n\n')
                entries.append((key, -len(entries)-1, sha, heading))
    data = ''.join(blocks).encode('utf-8'); path = home/(_hash(data)+'.txt')
    _immutable(path, data); relative = path.relative_to(vault).as_posix()
    with sqlite3.connect(index_path) as index:
        index.executemany('INSERT OR IGNORE INTO source_refs VALUES(?,?,?,?)',[(key,row_id,sha,'[['+relative+'#'+heading+']]') for key,row_id,sha,heading in entries])
    return {'metadata_records':len(entries),'coordinate_file':relative}


def quality_report(snapshot: Path, flags_path: Path | None = None) -> dict:
    """Quantify source quality without deleting or rewriting any Raw row.

    Repeated text under different log IDs remains legitimate distinct history.
    Length outliers use a robust log-length score; they are flags, not evidence
    that an original message is false. No private text leaves this report.
    """
    snapshot = _safe(snapshot)
    with closing(sqlite3.connect(snapshot.as_uri()+'?mode=ro', uri=True, timeout=.2)) as source:
        source.execute('PRAGMA query_only=ON'); source.execute('BEGIN')
        columns = {row[1] for row in source.execute('PRAGMA table_info(alden_messages)')}
        digest_expr = "COALESCE(NULLIF(digest,''),message)" if 'digest' in columns else 'message'
        duplicates = source.execute('SELECT COUNT(*),COALESCE(SUM(n-1),0),COALESCE(SUM(CASE WHEN variants>1 THEN 1 ELSE 0 END),0) FROM (SELECT COUNT(*) n,COUNT(DISTINCT '+digest_expr+') variants FROM alden_messages GROUP BY chat_id,log_id HAVING COUNT(*)>1)').fetchone()
        labels = [str(row[0] or '') for row in source.execute('SELECT label FROM alden_rooms')] if source.execute("SELECT 1 FROM sqlite_master WHERE name='alden_rooms'").fetchone() else []
        normalized = [' '.join(unicodedata.normalize('NFKC', label).split()) for label in labels]
        ids, message_lengths = array('Q'), array('I')
        for row_id,length in source.execute('SELECT id,length(message) FROM alden_messages ORDER BY id'):
            ids.append(row_id);message_lengths.append(int(length or 0))
        # The source scan is separate from the interactive sync path.
        logs = [math.log1p(length) for length in message_lengths if length > 0]
        median = statistics.median(logs) if logs else 0.
        mad = statistics.median(abs(value-median) for value in logs) if logs else 0.
        scale = max(mad,.01)
        flags = [(row_id,.67448975*abs(math.log1p(length)-median)/scale) for row_id,length in zip(ids,message_lengths) if length>0 and .67448975*abs(math.log1p(length)-median)/scale>6]
        outliers = len(flags)
        if flags_path is not None:
            _safe(flags_path.parent,directory=True)
            if flags_path.is_symlink() or flags_path.exists(): raise RuntimeError('osk_quality_target_exists')
            with closing(sqlite3.connect(flags_path)) as quality:
                quality.execute('CREATE TABLE source_flags(row_id INTEGER PRIMARY KEY,source_id TEXT,kind TEXT,score REAL)')
                account=source.execute("SELECT value FROM corpus_meta WHERE key='account'").fetchone()[0]
                for row_id,score in flags:
                    room,log=source.execute('SELECT chat_id,log_id FROM alden_messages WHERE id=?',(row_id,)).fetchone()
                    quality.execute('INSERT INTO source_flags VALUES(?,?,?,?)',(row_id,f'kakao:{account}:room:{room}:log:{log}','length_outlier',score))
                quality.commit()
            flags_path.chmod(0o600)
        return {'schema_version':1,'raw_rows':len(message_lengths), 'source_identity_duplicate_groups':int(duplicates[0]),
                'duplicate_identity_extra_rows':int(duplicates[1]),'conflicting_identity_groups':int(duplicates[2]),
                'unchanged_raw_rows':len(message_lengths),'raw_rows_deleted':0,
                'rooms':len(labels),'normalized_room_labels':sum(a!=b for a,b in zip(labels,normalized)),
                'unnamed_rooms':sum(not name or name=='이름 없는 채팅방' for name in normalized),
                'length_outliers_flagged':outliers,'length_outlier_rule':'0.67448975*abs(log1p(length)-median)/max(MAD,0.01) > 6',
                'log_length_median':median,'log_length_mad':mad,'messages_over_64k_characters':sum(length>65536 for length in message_lengths),
                'identity_policy':'account,room_id,log_id; identical text under distinct IDs is retained'}


def ground_graph(state_root: Path, graph: dict, filter_text) -> dict:
    """Archive exact referenced original records, then attach OSK coordinates.

    Enabled only after the initial full private import has been verified. Each
    refresh reads a bounded set of source IDs and appends immutable versions;
    it does not export two million rows on the interactive path. No user/agent
    conversation rounds or approval fields are synthesized from Kakao history.
    """
    config_path = state_root / 'knowledge/osk/raw-sources.json'
    if not config_path.exists(): return graph
    config = json.loads(_safe(config_path).read_text())
    if config.get('schema_version') != 1 or config.get('enabled') is not True:
        raise RuntimeError('osk_raw_sources_configuration_invalid')
    from auto_reply_knowledge_graph import _index_db_path
    source_path = _safe(_index_db_path(state_root))
    vault = _safe(state_root / 'knowledge/osk/vault', directory=True)
    generation = config.get('base_generation', '')
    if not isinstance(generation,str) or not re.fullmatch('[0-9a-f]{32}|[0-9a-f]{64}',generation):
        raise RuntimeError('osk_source_base_generation_invalid')
    coordinates(vault/'_sources/kakao'/generation/'source-index.sqlite3',[])
    home = _safe(vault / '_sources/kakao/current', directory=True)
    cache_path = home / 'references.sqlite3'
    if cache_path.is_symlink(): raise RuntimeError('osk_source_symlink')
    node_keys = {}
    for node in graph.get('nodes', []):
        keys = list((node.get('evidence') or {}).get('source_event_ids') or [])[:16]
        identity = str(node.get('id', ''))
        if identity.startswith('chat:kakao:'): keys.append(identity[5:])
        match = re.fullmatch('person:kakao:([0-9a-f]{64}):actor:([1-9][0-9]*)', identity)
        if match: keys.append('kakao:'+match[1]+':author:'+match[2])
        node_keys[identity] = list(dict.fromkeys(str(key) for key in keys))
    wanted = {key for keys in node_keys.values() for key in keys}
    if len(wanted) > 3000: raise RuntimeError('osk_source_reference_budget')
    packets = {}; conflicts = set()
    # Only requested ledger IDs are read. Different generation/delivery rows
    # remain distinct records in an event packet, preserving their actual roles.
    ledger_keys = {key for key in wanted if key.startswith('db:')}
    for ledger in sorted((state_root/'rooms').glob('*/reply-evidence.jsonl')) if ledger_keys else []:
        ledger = _safe(ledger)
        if ledger.stat().st_size > 64*1024*1024: raise RuntimeError('osk_source_ledger_budget')
        with ledger.open() as stream:
            for line in stream:
                if len(line) > 1024*1024: raise RuntimeError('osk_source_record_budget')
                try: record=json.loads(line)
                except ValueError: continue
                if isinstance(record,dict) and record.get('event_id') in ledger_keys:
                    packets.setdefault(record['event_id'],[]).append({'record_kind':'external_automation_receipt','source_role':'automation_history','original':record})
    with closing(sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as source, closing(sqlite3.connect(cache_path)) as cache:
        source.execute('PRAGMA query_only=ON'); source.execute('BEGIN'); source.row_factory=sqlite3.Row
        cache.execute('CREATE TABLE IF NOT EXISTS current_refs(source_id TEXT PRIMARY KEY,fingerprint TEXT,coordinate TEXT)')
        account=source.execute("SELECT value FROM corpus_meta WHERE key='account'").fetchone()[0]
        if config.get('base_account') != account: raise RuntimeError('osk_source_account_changed')
        for key in sorted(wanted):
            match=re.fullmatch('kakao:([0-9a-f]{64}):room:([1-9][0-9]*):log:([1-9][0-9]*)',key)
            db_match=re.fullmatch('db:([1-9][0-9]*):([1-9][0-9]*)',key)
            if match and match[1]==account or db_match:
                room,log=(match[2],match[3]) if match else (db_match[1],db_match[2])
                records=source.execute('SELECT * FROM alden_messages WHERE chat_id=? AND log_id=?',(room,log)).fetchall()
                variants={json.dumps({field:dict(row).get(field) for field in ('author_id','message','attachment','message_type','sent_at','is_self')},sort_keys=True,default=_json_value) for row in records}
                if len(variants)>1: conflicts.add(key)
                for row in records:
                    original=dict(row);role='system_history' if str(original.get('author_id','0'))=='0' else 'outgoing_unclassified' if original.get('is_self') else 'peer_history'
                    packets.setdefault(key,[]).append({'record_kind':'external_kakao_message','source_role':role,'original':original})
            else:
                room=re.fullmatch('kakao:([0-9a-f]{64}):room:([1-9][0-9]*)',key)
                actor=re.fullmatch('kakao:([0-9a-f]{64}):author:([1-9][0-9]*)',key)
                if room and room[1]==account or actor and actor[1]==account:
                    table,column,identity=('alden_rooms','chat_id',room[2]) if room else ('alden_authors','author_id',actor[2])
                    for row in source.execute('SELECT * FROM '+table+' WHERE '+column+'=?',(identity,)):
                        packets.setdefault(key,[]).append({'record_kind':'external_corpus_metadata','source_role':'source_metadata','table':table,'original':dict(row)})
        references={}
        for key,records in packets.items():
            filtered=json.loads(filter_text(json.dumps(records,ensure_ascii=False,sort_keys=True,default=_json_value))[0])
            # A periodic 'seen' timestamp is not a new semantic source version.
            semantic=[{**record,'original':{name:value for name,value in record['original'].items() if name!='seen'}} for record in filtered]
            fingerprint=_hash(json.dumps(semantic,ensure_ascii=False,sort_keys=True).encode())
            existing=cache.execute('SELECT fingerprint,coordinate FROM current_refs WHERE source_id=?',(key,)).fetchone()
            if existing and existing[0]==fingerprint:
                references[key]=existing[1];continue
            heading='source-'+fingerprint
            payload=json.dumps({'source_id':key,'records':filtered},ensure_ascii=False,sort_keys=True)
            data=('## '+heading+'\n\n```json\n'+payload+'\n```\n').encode()
            path=home/(_hash(data)+'.txt');_immutable(path,data)
            coordinate='[['+path.relative_to(vault).as_posix()+'#'+heading+']]'
            cache.execute('INSERT INTO current_refs VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET fingerprint=excluded.fingerprint,coordinate=excluded.coordinate',(key,fingerprint,coordinate))
            references[key]=coordinate
        cache.commit()
    cache_path.chmod(0o600)
    nodes=[];unresolved=0
    for node in graph.get('nodes',[]):
        keys=node_keys[str(node['id'])];refs=list(dict.fromkeys(references[key] for key in keys if key in references))
        if not refs or conflicts.intersection(keys):
            unresolved+=1;continue  # Keep prior files; retract unsupported learned facts.
        nodes.append({**node,'raw_sources':refs})
    active={str(node['id']) for node in nodes}
    edges=[edge for edge in graph.get('edges',[]) if edge.get('source') in active and edge.get('target') in active]
    return {**graph,'nodes':nodes,'node_count':len(nodes),'grounded_nodes':len(nodes),'edges':edges,'edge_count':len(edges),
            'raw_grounding':{'grounded_nodes':len(nodes),'withheld_nodes':unresolved,'source_records':len(references),'conflicting_source_ids':len(conflicts)}}
