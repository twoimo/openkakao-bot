"""Derive bounded, source-grounded topic nodes from a published Raw corpus.

The historical reference DB has context_message_topics. Published Alden Raw
corpora do not. This path reads original rows once, uses the existing explicit
keyword dictionary, and labels co-mentions without inventing causal relations.
"""
from __future__ import annotations
import itertools
import json
import time
import unicodedata
import re
from collections import Counter, defaultdict

from auto_reply_reference_store import classify_topics, TOPIC_LABELS, TOPIC_LEXICON

LABELS = {**TOPIC_LABELS, 'ai':'AI'}
LEXICON = {**TOPIC_LEXICON, 'ai':('AI','인공지능','머신러닝','딥러닝')}
AI_TERM = re.compile(r'(?<![a-z0-9])ai(?![a-z0-9])|인공지능|머신러닝|딥러닝',re.I)


def derive(source, *, chat: str = '', minimum: int = 20) -> dict:
    counts, pairs, rooms, people = Counter(), Counter(), Counter(), Counter()
    samples, pair_samples, room_samples, person_samples = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    account = source.execute("SELECT value FROM corpus_meta WHERE key='account'").fetchone()[0]
    sql = 'SELECT chat,chat_id,log_id,message,author_id,is_self,message_type FROM alden_messages'
    args = []
    if chat: sql += ' WHERE chat=?'; args.append(chat)
    scanned = 0
    for room, room_id, log_id, message, author, is_self, kind in source.execute(sql + ' ORDER BY id', args):
        scanned += 1
        # Keep system notices and the user's outgoing automation out of the
        # inference about what incoming participants actually discussed.
        if is_self or not str(author).isdigit() or int(author) <= 0 or kind != 1 or not isinstance(message, str): continue
        normalized = ' '.join(unicodedata.normalize('NFKC', message).split())
        topics = sorted(set(classify_topics(normalized)))
        if AI_TERM.search(normalized): topics = sorted(set(topics) | {'ai'})
        source_id = f'kakao:{account}:room:{room_id}:log:{log_id}'
        for topic in topics:
            counts[topic] += 1; rooms[(room, topic)] += 1
            people[(str(author),room,topic)] += 1
            if len(samples[topic]) < 8: samples[topic].append(source_id)
            if len(room_samples[(room, topic)]) < 8: room_samples[(room, topic)].append(source_id)
            if len(person_samples[(str(author),room,topic)]) < 8: person_samples[(str(author),room,topic)].append(source_id)
        for left, right in itertools.combinations(topics, 2):
            key = (room, left, right); pairs[key] += 1
            if len(pair_samples[key]) < 8: pair_samples[key].append(source_id)
    active = {topic for topic, count in counts.items() if count >= minimum}
    return {'scanned':scanned,'counts':dict(counts),'active':active,'samples':samples,'pairs':pairs,
            'pair_samples':pair_samples,'rooms':rooms,'room_samples':room_samples,'people':people,'person_samples':person_samples,'account':account}


def index(graph, source, *, chat: str = '', minimum: int = 20) -> dict:
    data = derive(source, chat=chat, minimum=minimum); now = int(time.time())
    for topic in sorted(data['active']):
        count = data['counts'][topic]; label = LABELS.get(topic, topic)
        evidence = {'kind':'snapshot','source_event_ids':data['samples'][topic],'chat_id':chat,'confirmed_at':None,'retracted':False}
        graph.execute('INSERT INTO kg_entities(entity_id,name,category,aliases_json,description,key_facts_json,importance,evidence_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name,category=excluded.category,aliases_json=excluded.aliases_json,description=excluded.description,key_facts_json=excluded.key_facts_json,importance=excluded.importance,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at',
                      ('topic:'+topic,label+' (대화 주제)','대화 주제',json.dumps(list(LEXICON[topic]),ensure_ascii=False),
                       '수집된 원문에서 분류 사전의 키워드가 일치한 주제입니다.',json.dumps([f'참여자 메시지 {count:,}개에서 키워드가 발견됐습니다.'],ensure_ascii=False),min(99,40+count//200),json.dumps(evidence),now))
    written = 0; seen_pairs = set()
    def relation(left, relation_type, right, room, count, refs):
        nonlocal written
        evidence = json.dumps({'kind':'snapshot','source_event_ids':refs,'chat_id':room,'confirmed_at':None,'retracted':False})
        graph.execute('INSERT INTO kg_relations(source_id,relation,target_id,subject_id,relation_type,object_id,room_id,evidence_message_id,context,weight,evidence_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,relation,target_id) DO UPDATE SET weight=excluded.weight,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at',
                      (left,relation_type,right,left,relation_type,right,room,refs[0] if refs else '', '원문 키워드 일치',min(99,30+count//10),evidence,now)); written += 1
    for (room, left, right), count in data['pairs'].most_common(160):
        if left in data['active'] and right in data['active'] and count >= 10 and (left,right) not in seen_pairs:
            # The ERE schema has one row per tuple. Keep the strongest single
            # room observation instead of merging different rooms' evidence.
            seen_pairs.add((left,right))
            relation('topic:'+left,'co_mentions','topic:'+right,room,count,data['pair_samples'][(room,left,right)])
    room_sql = 'SELECT chat FROM context_messages' + (' WHERE chat=?' if chat else '') + ' GROUP BY chat HAVING COUNT(*)>=40 ORDER BY COUNT(*) DESC LIMIT 40'
    visible_rooms = {row[0] for row in source.execute(room_sql, [chat] if chat else [])}
    for (room, topic), count in data['rooms'].most_common(240):
        if topic in data['active'] and count >= minimum and room in visible_rooms:
            relation('chat:'+room,'discusses','topic:'+topic,room,count,data['room_samples'][(room,topic)])
    actor_sql='SELECT author_id FROM context_messages WHERE CAST(author_id AS INTEGER)>0'+(' AND chat=?' if chat else '')+' GROUP BY author_id ORDER BY COUNT(*) DESC LIMIT 60'
    visible_actors={str(row[0]) for row in source.execute(actor_sql,[chat] if chat else [])}
    for (actor,room,topic),count in data['people'].most_common(240):
        if actor in visible_actors and topic in data['active'] and count>=minimum:
            relation('person:kakao:'+data['account']+':actor:'+str(int(actor)),'TALKS_ABOUT','topic:'+topic,room,count,data['person_samples'][(actor,room,topic)])
    graph.execute('INSERT INTO kg_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  ('raw_topic_derivation',json.dumps({'rows':data['scanned'],'topics':len(data['active']),'relations':written,'method':'NFKC + existing keyword dictionary; incoming text only','counts':data['counts']})))
    graph.commit()
    return {'topics':len(data['active']),'written':len(data['active']),'with_samples':len(data['active'])}
