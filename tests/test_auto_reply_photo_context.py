"""Photo follow-up authority and owned recovery; never use live Kakao adapters."""

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests import test_auto_reply_cli_runtime as runtime_fixtures


class PhotoContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = runtime_fixtures.AutoReplyCliRuntimeTests()
        cls.worker = cls.fixture._load_auto_reply_module('photo_context_worker')

    def setUp(self):
        graph = mock.patch('auto_reply_knowledge_graph.retrieve_knowledge_bundle',
                           return_value={'facts': []})
        graph.start()
        self.addCleanup(graph.stop)

    @staticmethod
    def event():
        return {'chat_id': 42, 'author_id': 7, 'log_id': 100, 'sent_at': 1000,
                'message': '왼쪽 색은?', 'recent_messages': []}

    @staticmethod
    def photo(**changes):
        return {'chat_id': 42, 'author_id': 7, 'log_id': 99, 'sent_at': 999,
                'message_type': 16386, 'message': '사진', 'is_self': False, **changes}

    def test_only_proven_preceding_same_room_photos_are_recovered(self):
        w = self.worker
        for changes in ({'log_id':100}, {'log_id':101}, {'sent_at':1001},
                        {'sent_at':699}, {'chat_id':43}, {'chat_id':True},
                        {'author_id':8}, {'is_self':True}, {'message_type':'2'},
                        {'sent_at':None}, {'sent_at':float('nan')},
                        {'message_type':True}):
            with self.subTest(row=changes):
                event = self.event(); event['recent_messages'] = [self.photo(**changes)]
                self.assertEqual(w._same_author_recent_photo_events(event), [])
        for value in (None, 'invalid', '1000', float('nan'), float('inf'), True, 1000.0):
            with self.subTest(current_time=value):
                event = self.event(); event['sent_at'] = value
                event['recent_messages'] = [self.photo()]
                self.assertEqual(w._same_author_recent_photo_events(event), [])
        event = self.event(); event['recent_messages'] = [self.photo(sent_at=700)]
        source = w._same_author_recent_photo_event(event)
        self.assertEqual((source['log_id'], source['sent_at'], source['attachment']), (99,700,'image'))
        self.assertEqual(event['sent_at'], 1000)
        source_row = self.photo(); source_row.pop('chat_id')
        event['recent_messages'] = [source_row]
        self.assertEqual(w._same_author_recent_photo_event(event)['chat_id'],42)

    def test_duplicate_recent_rows_do_not_repeat_download_candidates(self):
        event = self.event()
        event['recent_messages'] = [self.photo(log_id=96),self.photo(),self.photo(),self.photo(log_id=98)]
        self.assertEqual([x['log_id'] for x in self.worker._same_author_recent_photo_events(event,limit=2)], [99,98])
        self.assertEqual(self.worker._same_author_recent_photo_events(event,limit=-1), [])

    def test_recovery_rejects_foreign_bundle_symlink_and_duplicate_paths(self):
        w = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary)/'fake-cli'; binary.touch()
            with self.fixture._owned_image_bundle(w) as foreign:
                original = foreign['paths'][0].read_bytes()
                for mode in ('foreign','symlink','duplicate','malformed'):
                    created = []
                    def run(command, **kwargs):
                        self.assertIn('--expected-author-id',command)
                        directory = Path(command[command.index('--output-dir')+1]);created.append(directory)
                        path = directory/'image-00.png'
                        if mode=='malformed': return 0,b'[]',b''
                        if mode=='foreign': paths = [str(foreign['paths'][0])]
                        elif mode=='symlink':
                            path.symlink_to(foreign['paths'][0]);paths=[str(path)]
                        else:
                            path.write_bytes(original);path.chmod(0o600);paths=[str(path),str(path)]
                        return 0,json.dumps({'paths':paths}).encode(),b''
                    with self.subTest(mode=mode), mock.patch.object(w,'BIN',binary), \
                            mock.patch.dict(os.environ,{'OPENKAKAO_ALLOW_IMAGE_ANALYSIS':'1'}), \
                            mock.patch.object(w,'_run_bounded_process',side_effect=run):
                        self.assertIsNone(w._recover_local_media_bundle(self.event()))
                        # A rejected foreign target remains untouched. Cleanup
                        # is restricted to this call's newly created directory.
                        self.assertEqual(foreign['paths'][0].read_bytes(),original)
                        self.assertTrue(all(not p.exists() for p in created))

    def test_recovery_cancellation_stops_owned_cli_and_cleans_directory(self):
        w = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); pid_file=root/'pid'; child_file=root/'child'; directory_file=root/'directory'
            binary=root/'fake-cli'
            binary.write_text('#!/bin/sh\nprintf "%s" "$$" > "$ALDEN_PHOTO_TEST_PID"\n'
                              'prev=""\nfor arg in "$@"; do\n'
                              'if [ "$prev" = "--output-dir" ]; then printf "%s" "$arg" > "$ALDEN_PHOTO_TEST_DIR"; fi\n'
                              'prev="$arg"\ndone\nsleep 20 &\nprintf "%s" "$!" > "$ALDEN_PHOTO_TEST_CHILD"\nwait\n')
            binary.chmod(0o700)
            (root/'abort').mkdir(mode=0o700)
            token=w.AbortController(root/'abort').token(); errors=[]
            def recover():
                try:
                    with w._active_job_abort_token(token): w._recover_local_media_bundle(self.event())
                except BaseException as exc: errors.append(exc)
            with mock.patch.object(w,'BIN',binary), mock.patch.dict(os.environ,{
                'OPENKAKAO_ALLOW_IMAGE_ANALYSIS':'1','ALDEN_PHOTO_TEST_PID':str(pid_file),
                'ALDEN_PHOTO_TEST_CHILD':str(child_file),
                'ALDEN_PHOTO_TEST_DIR':str(directory_file)}):
                thread=threading.Thread(target=recover);thread.start()
                try:
                    deadline=time.monotonic()+3
                    while not child_file.exists() and time.monotonic()<deadline: time.sleep(0.01)
                    self.assertTrue(directory_file.exists())
                    self.assertTrue(child_file.exists())
                    token.cancel();thread.join(3)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],w.AldenCancelled)
                    self.assertFalse(Path(directory_file.read_text()).exists())
                    with self.assertRaises(ProcessLookupError): os.kill(int(pid_file.read_text()),0)
                    with self.assertRaises(ProcessLookupError): os.kill(int(child_file.read_text()),0)
                finally:
                    token.cancel();thread.join(3)

    def test_prompt_keeps_photo_origin_separate_from_current_user_turn(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            module,_=self.fixture._load_trusted_codex_module('photo_context_prompt',Path(temporary))
            module._operator_state_root=lambda:Path(temporary)
            with self.fixture._owned_image_bundle(module) as media:
                evidence='media:'+media['manifest']['bundle_sha256']
                response={'should_reply':True,'reply':'왼쪽은 녹색이네요','category':'information',
                          'reason':'photo_followup','evidence_ids':[evidence]}
                with self.fixture._fake_qwen27b_gateway(module,json.dumps(response)) as gateway, \
                        mock.patch.object(module,'privacy_attestation_current',return_value=True), \
                        mock.patch.object(module,'runner_is_trusted',return_value=True), \
                        mock.patch.object(module,'_acquire_model_call_slot',return_value={
                            'allowed':True,'failure_class':'','retry_at':time.time()+180,'lease_token':'c'*32}), \
                        mock.patch.object(module,'_finish_model_call_success',return_value=True), \
                        mock.patch.object(module,'detect_mlx_gateway_models',return_value=[{
                            'id':module.QWEN38_27B_MODEL_ID.removeprefix('mlx/'),
                            'loaded':True,'state':'ready','capabilities':['chat','vision','json_schema']}]), \
                        mock.patch.object(module,'_run_bounded_process'):
                    result=module.generate_reply('왼쪽 색은?',[],[],[],[],image_paths=media['paths'],
                        image_marker=media['marker'],media_evidence_id=evidence,source_log_id=100,
                        media_source_log_ids=[99])
                    self.assertTrue(result['should_reply'])
                    prompt=json.loads(gateway['payloads'][0]['messages'][1]['content'][0]['text'])
                    self.assertEqual(prompt['current_inbound_evidence']['source_log_id'],100)
                    self.assertEqual(prompt['media_evidence']['source_log_id'],99)
                    self.assertEqual(prompt['media_evidence']['source_log_ids'],[99])
                    gateway['external_urlopen'].assert_not_called()
                    gateway['payloads'].clear()
                    for invalid in ([100],[101],[99,99],[[99]],[True],[]):
                        bad=module.generate_reply('왼쪽 색은?',[],[],[],[],image_paths=media['paths'],
                            image_marker=media['marker'],media_evidence_id=evidence,source_log_id=100,
                            media_source_log_ids=invalid)
                        self.assertEqual(bad['reason'],'image_unavailable')
                    self.assertEqual(gateway['payloads'],[])

    def test_second_recovery_cancellation_cleans_first_completed_bundle(self):
        w=self.worker
        event=self.event();event['recent_messages']=[self.photo(),self.photo(log_id=98,sent_at=998)]
        with self.fixture._owned_image_bundle(w) as media, \
                mock.patch.object(w,'_reply_turn_hold_reason',return_value=None), \
                mock.patch.dict(os.environ,{'OPENKAKAO_ALLOW_IMAGE_ANALYSIS':'1'}), \
                mock.patch.object(w,'_recover_local_media_bundle',side_effect=[media['paths'],w.AldenCancelled('second_recovery')]), \
                mock.patch.object(w,'generate_reply') as generate:
            with self.assertRaises(w.AldenCancelled): w.analyze_event(event)
            self.assertFalse(media['directory'].exists())
            generate.assert_not_called()

    def test_missing_file_before_admission_never_acquires_model_lease(self):
        w = self.worker
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(w, '_operator_state_root', return_value=Path(temporary)), \
                mock.patch.object(w, 'runner_is_trusted', return_value=True), \
                mock.patch.object(w, '_image_path_within_cap', return_value=True), \
                mock.patch.object(w, '_acquire_model_call_slot') as acquire:
            result = w.generate_reply(
                '[사진]', [], [], [], [], attachment='image',
                image_paths=[Path(temporary) / 'missing.png'],
                media_evidence_id='media:' + 'a' * 64,
            )
        self.assertEqual(result['reason'], 'image_unavailable')
        self.assertFalse(result['model_invoked'])
        self.assertEqual(result['evidence_ids'], [])
        acquire.assert_not_called()


if __name__ == '__main__':
    unittest.main()
