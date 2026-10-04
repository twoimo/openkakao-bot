import hashlib
import json
import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import alden_osk_sources as sources


class OskSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.snapshot = self.root / 'corpus.sqlite3'
        self.account = '1' * 64
        with sqlite3.connect(self.snapshot) as db:
            db.executescript('CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat_id TEXT,log_id TEXT,author_id TEXT,message TEXT,is_self INTEGER);')
            db.execute('INSERT INTO corpus_meta VALUES (?,?)', ('account', self.account))
            db.executemany('INSERT INTO alden_messages VALUES (?,?,?,?,?,?)', [
                (1, '42', str(2**60), '7', '## 9\n' + '긴 원문 ' * 4000, 0),
                (2, '42', '2', '7', '내가 쓴 기록이지만 사람/에이전트 여부는 미확인', 1),
                (3, '42', '3', '0', '{"system":"알림"}', 0)])
        self.vault = self.root / 'vault'
        self.before = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()

    def tearDown(self):
        self.temporary.cleanup()

    def export(self, **kwargs):
        return sources.export_corpus(self.snapshot, self.vault, 'a'*32, lambda text: (text, []), **kwargs)

    def index(self):
        return self.vault / '_sources/kakao' / ('a'*32) / 'source-index.sqlite3'

    def test_preserves_full_records_roles_and_exact_large_ids_without_source_writes(self):
        report = self.export()
        self.assertEqual(report['records'], 3)
        self.assertFalse(report['content_truncated'])
        self.assertEqual(hashlib.sha256(self.snapshot.read_bytes()).hexdigest(), self.before)
        path = next(self.index().parent.glob('*.txt'))
        payloads = [json.loads(block.split('```json\n',1)[1].split('\n```',1)[0]) for block in path.read_text().split('\n\n## ')]
        self.assertEqual(len(payloads[0]['original']['message']), len('## 9\n' + '긴 원문 '*4000))
        self.assertEqual([p['source_role'] for p in payloads], ['peer_history','outgoing_unclassified','system_history'])
        key = f'kakao:{self.account}:room:42:log:{2**60}'
        refs = sources.coordinates(self.index(), [key])
        self.assertEqual(len(refs), 1)
        self.assertIn('#message-1-', refs[0])
        self.assertFalse(list(self.vault.rglob('_raw')))

    def test_chunking_keeps_every_source_and_never_replaces_an_existing_generation(self):
        with patch.object(sources, 'CHUNK_ROWS', 1):
            self.assertEqual(self.export()['chunks'], 3)
        before = {p.name:p.read_bytes() for p in self.index().parent.glob('*.txt')}
        with self.assertRaisesRegex(RuntimeError, 'generation_exists'):
            self.export()
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.index().parent.glob('*.txt')})

    def test_cancelled_stage_cannot_supply_grounding_coordinates(self):
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.export(cancelled=lambda: True)
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            sources.coordinates(self.index(), ['anything'])

    def test_duplicate_source_identity_retains_both_records_instead_of_merging(self):
        with sqlite3.connect(self.snapshot) as db:
            db.execute("UPDATE alden_messages SET log_id='2' WHERE id=3")
        self.export()
        refs = sources.coordinates(self.index(), [f'kakao:{self.account}:room:42:log:2'])
        self.assertEqual(len(refs), 2)

    def test_symlink_snapshot_is_rejected_without_creating_a_vault(self):
        link = self.root/'alias.sqlite3';link.symlink_to(self.snapshot)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            sources.export_corpus(link, self.vault, 'a'*32, lambda text:(text, []))
        self.assertFalse(self.vault.exists())

    def test_metadata_requires_complete_capture_and_retains_source_labels(self):
        with sqlite3.connect(self.snapshot) as db:
            db.execute('CREATE TABLE alden_rooms(chat_id TEXT,label TEXT)');db.execute('INSERT INTO alden_rooms VALUES(?,?)',('42','ＡＢＣ 방'))
            db.execute('CREATE TABLE alden_authors(author_id TEXT,label TEXT)');db.execute('INSERT INTO alden_authors VALUES(?,?)',('7','같은 이름'))
        self.export()
        receipt=sources.export_metadata(self.snapshot,self.vault,'a'*32,lambda text:(text,[]))
        self.assertEqual(receipt['metadata_records'],3)
        refs=sources.coordinates(self.index(),[f'kakao:{self.account}:author:7']);self.assertEqual(len(refs),1)
        self.assertIn('ＡＢＣ 방',(self.vault/receipt['coordinate_file']).read_text())
        with self.assertRaises(ValueError):sources.export_metadata(self.snapshot,self.vault,'../bad',lambda text:(text,[]))

    def ground_fixture(self):
        self.export();state=self.root/'state';home=state/'knowledge/osk';shutil.copytree(self.vault,home/'vault')
        source=state/'knowledge/corpus'/self.account/'context.sqlite3';source.parent.mkdir(parents=True);shutil.copy2(self.snapshot,source)
        (source.parent.parent/'current.json').write_text(json.dumps({'schema_version':1,'account':self.account}))
        (home/'raw-sources.json').write_text(json.dumps({'schema_version':1,'enabled':True,'base_generation':'a'*32,'base_account':self.account}))
        return state,source

    def test_exact_current_records_are_immutable_versions_with_original_roles(self):
        state,source=self.ground_fixture();before=hashlib.sha256(source.read_bytes()).hexdigest()
        key=f'kakao:{self.account}:room:42:log:2'
        graph={'nodes':[{'id':'node','evidence':{'source_event_ids':[key]}},{'id':'unproved','evidence':{'kind':'seed'}}],'edges':[]}
        derived=sources.ground_graph(state,graph,lambda text:(text,[]))
        self.assertEqual(derived['raw_grounding']['grounded_nodes'],1);self.assertEqual(derived['raw_grounding']['withheld_nodes'],1)
        files={p:p.read_bytes() for p in (state/'knowledge/osk/vault/_sources/kakao/current').glob('*.txt')}
        self.assertIn('outgoing_unclassified',next(iter(files.values())).decode())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
        again=sources.ground_graph(state,graph,lambda text:(text,[]));self.assertEqual(again['nodes'][0]['raw_sources'],derived['nodes'][0]['raw_sources'])
        with sqlite3.connect(source) as db:db.execute("UPDATE alden_messages SET message='수정된 원문' WHERE id=2")
        updated=sources.ground_graph(state,graph,lambda text:(text,[]));self.assertNotEqual(updated['nodes'][0]['raw_sources'],derived['nodes'][0]['raw_sources'])
        for path,content in files.items():self.assertEqual(path.read_bytes(),content)

    def test_conflicting_source_identity_is_archived_but_withheld_from_learning(self):
        state,source=self.ground_fixture()
        with sqlite3.connect(source) as db:db.execute("UPDATE alden_messages SET log_id='2' WHERE id=3")
        graph={'nodes':[{'id':'conflict','evidence':{'source_event_ids':[f'kakao:{self.account}:room:42:log:2']}}],'edges':[]}
        result=sources.ground_graph(state,graph,lambda text:(text,[]));self.assertEqual(result['nodes'],[]);self.assertEqual(result['raw_grounding']['conflicting_source_ids'],1)

    def test_quality_flags_keep_all_raw_and_do_not_merge_repeated_text_with_different_ids(self):
        with sqlite3.connect(self.snapshot) as db:
            db.execute('CREATE TABLE alden_rooms(chat_id TEXT,label TEXT)');db.execute('INSERT INTO alden_rooms VALUES(?,?)',('42',' ＡＢＣ  방 '))
        before=hashlib.sha256(self.snapshot.read_bytes()).hexdigest();flags=self.root/'quality.sqlite3'
        report=sources.quality_report(self.snapshot,flags)
        self.assertEqual(report['raw_rows_deleted'],0);self.assertEqual(report['source_identity_duplicate_groups'],0)
        self.assertEqual(report['normalized_room_labels'],1);self.assertTrue(flags.exists())
        self.assertEqual(hashlib.sha256(self.snapshot.read_bytes()).hexdigest(),before)
