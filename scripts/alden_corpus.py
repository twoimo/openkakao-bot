"""Read the published local corpus. History is quoted evidence, never input."""
from __future__ import annotations
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

def resolve_room(root: Path, query: str) -> dict:
    """Resolve exact visible names only; never pick between equal names."""
    from auto_reply_knowledge_graph import _index_db_path, _corpus_room_displays, _room_titles, _identity_label
    path=_index_db_path(root)
    if not path.is_file():return {'state':'none','chat_id':''}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        db.execute('PRAGMA query_only=ON')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='alden_rooms'").fetchone():return {'state':'none','chat_id':''}
        needle=_identity_label(query,limit=None).casefold().replace(' ','')
        found=[]
        labels=dict(db.execute('SELECT chat,label FROM alden_rooms'))
        displays=_corpus_room_displays(labels,_room_titles(root))
        for key,info in displays.items():
            for label in info['aliases']:
                if label==key:continue
                name=_identity_label(label).casefold().replace(' ','')
                if len(name)>=2 and name in needle:found.append((len(name),key.rsplit(':',1)[-1]))
        if not found:return {'state':'none','chat_id':''}
        longest=max(size for size,_ in found);ids={key for size,key in found if size==longest}
        return {'state':'resolved' if len(ids)==1 else 'ambiguous','chat_id':next(iter(ids)) if len(ids)==1 else '', 'matches':len(ids)}

def search(root: Path, query: str, *, chat_id: str = '', author_id: str = '', limit: int = 6, expected_account: str = '', cancelled=None, time_from=None, time_to=None) -> dict:
    if not isinstance(query,str) or len(query)>2048 or not 1<=limit<=20:
        raise ValueError('corpus_query_invalid')
    for value in (chat_id,author_id):
        if value and (not value.isascii() or not value.isdigit() or not 0<int(value)<2**63):
            raise ValueError('corpus_scope_invalid')
    from auto_reply_knowledge_graph import _index_db_path, _message_datetime_kst
    bounds=[]
    for value in (time_from,time_to):
        parsed=_message_datetime_kst(value) if value is not None else None
        if value is not None and parsed is None:raise ValueError('corpus_time_scope_invalid')
        bounds.append(parsed.timestamp() if parsed is not None else None)
    if bounds[0] is not None and bounds[1] is not None and bounds[0]>bounds[1]:raise ValueError('corpus_time_scope_invalid')
    path=_index_db_path(root)
    if not path.exists(): return {'ok':False,'reason':'corpus_not_ready','items':[]}
    terms=list(dict.fromkeys(re.findall(r'[^\W_]{2,}',unicodedata.normalize('NFKC',query),re.UNICODE)))[:16]
    if not terms and not (chat_id or author_id): return {'ok':True,'items':[]}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        db.execute('PRAGMA query_only=ON')
        if cancelled is not None:db.set_progress_handler(lambda:1 if cancelled() else 0,1000)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='alden_messages'").fetchone():
            return {'ok':False,'reason':'corpus_not_ready','items':[]}
        account=db.execute("SELECT value FROM corpus_meta WHERE key='account'").fetchone()[0]
        if expected_account and account!=expected_account: return {'ok':False,'reason':'corpus_account_mismatch','items':[]}
        params=[];where=[]
        if terms:
            table='context_messages_fts f JOIN alden_messages m ON m.id=f.rowid'
            where.append('context_messages_fts MATCH ?');params.append(' OR '.join('"'+word+'"' for word in terms))
            order='bm25(context_messages_fts),m.id DESC'
        else: table='alden_messages m';order='m.id DESC'
        if chat_id:where.append('m.chat_id=?');params.append(str(int(chat_id)))
        if author_id:where.append('m.author_id=?');params.append(str(int(author_id)))
        if any(value is not None for value in bounds):
            def epoch(value):
                parsed=_message_datetime_kst(value)
                return parsed.timestamp() if parsed is not None else None
            db.create_function('alden_epoch',1,epoch,deterministic=True)
            for value,operator in zip(bounds,('>=','<=')):
                if value is not None:where.append('alden_epoch(m.date) '+operator+' ?');params.append(value)
        sql='SELECT m.chat_id,m.log_id,m.author_id,m.user_name,m.message,m.date,m.is_self,m.message_type FROM '+table
        if where:sql+=' WHERE '+' AND '.join(where)
        sql+=' ORDER BY '+order+' LIMIT ?';params.append(limit)
        items=[];budget=4800
        for room,log,actor,name,text,date,self_row,kind in db.execute(sql,params):
            excerpt=str(text)[:min(1000,budget)];budget-=len(excerpt)
            observed=_message_datetime_kst(date)
            items.append({'source_id':f'kakao:{account}:room:{room}:log:{log}','chat_id':room,'log_id':log,'author_id':actor,'sender':name,'content':excerpt,'date':observed.isoformat() if observed is not None else str(date),'source_date':date,'truncated':len(excerpt)<len(str(text)),
                          'source_kind':'local_db_snapshot','source_role':'outgoing_unclassified' if self_row else 'peer_history' if int(actor)>0 else 'system_history','message_type':kind})
            if budget<=0:break
        return {'ok':True,'mode':'bm25','account':account,'items':items}
