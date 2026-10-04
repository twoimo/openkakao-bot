import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_history as h

class HistoryTests(unittest.TestCase):
    def test_unnamed_display_alias_keeps_original_names_ids_and_account_scope(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);(root/'knowledge').mkdir()
            (root/'knowledge/room-aliases.json').write_text(json.dumps({'schema_version':1,'account':'account','rooms':{'9007199254740997':{'label':'민준 · 서연 대화 · #a1b2c3d4'}}}))
            data={'account':'account','rooms':[{'chat_id':'9007199254740997','chat_name':'이름 없는 채팅방'},{'chat_id':'42','chat_name':'내가 정한 이름'}]}
            value=h.room_display_aliases(root,data)
            self.assertTrue(value['rooms'][0]['display_alias']);self.assertEqual(value['rooms'][0]['original_chat_name'],'이름 없는 채팅방')
            self.assertEqual(value['rooms'][0]['chat_id'],'9007199254740997');self.assertEqual(value['rooms'][1]['chat_name'],'내가 정한 이름')
            self.assertEqual(h.room_display_aliases(root,{**data,'account':'other'})['rooms'][0]['chat_name'],'이름 없는 채팅방')
    def test_all_confirmed_turns_survive_paging_without_cutting_a_pair(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            for turn in range(1,124):
                h.record_voice(root,'session',turn,'user','말씀 '+str(turn),turn)
                if turn%3:h.record_voice(root,'session',turn,'assistant','답변 '+str(turn),turn)
            seen=[];before=None
            while True:
                page=h.read(root,Path('/unused'),'voice-history-messages',json.dumps({'limit':10,'before':before}),'session')
                seen += [(r['turn_id'],r['role']) for r in page['items']]
                before=page['next']
                if before is None:break
            expected={(t,'user') for t in range(1,124)}|{(t,'assistant') for t in range(1,124) if t%3}
            self.assertEqual(set(seen),expected);self.assertEqual(len(seen),len(expected))
            h.record_voice(root,'session',1,'user','늦은 중복',1)
            with h.database(root) as db:self.assertEqual(db.execute('SELECT content FROM voice_messages WHERE turn_id=1 AND role="user"').fetchone()[0],'말씀 1')
            self.assertFalse(list(root.glob('*.wav')))
            self.assertEqual((root/'alden-history.sqlite3').stat().st_mode&0o777,0o600)

    def test_read_missing_history_does_not_create_storage(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'missing'
            self.assertEqual(h.read(root,Path('/unused'),'voice-history-sessions')['items'],[])
            self.assertFalse(root.exists())

    def test_real_cycle_steps_are_durable_and_pid_reuse_is_not_live(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            h.cycle_step(root,'cycle','collecting',rooms=2,message='do not persist')
            h.cycle_step(root,'cycle','graphing',nodes=3)
            with h.database(root,write=True) as db:db.execute('UPDATE db_cycles SET pid_start="other process"')
            page=h.read(root,Path('/unused'),'db-sync-history')
            self.assertEqual([r['phase'] for r in page['items']],['graphing','collecting'])
            self.assertEqual(page['current']['phase'],'interrupted')
            self.assertNotIn('message',page['items'][1]['details'])
            h.cycle_step(root,'cycle','complete',nodes=3)
            self.assertEqual(h.read(root,Path('/unused'),'db-sync-history')['current']['phase'],'complete')

    def test_chat_cursor_is_arg_data_and_never_a_shell(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            with mock.patch('alden_history._local_cli',return_value={'messages':[]}) as cli:
                h.read(root,Path('/absolute/cli'),'history-messages','{"anchor":"123","before":"100","limit":50}','42')
                self.assertEqual(cli.call_args.args[1],['local-history','42','-n','50','--anchor','123','--before','100'])
                with self.assertRaises(ValueError):h.read(root,Path('/absolute/cli'),'history-messages','{}','42;echo x')

    def test_only_raw_collection_has_the_longer_bounded_timeout(self):
        with TemporaryDirectory() as directory:
            binary=Path(directory)/'cli';binary.write_bytes(b'test adapter')
            result=mock.Mock(returncode=0,stdout='{"ok":true}')
            with mock.patch('alden_history.subprocess.run',return_value=result) as run:
                h._local_cli(binary,['local-db-collect'])
                self.assertEqual(run.call_args.kwargs['timeout'],90)
                h._local_cli(binary,['local-history','42','-n','100'])
                self.assertEqual(run.call_args.kwargs['timeout'],20)
                self.assertNotIn('shell',run.call_args.kwargs)

if __name__=='__main__':unittest.main()
