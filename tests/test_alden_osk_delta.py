import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_osk_delta as delta
import alden_osk as sdk
from alden_history import LocalDataAccessWaiting


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.state=self.root/'state';self.account='1'*64
        folder=self.state/'knowledge/corpus'/self.account;folder.mkdir(parents=True)
        self.source=folder/'context.sqlite3'
        with sqlite3.connect(self.source) as db:
            db.executescript('CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat_id TEXT,log_id TEXT,author_id TEXT,message TEXT,digest BLOB,is_self INTEGER); CREATE TABLE alden_rooms(chat_id TEXT PRIMARY KEY,label TEXT); CREATE TABLE alden_authors(author_id TEXT PRIMARY KEY,label TEXT);')
            db.executemany('INSERT INTO corpus_meta VALUES(?,?)',[('account',self.account),('snapshot','v1'),('complete','1')])
            db.executemany('INSERT INTO alden_messages VALUES(?,?,?,?,?,?,?)',[(1,'42',str(2**60),'7','원문',b'first',0),(2,'84','2','8','다른 방',b'second',0)])
            db.execute("INSERT INTO alden_rooms VALUES('42','방')");db.execute("INSERT INTO alden_authors VALUES('7','이름')")
        self.baseline=self.root/'baseline.sqlite3';shutil.copy2(self.source,self.baseline)
        (folder.parent/'current.json').write_text(json.dumps({'schema_version':1,'account':self.account}))
        home=self.state/'knowledge/osk';home.mkdir()
        (home/'raw-sources.json').write_text(json.dumps({'schema_version':1,'enabled':True,'base_account':self.account,'base_snapshot':str(self.baseline)}))
        self.filter=lambda value:(value,[])

    def texts(self):return {path:path.read_bytes() for path in (self.state/'knowledge/osk/vault/_sources/kakao/deltas').glob('*.txt')}

    def test_insert_correction_removal_and_role_change_append_without_touching_sources(self):
        first=delta.capture(self.state,self.filter);self.assertEqual(first['changed'],0)
        originals=self.texts();baseline_hash=hashlib.sha256(self.baseline.read_bytes()).hexdigest()
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE corpus_meta SET value='v2' WHERE key='snapshot'")
            db.execute("UPDATE alden_messages SET message='정정된 원문',digest=X'AB' WHERE id=1")
            db.execute('DELETE FROM alden_messages WHERE id=2')
            db.execute("INSERT INTO alden_messages VALUES(3,'42','3','7','내 기록',X'AC',1)")
        source_hash=hashlib.sha256(self.source.read_bytes()).hexdigest()
        second=delta.capture(self.state,self.filter)
        self.assertEqual((second['changed'],second['removed'],second['rows']),(2,1,2))
        text=b''.join(self.texts().values()).decode();self.assertIn('external_source_removed',text);self.assertIn('outgoing_unclassified',text)
        self.assertIn(str(2**60),text);self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),source_hash)
        self.assertEqual(hashlib.sha256(self.baseline.read_bytes()).hexdigest(),baseline_hash)
        for path,body in originals.items():self.assertEqual(path.read_bytes(),body)
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE corpus_meta SET value='v3' WHERE key='snapshot'");db.execute('UPDATE alden_messages SET is_self=1 WHERE id=1')
        self.assertEqual(delta.capture(self.state,self.filter)['changed'],1)

    def test_same_publication_is_noop_and_cancelled_capture_cannot_advance_index(self):
        first=delta.capture(self.state,self.filter);files=self.texts()
        second=delta.capture(self.state,self.filter);self.assertEqual(second['state'],'unchanged');self.assertEqual(self.texts(),files)
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE corpus_meta SET value='v2' WHERE key='snapshot'");db.execute("INSERT INTO alden_messages VALUES(3,'42','3','7','새 원문',X'AB',0)")
        with self.assertRaisesRegex(RuntimeError,'cancelled'):delta.capture(self.state,self.filter,cancelled=lambda:True)
        self.assertEqual(delta.capture(self.state,self.filter)['changed'],1)

    def test_partial_or_foreign_corpus_does_not_get_a_complete_archive_marker(self):
        with sqlite3.connect(self.source) as db:db.execute("UPDATE corpus_meta SET value='0' WHERE key='complete'")
        with self.assertRaisesRegex(RuntimeError,'not_complete'):delta.capture(self.state,self.filter)
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE corpus_meta SET value='1' WHERE key='complete'");db.execute("UPDATE corpus_meta SET value=? WHERE key='account'",('2'*64,))
        with self.assertRaisesRegex(RuntimeError,'not_complete'):delta.capture(self.state,self.filter)

    def test_original_access_wait_keeps_saved_sync_and_uses_collection_backoff(self):
        # Collection and sync adapters are fake; no real DB access, worker/send
        # or model action occurs in this behavior test.
        (self.state/'knowledge/osk/raw-sources.json').unlink()
        calls=[];steps=[]
        def collect(*args):calls.append('collect');raise LocalDataAccessWaiting()
        def sync(*args):calls.append('sync');return {'ok':True,'pending':0,'changed':0,'conflicts':0,'stale':False}
        with patch.object(sdk,'_aborted',return_value=False):
            first=sdk.sync_cycle(self.state,Path('/fake'),collect=collect,sync=sync,record=lambda *a,**k:steps.append(a[2]),now=1000)
            second=sdk.sync_cycle(self.state,Path('/fake'),collect=collect,sync=sync,record=lambda *a,**k:steps.append(a[2]),now=1060)
        self.assertTrue(first['ok']);self.assertEqual(first['collection'],'waiting')
        self.assertTrue(second['ok']);self.assertEqual(calls,['collect','sync','sync'])
        self.assertEqual(steps[-1],'pending');self.assertNotIn('complete',steps)

    def test_failed_cli_cycle_returns_json_and_finishes_the_same_receipt(self):
        self.state.chmod(0o700)
        binary=self.root/'fake-cli'
        binary.write_text('#!/bin/sh\nexit 2\n');binary.chmod(0o700)
        result=subprocess.run(
            [sys.executable,str(Path(sdk.__file__)), '--state-root',str(self.state),
             '--sync','--bin',str(binary)],capture_output=True,text=True,timeout=10,
        )
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stdout).get('error'),'RuntimeError',result.stdout)
        with sqlite3.connect(self.state/'alden-history.sqlite3') as db:
            receipts=db.execute('SELECT phase FROM db_cycles').fetchall()
            steps=db.execute('SELECT phase FROM db_steps ORDER BY at').fetchall()
        self.assertEqual(receipts,[('failed',)])
        self.assertEqual(steps,[('collecting',),('failed',)])


if __name__=='__main__':unittest.main()
