"""Published-source selection, identity and quotes; no account/model/send."""
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_auto_reply_knowledge_graph import KG
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_corpus

class CorpusTests(unittest.TestCase):
    def fixture(self,base):
        root=base/'state';root.mkdir()
        account='a'*64;folder=root/'knowledge/corpus'/account;folder.mkdir(parents=True)
        source=folder/'context.sqlite3';c=sqlite3.connect(source)
        c.executescript('''CREATE TABLE corpus_meta(key TEXT,value TEXT);CREATE TABLE context_message_topics(message_id INTEGER,topic TEXT);CREATE TABLE context_topic_stats(chat TEXT,topic TEXT,message_count INTEGER);CREATE TABLE alden_rooms(chat TEXT,chat_id TEXT,label TEXT);CREATE TABLE alden_authors(author_id TEXT,label TEXT);
          CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat TEXT,chat_id TEXT,log_id TEXT,author_id TEXT,user_name TEXT,message TEXT,date,is_self INTEGER,message_type INTEGER,source TEXT,UNIQUE(chat_id,log_id));
          CREATE INDEX corpus_by_room ON alden_messages(chat,id);CREATE INDEX corpus_by_actor ON alden_messages(author_id,chat,id);
          CREATE VIEW context_messages AS SELECT *,X'' AS vector FROM alden_messages;
          CREATE VIRTUAL TABLE context_messages_fts USING fts5(message,user_name,chat,content='alden_messages',content_rowid='id');''')
        c.execute('INSERT INTO corpus_meta VALUES(?,?)',('account',account));c.execute('INSERT INTO corpus_meta VALUES(?,?)',('snapshot','fixed'))
        c.executemany('INSERT INTO alden_authors VALUES(?,?)',[('7','같은 이름'),('8','같은 이름')])
        for room in ('42','84'):
            key=f'kakao:{account}:room:{room}';c.execute('INSERT INTO alden_rooms VALUES(?,?,?)',(key,room,'같은 방 이름'))
            for actor in ('7','8'):
                for i in range(60):
                    c.execute('INSERT INTO alden_messages(chat,chat_id,log_id,author_id,user_name,message,date,is_self,message_type,source) VALUES(?,?,?,?,?,?,?,?,?,?)',(key,room,str(9007199254740997+i+int(actor)*1000),actor,'같은 이름',f'원문 행 {room} {actor} {i} <script>','2026-10-02',actor=='7',1,key))
        c.execute("INSERT INTO context_messages_fts(context_messages_fts) VALUES('rebuild')");c.commit();c.close()
        (folder.parent/'current.json').write_text(json.dumps({'schema_version':1,'account':account,'snapshot':'fixed'}))
        return root,account,source

    def test_same_names_do_not_merge_numeric_authors_or_rooms(self):
        with TemporaryDirectory() as td:
            root,account,source=self.fixture(Path(td));graph=KG._connect_kg(root/'graph.sqlite3')
            try:
                KG.index_chat_entities(graph,root);KG.index_person_entities(graph,root);KG.index_membership_relations(graph,root)
                people=graph.execute("SELECT entity_id,name FROM kg_entities WHERE category='대화 상대'").fetchall()
                self.assertEqual(set(people),{(f'person:kakao:{account}:actor:7','같은 이름'),(f'person:kakao:{account}:actor:8','같은 이름')})
                rooms=graph.execute("SELECT entity_id,name,aliases_json FROM kg_entities WHERE category='대화방'").fetchall()
                self.assertEqual(len(rooms),2);self.assertEqual(len({r[1] for r in rooms}),2)
                self.assertTrue(all(r[1].startswith('같은 방 이름 · #') and '같은 방 이름' in json.loads(r[2]) for r in rooms))
                self.assertEqual(graph.execute("SELECT count(*) FROM kg_relations WHERE relation='TALKED_IN'").fetchone()[0],4)
            finally:graph.close()

    def test_scope_and_quote_roles_are_preserved_and_accounts_do_not_cross(self):
        with TemporaryDirectory() as td:
            root,account,_=self.fixture(Path(td))
            result=alden_corpus.search(root,'원문',chat_id='42',author_id='7')
            self.assertTrue(result['ok']);self.assertTrue(result['items'])
            self.assertTrue(all(r['chat_id']=='42' and r['author_id']=='7' and r['source_role']=='outgoing_unclassified' for r in result['items']))
            self.assertIn('<script>',result['items'][0]['content'])
            self.assertEqual(alden_corpus.search(root,'원문',expected_account='b'*64)['items'],[])
            with self.assertRaises(ValueError):alden_corpus.search(root,'원문',chat_id='42 OR 1=1')

    def test_equal_room_names_require_disambiguation(self):
        with TemporaryDirectory() as td:
            root,_,_=self.fixture(Path(td))
            self.assertEqual(alden_corpus.resolve_room(root,'카카오톡 같은 방 이름 대화')['state'],'ambiguous')
            self.assertEqual(alden_corpus.resolve_room(root,'없는 이름')['state'],'none')

    def test_missing_titles_restore_exact_catalog_identity_and_remain_distinct(self):
        from unittest import mock
        with TemporaryDirectory() as td:
            root,account,path=self.fixture(Path(td))
            with sqlite3.connect(path) as db:db.execute("UPDATE alden_rooms SET label=CASE chat_id WHEN '42' THEN '' ELSE char(8203) END")
            (root/'menubar-room-catalog.json').write_text(json.dumps({'schema_version':1,'rooms':[{'chat_id':'42','title':'ＡＩ　 연구방'}]}))
            graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                with mock.patch.object(KG,'_copy_consistent_sqlite_replica',wraps=KG._copy_consistent_sqlite_replica) as copy:
                    KG.index_chat_entities(graph,root)
                self.assertEqual(copy.call_count,1)
                rows=dict(graph.execute("SELECT entity_id,name FROM kg_entities WHERE category='대화방'"))
                self.assertEqual(rows[f'chat:kakao:{account}:room:42'],'AI 연구방')
                self.assertTrue(rows[f'chat:kakao:{account}:room:84'].startswith('같은 이름 대화 · #'))
                self.assertNotEqual(rows[f'chat:kakao:{account}:room:84'],rows[f'chat:kakao:{account}:room:42'])
                # Display aliases are grounded in the roster, not written back
                # into the raw room title or used to merge numeric identities.
                with sqlite3.connect(path) as raw:
                    self.assertEqual(raw.execute("SELECT label FROM alden_rooms WHERE chat_id='84'").fetchone()[0],chr(8203))
                self.assertEqual(alden_corpus.resolve_room(root,'카카오톡 ai연구방 내용')['chat_id'],'42')
                KG.index_chat_entities(graph,root)
                self.assertEqual(rows,dict(graph.execute("SELECT entity_id,name FROM kg_entities WHERE category='대화방'")))
            finally:graph.close()

    def test_unicode_variants_are_search_aliases_not_merged_identities(self):
        with TemporaryDirectory() as td:
            root,_,path=self.fixture(Path(td))
            with sqlite3.connect(path) as db:db.execute("UPDATE alden_rooms SET label=CASE chat_id WHEN '42' THEN 'ＡＩ　연구' ELSE 'AI연구' END")
            self.assertEqual(alden_corpus.resolve_room(root,'ai 연구 기록')['state'],'ambiguous')
            graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_chat_entities(graph,root)
                rooms=graph.execute("SELECT name FROM kg_entities WHERE category='대화방'").fetchall()
                self.assertEqual(len(set(rooms)),2);self.assertTrue(all(' · #' in r[0] for r in rooms))
                self.assertEqual(alden_corpus.resolve_room(root,rooms[0][0])['state'],'resolved')
            finally:graph.close()

    def test_people_samples_match_global_latest_ids_without_other_room_mix(self):
        with TemporaryDirectory() as td:
            root,account,path=self.fixture(Path(td));graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_person_entities(graph,root)
                with sqlite3.connect(path) as source:
                    for actor in ('7','8'):
                        expected=source.execute('SELECT chat_id,log_id FROM alden_messages WHERE author_id=? ORDER BY id DESC LIMIT 3',(actor,)).fetchall()
                        evidence=json.loads(graph.execute('SELECT evidence_json FROM kg_entities WHERE entity_id=?',(f'person:kakao:{account}:actor:{actor}',)).fetchone()[0])
                        self.assertEqual(evidence['source_event_ids'],[f'kakao:{account}:room:{room}:log:{log}' for room,log in expected])
            finally:graph.close()

    def test_time_scope_canonicalizes_real_timestamps_and_keeps_source(self):
        with TemporaryDirectory() as td:
            root,_,path=self.fixture(Path(td));stamp=1758708000.25
            with sqlite3.connect(path) as db:db.execute('UPDATE alden_messages SET date=? WHERE id=1',(stamp,))
            day=KG._message_datetime_kst(stamp).date().isoformat()
            result=alden_corpus.search(root,'원문',chat_id='42',author_id='7',time_from=day+'T00:00:00+09:00',time_to=day+'T23:59:59+09:00')
            self.assertEqual(len(result['items']),1)
            self.assertEqual(result['items'][0]['source_date'],stamp)
            self.assertEqual(result['items'][0]['date'],KG._message_datetime_kst(stamp).isoformat())
            for start,end in [('invalid',None),('2026-10-02','2026-10-01')]:
                with self.assertRaisesRegex(ValueError,'corpus_time_scope_invalid'):alden_corpus.search(root,'원문',time_from=start,time_to=end)

    def test_new_publication_invalidates_a_recent_graph_cache(self):
        from unittest import mock
        import time
        with TemporaryDirectory() as td:
            root,account,path=self.fixture(Path(td));graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            KG.ensure_seeded(graph);KG.write_meta(graph,'last_indexed_at',str(int(time.time())));graph.close()
            with mock.patch.object(KG,'refresh_dense_index'):
                result=KG.collect_knowledge_graph(root/'context.sqlite3',state_root=root,wait_for_reindex=True)
            self.assertFalse(result['stale']);self.assertEqual(result['reindex']['mode'],'inline')
            graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            self.assertEqual(KG.read_meta(graph,'source_corpus_snapshot'),'fixed');graph.close()

    def test_only_explicitly_published_account_path_is_selected(self):
        with TemporaryDirectory() as td:
            root,account,path=self.fixture(Path(td));self.assertEqual(KG._index_db_path(root),path)
            pointer=path.parent.parent/'current.json';pointer.write_text('{"schema_version":1,"account":"../escape"}')
            with self.assertRaises(RuntimeError):KG._index_db_path(root)

    def test_snapshot_candidates_keep_valid_provenance_and_numeric_room_scope(self):
        with TemporaryDirectory() as td:
            root,account,_=self.fixture(Path(td));graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_chat_entities(graph,root);KG.index_person_entities(graph,root);KG.index_membership_relations(graph,root)
                for identifier,evidence,updated in graph.execute("SELECT entity_id,evidence_json,updated_at FROM kg_entities"):
                    self.assertTrue(KG._candidate_provenance(identifier,evidence,updated)['provenance_valid'])
            finally:graph.close()
            from unittest import mock
            with mock.patch.object(KG,'_local_dense_embeddings',side_effect=RuntimeError('no model')):
                found=KG._query_knowledge_ranked('같은 이름',state_root=root,chat_id='42')
                forbidden=KG._query_knowledge_ranked('같은 이름',state_root=root,chat_id='999')
            self.assertTrue(f'person:kakao:{account}:actor:7' in found['candidates'])
            self.assertFalse(any(row.startswith(('person:kakao:','chat:kakao:')) for row in forbidden['candidates']))
            self.assertFalse(any('原文 행 84' in fact or '원문 행 84' in fact for fact in found['entity_facts']))
            self.assertTrue(all(':room:84:' not in eid for p in found['candidate_provenance'] for eid in p['source_event_ids']))

if __name__=='__main__':unittest.main()
