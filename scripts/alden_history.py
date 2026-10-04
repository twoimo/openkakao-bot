"""Private text history and real DB/graph cycle receipts for Alden.

Kakao history stays in Kakao's read-only local database. Voice history stores
confirmed text only. No microphone audio, messages or credentials are sent.
"""
from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import time
import uuid
import unicodedata
from contextlib import contextmanager
from pathlib import Path

class LocalDataAccessWaiting(RuntimeError):
    pass

PHASES = frozenset(('collecting', 'graphing', 'complete', 'pending', 'paused', 'failed', 'interrupted'))

@contextmanager
def database(state_root: Path, *, write: bool = False):
    path = state_root / 'alden-history.sqlite3'
    if path.is_symlink() or state_root.is_symlink():
        raise RuntimeError('history_path_unsafe')
    if write:
        state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        db=sqlite3.connect(path, timeout=0.15)
        db.execute('PRAGMA journal_mode=WAL')  # Only this derived history DB.
        db.executescript('''
        CREATE TABLE IF NOT EXISTS voice_sessions(id TEXT PRIMARY KEY,started_at REAL,title TEXT,source TEXT);
        CREATE TABLE IF NOT EXISTS voice_messages(session_id TEXT,turn_id INTEGER,role TEXT,content TEXT,created_at REAL,context_version INTEGER,state TEXT,PRIMARY KEY(session_id,turn_id,role));
        CREATE TABLE IF NOT EXISTS db_cycles(id TEXT PRIMARY KEY,started_at REAL,updated_at REAL,phase TEXT,pid INTEGER,details TEXT);
        CREATE TABLE IF NOT EXISTS db_steps(id INTEGER PRIMARY KEY,cycle_id TEXT,at REAL,phase TEXT,details TEXT);
        CREATE INDEX IF NOT EXISTS voice_message_page ON voice_messages(session_id,turn_id);
        CREATE INDEX IF NOT EXISTS db_step_page ON db_steps(id);
        ''')
        if 'pid_start' not in {row[1] for row in db.execute('PRAGMA table_info(db_cycles)')}:
            db.execute('ALTER TABLE db_cycles ADD COLUMN pid_start TEXT NOT NULL DEFAULT ""')
    else:
        if not path.exists():
            yield None
            return
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0.15)
        db.execute('PRAGMA query_only=ON')
    db.row_factory=sqlite3.Row
    try:
        yield db
        if write: db.commit()
    finally:
        db.close()

def record_voice(state_root: Path, session: str, turn: int, role: str, content: str,
                 context_version: int, *, source: str = 'microphone', state: str = 'confirmed') -> None:
    if role not in ('user','assistant') or not isinstance(content,str) or not content:
        raise ValueError('voice_history_record_invalid')
    now=time.time()
    with database(state_root,write=True) as db:
        db.execute('INSERT OR IGNORE INTO voice_sessions VALUES(?,?,?,?)',(session,now,content[:80],source))
        db.execute('INSERT OR IGNORE INTO voice_messages VALUES(?,?,?,?,?,?,?)',(session,turn,role,content,now,context_version,state))

def context_coverage(state_root: Path) -> dict:
    """Counts in the existing search corpus, separate from raw Kakao collection."""
    from auto_reply_knowledge_graph import _index_db_path
    path = _index_db_path(state_root)
    if not path.is_file() or path.is_symlink():
        return {}
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.15) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        try:
            row = db.execute('SELECT COUNT(*),COUNT(DISTINCT chat) FROM context_messages').fetchone()
        except sqlite3.OperationalError:
            return {}
        return {'indexed_messages': row[0], 'indexed_rooms': row[1]}

def cycle_step(state_root: Path, cycle: str, phase: str, **details) -> None:
    if phase not in PHASES: raise ValueError('db_cycle_phase_invalid')
    # Receipts contain status/counts, never message bodies or private DB keys.
    safe={k:v for k,v in details.items() if k in ('rooms','messages','nodes','changed','pending','conflicts','reason','indexed_messages','indexed_rooms','corpus_messages','corpus_pending') and isinstance(v,(int,str,bool))}
    encoded=json.dumps(safe,ensure_ascii=False);now=time.time()
    with database(state_root,write=True) as db:
        signature=subprocess.run(['ps','-p',str(os.getpid()),'-o','lstart='],capture_output=True,text=True,timeout=2).stdout.strip()
        db.execute('INSERT OR IGNORE INTO db_cycles(id,started_at,updated_at,phase,pid,details,pid_start) VALUES(?,?,?,?,?,?,?)',(cycle,now,now,phase,os.getpid(),encoded,signature))
        db.execute('UPDATE db_cycles SET updated_at=?,phase=?,details=? WHERE id=?',(now,phase,encoded,cycle))
        db.execute('INSERT INTO db_steps(cycle_id,at,phase,details) VALUES(?,?,?,?)',(cycle,now,phase,encoded))

def _local_cli(binary: Path, args: list[str]) -> dict:
    if not binary.is_absolute() or binary.is_symlink() or not binary.is_file():
        raise RuntimeError('history_cli_invalid')
    result=subprocess.run([str(binary),*args,'--json'],capture_output=True,text=True,timeout=90 if args and args[0]=='local-db-collect' else 20)
    if result.returncode:
        if 'local_account_metadata_access_waiting' in result.stderr: raise LocalDataAccessWaiting('local_account_metadata_access_waiting')
        raise RuntimeError('local_history_unavailable')
    if len(result.stdout.encode())>8*1024*1024: raise RuntimeError('history_page_too_large')
    data=json.loads(result.stdout)
    if not isinstance(data,dict): raise RuntimeError('history_response_invalid')
    return data

def read(state_root: Path, binary: Path, action: str, query: str | None = None, chat_id: str | None = None) -> dict:
    if action in ('reply-history', 'geeknews-history'):
        from alden_automation_history import read as automation_history
        return automation_history(state_root, 'reply' if action == 'reply-history' else 'geeknews', query)
    options=json.loads(query) if query else {}
    if not isinstance(options,dict): raise ValueError('history_cursor_invalid')
    limit=options.get('limit',100)
    if type(limit) is not int or not 1<=limit<=200: raise ValueError('history_limit_invalid')
    if action=='history-rooms':
        return room_display_aliases(state_root, _local_cli(binary,['local-history-rooms']))
    if action=='history-messages':
        if not chat_id or not chat_id.isascii() or not chat_id.isdigit() or not 0<int(chat_id)<2**63:
            raise ValueError('history_chat_invalid')
        args=['local-history',chat_id,'-n',str(limit)]
        for name in ('anchor','before'):
            value=options.get(name)
            if value is not None:
                if not isinstance(value,str) or not value.isascii() or not value.isdigit() or not 0<=int(value)<2**63:
                    raise ValueError('history_cursor_invalid')
                args += ['--'+name,value]
        return {'ok':True,**_local_cli(binary,args)}
    with database(state_root) as db:
        if db is None: return {'ok':True,'items':[],'next':None}
        if action=='voice-history-sessions':
            rows=db.execute('SELECT s.*,COUNT(m.role) AS messages,MAX(m.created_at) AS updated_at FROM voice_sessions s LEFT JOIN voice_messages m ON m.session_id=s.id GROUP BY s.id ORDER BY s.started_at DESC').fetchall()
            return {'ok':True,'items':[dict(row) for row in rows]}
        if action=='voice-history-messages':
            if not isinstance(chat_id,str) or len(chat_id)>128: raise ValueError('voice_session_invalid')
            before=options.get('before')
            if before is not None and (type(before) is not int or before<=0): raise ValueError('voice_cursor_invalid')
            turns=db.execute('SELECT DISTINCT turn_id FROM voice_messages WHERE session_id=? AND (? IS NULL OR turn_id<?) ORDER BY turn_id DESC LIMIT ?', (chat_id,before,before,limit+1)).fetchall()
            more=len(turns)>limit;turns=turns[:limit]
            if not turns: return {'ok':True,'items':[],'next':None}
            low=turns[-1]['turn_id'];high=turns[0]['turn_id']
            rows=db.execute('SELECT * FROM voice_messages WHERE session_id=? AND turn_id BETWEEN ? AND ? ORDER BY turn_id,CASE role WHEN "user" THEN 0 ELSE 1 END',(chat_id,low,high)).fetchall()
            return {'ok':True,'items':[dict(row) for row in rows],'next':low if more else None}
        if action=='db-sync-history':
            before=options.get('before')
            if before is not None and (type(before) is not int or before<=0): raise ValueError('db_history_cursor_invalid')
            rows=db.execute('SELECT * FROM db_steps WHERE (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?', (before,before,limit+1)).fetchall()
            current=db.execute('SELECT * FROM db_cycles ORDER BY started_at DESC LIMIT 1').fetchone()
            items=[]
            for row in rows[:limit]:
                item=dict(row);item['details']=json.loads(item['details']);items.append(item)
            current=dict(current) if current else None
            if current:
                current['details']=json.loads(current['details'])
                if current['phase'] in ('collecting','graphing'):
                    live=subprocess.run(['ps','-p',str(current['pid']),'-o','lstart='],capture_output=True,text=True,timeout=2)
                    if live.returncode or not current['pid_start'] or live.stdout.strip()!=current['pid_start']:current['phase']='interrupted'
            return {'ok':True,'items':items,'current':current,'next':items[-1]['id'] if len(rows)>limit else None}
    raise ValueError('history_action_invalid')


def room_display_aliases(state_root: Path, data: dict) -> dict:
    """Use grounded aliases for unnamed rooms without renaming Kakao data."""
    path=state_root/'knowledge/room-aliases.json'
    if not path.is_file() or path.is_symlink() or path.parent.is_symlink() or path.stat().st_size>1024*1024:return data
    try: aliases=json.loads(path.read_text())
    except (OSError,ValueError):return data
    if aliases.get('schema_version')!=1 or aliases.get('account')!=data.get('account'):return data
    names=aliases.get('rooms')
    if not isinstance(names,dict):return data
    rows=[]
    for row in data.get('rooms',[]):
        if not isinstance(row,dict):continue
        title=''.join(unicodedata.normalize('NFKC',str(row.get('chat_name') or '')).split())
        display=names.get(str(row.get('chat_id')),{}).get('label')
        if title in ('','이름없는채팅방','이름없는대화방','(알수없음)','제목미확인') and isinstance(display,str) and display:
            row={**row,'original_chat_name':row.get('chat_name'),'chat_name':display,'display_alias':True}
        rows.append(row)
    return {**data,'rooms':rows}
