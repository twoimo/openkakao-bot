"""Complete private Raw catch-up against immutable published Alden versions.

Only a compact digest/identity index is retained; source databases are opened
read-only. Changed payloads and removals append immutable source records. An
unchanged publication costs no corpus scan or new Raw files.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from alden_osk_sources import _safe, _immutable, _hash, _json_value


def _readonly(path: Path):
    db=sqlite3.connect(_safe(path).as_uri()+'?mode=ro',uri=True,timeout=.2)
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN');db.row_factory=sqlite3.Row
    return db


def _identity(account: str, row) -> str:
    return f"kakao:{account}:room:{row['chat_id']}:log:{row['log_id']}"


def _payload(row, account):
    original={key:_json_value(value) for key,value in dict(row).items()}
    role='system_history' if str(original.get('author_id','0'))=='0' else 'outgoing_unclassified' if original.get('is_self') else 'peer_history'
    return {'record_kind':'external_kakao_message','source_id':_identity(account,row),'source_role':role,'original':original}


def _fingerprint(row):
    # The producer digest covers message, author/name, attachment, type/time.
    # Add direction explicitly: producer digest does not include is_self.
    value=row['digest']
    return bytes(value) if isinstance(value,(bytes,bytearray)) else str(value).encode()


def capture(state_root: Path, filter_text, *, cancelled=lambda:False) -> dict:
    started=time.perf_counter();home=state_root/'knowledge/osk';config=json.loads(_safe(home/'raw-sources.json').read_text())
    if config.get('schema_version')!=1 or config.get('enabled') is not True:raise RuntimeError('osk_delta_configuration_invalid')
    from auto_reply_knowledge_graph import _index_db_path
    vault=_safe(home/'vault',directory=True);archive=_safe(vault/'_sources/kakao/deltas',directory=True)
    cache_path=archive/'versions.sqlite3'
    if cache_path.is_symlink():raise RuntimeError('osk_delta_cache_unsafe')
    with closing(_readonly(_index_db_path(state_root))) as source, closing(sqlite3.connect(cache_path,timeout=.2)) as cache:
        meta=dict(source.execute('SELECT key,value FROM corpus_meta'))
        account=meta.get('account');snapshot=meta.get('snapshot')
        if account!=config.get('base_account') or not snapshot or meta.get('complete')!='1':raise RuntimeError('osk_delta_source_not_complete')
        source.set_progress_handler(lambda:int(cancelled()),1000)
        cache.execute('CREATE TABLE IF NOT EXISTS versions(row_id INTEGER PRIMARY KEY,chat_id TEXT,log_id TEXT,digest BLOB,self INTEGER)')
        cache.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)')
        cache.execute('CREATE TABLE IF NOT EXISTS metadata_versions(source_id TEXT PRIMARY KEY,fingerprint TEXT)')
        saved=dict(cache.execute('SELECT key,value FROM metadata'))
        if saved.get('account') and saved['account']!=account:raise RuntimeError('osk_delta_account_changed')
        if saved.get('snapshot')==snapshot:
            return {'ok':True,'state':'unchanged','snapshot':snapshot,'rows':int(saved['rows']),'changed':0,'removed':0,'chunks':0,'elapsed_ms':(time.perf_counter()-started)*1000}
        if not saved.get('initialized'):
            baseline_path=config.get('base_snapshot')
            if not isinstance(baseline_path,str):raise RuntimeError('osk_delta_base_snapshot_required')
            with closing(_readonly(Path(baseline_path))) as base:
                base_meta=dict(base.execute('SELECT key,value FROM corpus_meta'))
                if base_meta.get('account')!=account:raise RuntimeError('osk_delta_baseline_account_changed')
                rows=base.execute('SELECT id,chat_id,log_id,digest,is_self FROM alden_messages')
                cache.executemany('INSERT INTO versions VALUES(?,?,?,?,?)',rows)
            cache.executemany('INSERT INTO metadata VALUES(?,?)',[('account',account),('initialized','1')]);cache.commit()
        source.execute('ATTACH DATABASE ? AS previous',(cache_path.as_uri()+'?mode=ro',))
        blocks=[];updates=[];deletions=[];metadata_updates=[];chunks=changed=removed=0;size=0
        def append(record):
            nonlocal blocks,size
            if cancelled():raise RuntimeError('osk_delta_cancelled')
            payload=filter_text(json.dumps(record,ensure_ascii=False,sort_keys=True,default=_json_value))[0]
            sha=_hash(payload.encode());blocks.append('## source-'+sha+'\n\n```json\n'+payload+'\n```\n\n');size+=len(blocks[-1].encode())
            if len(blocks)>=512 or size>=1024*1024:flush()
        def flush():
            nonlocal blocks,size,chunks
            if not blocks:return
            data=''.join(blocks).encode();_immutable(archive/(_hash(data)+'.txt'),data);chunks+=1;blocks=[];size=0
        # Identity and direction guard against row-ID reuse and role changes.
        sql='SELECT n.* FROM alden_messages n LEFT JOIN previous.versions p ON n.id=p.row_id WHERE p.row_id IS NULL OR n.chat_id!=p.chat_id OR n.log_id!=p.log_id OR n.digest!=p.digest OR n.is_self!=p.self ORDER BY n.id'
        for row in source.execute(sql):
            append(_payload(row,account));changed+=1;updates.append((row['id'],row['chat_id'],row['log_id'],_fingerprint(row),row['is_self']))
        for row in source.execute('SELECT p.* FROM previous.versions p LEFT JOIN alden_messages n ON n.id=p.row_id WHERE n.id IS NULL OR n.chat_id!=p.chat_id OR n.log_id!=p.log_id ORDER BY p.row_id'):
            key=_identity(account,row);append({'record_kind':'external_source_removed','source_role':'source_state','source_id':key,'snapshot':snapshot});removed+=1;deletions.append((row['row_id'],))
        for table,column,kind in [('alden_rooms','chat_id','room'),('alden_authors','author_id','author')]:
            for row in source.execute('SELECT * FROM '+table+' ORDER BY '+column):
                key=f'kakao:{account}:{kind}:{row[column]}';record={'record_kind':'external_corpus_metadata','source_role':'source_metadata','source_id':key,'table':table,'original':dict(row)}
                fingerprint=_hash(filter_text(json.dumps(record,ensure_ascii=False,sort_keys=True,default=_json_value))[0].encode())
                old=cache.execute('SELECT fingerprint FROM metadata_versions WHERE source_id=?',(key,)).fetchone()
                if old is None or old[0]!=fingerprint:append(record);metadata_updates.append((key,fingerprint))
        flush()
        if cancelled():raise RuntimeError('osk_delta_cancelled')
        total=source.execute('SELECT COUNT(*) FROM alden_messages').fetchone()[0]
        source.rollback()  # Release the attached index's read lock before writing it.
        # Files are durable before the single version-index commit. Interrupted
        # writes can leave recoverable unreferenced chunks, never advance state.
        cache.execute('BEGIN IMMEDIATE');cache.executemany('DELETE FROM versions WHERE row_id=?',deletions)
        cache.executemany('INSERT INTO versions VALUES(?,?,?,?,?) ON CONFLICT(row_id) DO UPDATE SET chat_id=excluded.chat_id,log_id=excluded.log_id,digest=excluded.digest,self=excluded.self',updates)
        cache.executemany('INSERT INTO metadata_versions VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET fingerprint=excluded.fingerprint',metadata_updates)
        for key,value in [('snapshot',snapshot),('rows',str(total)),('captured_at',str(int(time.time())))]:cache.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
        cache.commit()
    cache_path.chmod(0o600)
    return {'ok':True,'state':'captured','snapshot':snapshot,'rows':total,'changed':changed,'removed':removed,'metadata_changed':len(metadata_updates),'chunks':chunks,'elapsed_ms':(time.perf_counter()-started)*1000}
