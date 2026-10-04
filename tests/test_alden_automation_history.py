import json
import sqlite3
from contextlib import closing
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_automation_history as history
import alden_history


class AutomationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.room = self.root / 'rooms' / '9007199254740997'
        self.room.mkdir(parents=True)
        self.queue = self.room / 'reply-queue.sqlite3'
        with closing(sqlite3.connect(self.queue)) as db, db:
            db.execute('CREATE TABLE reply_jobs(event_id TEXT PRIMARY KEY,event_json TEXT,status TEXT,reply TEXT,reason TEXT,error_class TEXT,created_at REAL,updated_at REAL)')

    def job(self, event='event', *, status='sent', reason='direct_question', reply='올든 답변', at=100, room=None, **extra):
        value = {'chat_id': room or self.room.name, 'chat_name': '같은 이름', 'message': '지난 약속은 어떻게 됐나요?', **extra}
        with closing(sqlite3.connect(self.queue)) as db, db:
            db.execute('INSERT INTO reply_jobs VALUES(?,?,?,?,?,?,?,?)', (event, json.dumps(value), status, reply, reason, None, at, at))

    def ledger(self, *values):
        (self.room / 'reply-evidence.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in values))

    def test_generation_and_delivery_join_with_context_and_terminal_confirmation(self):
        self.job()
        self.ledger({'event_id': 'event', 'status': 'scheduled', 'message': '질문 일부', 'reply': '올든 답변', 'reason': 'direct_question',
                     'recorded_at': 90, 'recent_conversation': [{'sender': '민준', 'message': '약속은 취소됐어요.'}]},
                    {'event_id': 'event', 'status': 'sent', 'outgoing_log_id': 9007199254740998, 'reply': '올든 답변', 'recorded_at': 95})
        before = self.queue.read_bytes()
        page = alden_history.read(self.root, Path('/unused'), 'reply-history')
        self.assertEqual(len(page['items']), 1)
        item = page['items'][0]
        self.assertEqual(item['room_id'], '9007199254740997')
        self.assertEqual(item['outcome'], 'confirmed')
        self.assertEqual(item['incoming'], '지난 약속은 어떻게 됐나요?')
        self.assertEqual(item['context'][0]['text'], '약속은 취소됐어요.')
        self.assertEqual(self.queue.read_bytes(), before)

    def test_queue_uncertainty_and_legacy_sent_without_db_id_are_not_confirmed(self):
        self.job(status='delivery_unknown')
        self.ledger({'event_id': 'event', 'status': 'sent', 'reply': '올든 답변', 'recorded_at': 95})
        self.assertEqual(history.read(self.root, 'reply')['items'][0]['outcome'], 'unknown')
        self.ledger({'event_id': 'archived', 'status': 'sent', 'reply': '보관된 일부', 'recorded_at': 20})
        archived = history.read(self.root, 'reply')['items'][-1]
        self.assertEqual(archived['outcome'], 'unknown'); self.assertTrue(archived['preview_only'])

    def test_archived_outgoing_row_confirms_only_matching_content(self):
        self.ledger({'event_id': 'archived', 'status': 'scheduled', 'reply': '같은 답변', 'reason': 'direct_question', 'recorded_at': 20},
                    {'event_id': 'archived', 'status': 'sent', 'reply': '같은 답변', 'outgoing_log_id': '123', 'recorded_at': 30})
        item = history.read(self.root, 'reply')['items'][0]
        self.assertEqual(item['confirmation'], 'outgoing_db_row')
        self.job(event='archived', status='delivery_unknown', reply='같은 답변이지만 다른 전체 내용')
        self.assertEqual(history.read(self.root, 'reply')['items'][0]['outcome'], 'unknown')

    def test_news_is_separate_and_titles_preserve_send_content(self):
        self.job(event='news', reason='geeknews_rss', proactive=True,
                 reply='GeekNews TOP5 · 2026-10-04 09:00 KST\n\n1. 새 소식 https://news.hada.io/topic?id=123\n\n2. 다른 소식 https://news.hada.io/topic?id=124')
        self.job(event='reply')
        page = history.read(self.root, 'geeknews')
        self.assertEqual([row['event_id'] for row in page['items']], ['news'])
        self.assertEqual(page['items'][0]['articles'][0]['title'], '새 소식')
        self.assertEqual(len(history.read(self.root, 'reply')['items']), 1)

    def test_pagination_ties_keep_every_distinct_identity_and_nfkc_search(self):
        for index in range(21): self.job(event=f'event-{index:02}', at=100, reply='ＡＢＣ 답변')
        found = []; before = None
        while True:
            page = history.read(self.root, 'reply', json.dumps({'limit': 4, 'before': before, 'search': 'abc', 'status': 'confirmed'}))
            found.extend(row['id'] for row in page['items']); before = page['next']
            if before is None: break
        self.assertEqual(len(found), 21); self.assertEqual(len(set(found)), 21)

    def test_different_room_payload_is_excluded_and_unsafe_sources_are_partial(self):
        self.job(room='42')
        self.assertEqual(history.read(self.root, 'reply')['items'], [])
        self.queue.unlink(); self.queue.symlink_to(self.root / 'outside.sqlite3')
        result = history.read(self.root, 'reply')
        self.assertTrue(result['partial']); self.assertEqual(result['unavailable_rooms'], 1)

    def test_missing_source_is_empty_without_storage_or_workers(self):
        missing = self.root / 'missing'
        self.assertEqual(history.read(missing, 'reply')['items'], [])
        self.assertFalse(missing.exists())
        for query in ({'limit': 0}, {'before': [float('nan'), 'x']}, {'status': {}}, {'unexpected': 'x'}):
            with self.assertRaises(ValueError): history.read(self.root, 'reply', json.dumps(query))


if __name__ == '__main__': unittest.main()
