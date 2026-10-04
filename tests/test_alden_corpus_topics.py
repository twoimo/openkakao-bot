import sqlite3
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from scripts import alden_corpus_topics as topics


class RawTopicTests(unittest.TestCase):
    def source(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('CREATE TABLE corpus_meta(key TEXT,value TEXT); CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat TEXT,chat_id TEXT,log_id TEXT,message TEXT,author_id TEXT,is_self INTEGER,message_type INTEGER);')
        db.execute('INSERT INTO corpus_meta VALUES(?,?)',('account','1'*64))
        return db

    def test_published_raw_schema_classifies_normalized_incoming_text_only(self):
        db=self.source()
        db.executemany('INSERT INTO alden_messages VALUES(?,?,?,?,?,?,?,?)',[(1,'room1','42','1','ＡＩ 모델 주식','7',0,1),(2,'room1','42','2','AI 모델','7',1,1),(3,'room1','42','3','AI 모델','0',0,0),(4,'room2','43','4','AI 모델','8',0,1)])
        db.commit();before=db.total_changes
        result=topics.derive(db,chat='room1',minimum=1)
        self.assertIn('ai',result['active']);self.assertEqual(result['counts']['ai'],1)
        self.assertEqual(result['samples']['ai'],['kakao:'+'1'*64+':room:42:log:1'])
        self.assertEqual(db.total_changes,before)

    def test_co_mentions_are_actual_same_message_matches(self):
        db=self.source()
        db.executemany('INSERT INTO alden_messages VALUES(?,?,?,?,?,?,?,?)',[(1,'r','42','1','AI 주식','7',0,1),(2,'r','42','2','AI','8',0,1),(3,'r','42','3','주식','8',0,1)])
        result=topics.derive(db,minimum=1)
        self.assertEqual(result['pairs'][('r','ai','stocks')],1)
        self.assertEqual(result['pair_samples'][('r','ai','stocks')],['kakao:'+'1'*64+':room:42:log:1'])


if __name__=='__main__':unittest.main()
