from __future__ import annotations

import contextlib
import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from scripts import alden_model_evaluation as E
from scripts import auto_reply_finetune as F
from scripts.alden_abort import AbortController, AldenCancelled
from tests import test_alden_dpo_scorer as fixtures


DATASET = Path(__file__).parent / 'fixtures/alden-model-evaluation/preferences.json'


class VersionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.policy = fixtures._checkpoint(self.root, 'model')
        self.dataset = self.root / 'preferences.json'
        self.dataset.write_bytes(DATASET.read_bytes())
        fixtures.FakeBackend.reset()

    def evaluate(self, **kwargs):
        def score(pairs, **options):
            return E.scorer.score_local_dpo_pairs(pairs, backend_factory=fixtures.FakeBackend,
                                                  **options)
        options = dict(dataset_path=self.dataset, policy_dir=self.policy, reference_dir=self.policy,
                       version='0.1.6', evaluation_root=self.root/'receipts',
                       checkpoint_format='auto', score_fn=score)
        options.update(kwargs)
        return E.evaluate_version(**options)

    def test_same_version_is_reused_but_changed_model_data_source_and_beta_evaluate(self):
        first = self.evaluate()
        self.assertEqual(first['phase'], 'evaluated')
        self.assertEqual(first['scoring']['evaluated'], 3)
        self.assertEqual(fixtures.FakeBackend.loads, 1)
        with mock.patch.object(E.scorer, 'score_local_dpo_pairs') as score:
            cached = self.evaluate()
            score.assert_not_called()
        self.assertTrue(cached['cached'])
        self.assertEqual(cached['evaluation_id'], first['evaluation_id'])
        self.assertFalse(cached['weights_trained']); self.assertFalse(cached['model_promoted'])
        for change in ('model', 'data', 'source', 'beta', 'version', 'runtime'):
            with self.subTest(change=change):
                options = {}; patch = contextlib.nullcontext()
                if change == 'model':
                    (self.policy/'model-00001-of-00001.safetensors').write_bytes(b'changed')
                elif change == 'data':
                    self.dataset.write_bytes(self.dataset.read_bytes()+b'\n')
                elif change == 'source': patch = mock.patch.object(E,'source_fingerprint',return_value='a'*64)
                elif change == 'runtime': patch = mock.patch.object(E.scorer,'_versions',return_value={'python':'changed'})
                elif change == 'beta': options['beta'] = .2
                elif change == 'version': options['version'] = '0.1.7'
                with patch:
                    result = self.evaluate(**options)
                self.assertFalse(result['cached'])
                self.assertNotEqual(result['evaluation_id'], first['evaluation_id'])

    def test_final_test_is_separate_and_never_reads_training_pairs_for_scoring(self):
        with mock.patch.object(E.scorer,'score_local_dpo_pairs',wraps=E.scorer.score_local_dpo_pairs) as fn:
            result = self.evaluate(final_test=True)
        ids = [p['pair_id'] for p in fn.call_args.args[0]]
        self.assertTrue(all(pair_id.startswith('test-') for pair_id in ids))
        self.assertEqual(result['key']['split'], 'test')
        self.assertEqual(result['split_counts'], {'train':3,'validation':3,'test':3})

    def test_duplicate_prompt_source_group_leak_and_unverified_label_fail_before_load(self):
        original = json.loads(self.dataset.read_text())
        for mode in ('prompt','group','normalized-group','label','identical','duplicate-key'):
            dataset = json.loads(json.dumps(original))
            if mode == 'prompt': dataset['pairs'][3]['prompt'] = dataset['pairs'][0]['prompt']
            elif mode == 'group': dataset['pairs'][3]['source_group'] = dataset['pairs'][0]['source_group']
            elif mode == 'normalized-group': dataset['pairs'][3]['source_group'] = '  '+dataset['pairs'][0]['source_group'].upper()+'  '
            elif mode == 'label': dataset['pairs'][3]['label_source'] = 'model_self_approval'
            elif mode == 'identical': dataset['pairs'][3]['rejected'] = dataset['pairs'][3]['chosen']
            raw = json.dumps(dataset)
            if mode == 'duplicate-key': raw = raw.replace('"schema_version": 1','"schema_version": 1, "schema_version": 1')
            self.dataset.write_text(raw)
            with self.subTest(mode=mode), self.assertRaises(E.EvaluationError): self.evaluate()
        self.assertEqual(fixtures.FakeBackend.loads, 0)
        self.assertFalse((self.root/'receipts').exists())

    def test_invalid_or_tampered_receipt_is_not_reused(self):
        result = self.evaluate()
        receipt = self.root/'receipts'/(result['evaluation_id']+'.json')
        envelope = json.loads(receipt.read_text()); envelope['receipt']['scoring']['mean_loss'] = 123
        receipt.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(E.EvaluationError,'cached_receipt_invalid'): self.evaluate()
        self.assertEqual(fixtures.FakeBackend.loads, 1)

    def test_checksum_valid_but_wrong_model_split_or_training_claim_is_not_reused(self):
        result = self.evaluate()
        path = self.root/'receipts'/(result['evaluation_id']+'.json')
        original = json.loads(path.read_text())
        for field in ('model','split','trained','promoted','ids','mean','loss','logprob','tokens','delta'):
            envelope = json.loads(json.dumps(original)); receipt = envelope['receipt']
            if field == 'model': receipt['scoring']['policy_sha256'] = 'a'*64
            elif field == 'split': receipt['split_counts']['train'] = 100
            elif field == 'trained': receipt['weights_trained'] = True
            elif field == 'promoted': receipt['model_promoted'] = True
            elif field == 'ids': receipt['scoring']['pairs'][0]['pair_id'] = 'foreign-pair'
            elif field == 'mean': receipt['scoring']['mean_loss'] = 123
            elif field == 'loss': receipt['scoring']['pairs'][0]['loss'] = 123
            elif field == 'logprob': receipt['scoring']['pairs'][0]['reference_chosen_logprob'] -= 1
            elif field == 'tokens': receipt['scoring']['pairs'][0]['chosen_tokens'] = True
            elif field == 'delta': receipt['scoring']['pairs'][0]['delta'] = 123
            envelope['sha256'] = hashlib.sha256(E._encode(receipt)).hexdigest()
            path.write_text(json.dumps(envelope))
            with self.subTest(field=field), self.assertRaisesRegex(E.EvaluationError,'cached_receipt_invalid'):
                self.evaluate()
        self.assertEqual(fixtures.FakeBackend.loads, 1)

    def test_admission_denial_and_busy_lock_do_not_load_or_publish(self):
        def reject(*args): raise E.EvaluationError('evaluation_memory_budget_low')
        with self.assertRaisesRegex(E.EvaluationError,'evaluation_memory_budget_low'):
            self.evaluate(admission=reject)
        with E._locked_root(self.root/'receipts'):
            with self.assertRaisesRegex(E.EvaluationError,'evaluation_in_progress'): self.evaluate()
        self.assertEqual(fixtures.FakeBackend.loads, 0)
        self.assertEqual(list((self.root/'receipts').glob('*.json')), [])

    def test_nonregular_dataset_is_rejected_without_blocking(self):
        fifo = self.root/'dataset-fifo'; os.mkfifo(fifo,0o600)
        with self.assertRaisesRegex(E.EvaluationError,'dataset_not_regular_file'):
            self.evaluate(dataset_path=fifo)
        self.assertEqual(fixtures.FakeBackend.loads,0)

    def test_late_deadline_or_abort_after_temp_fsync_cannot_publish(self):
        root = self.root/'receipts'
        abort = self.root/'abort'; abort.mkdir(mode=0o700)
        controller = AbortController(abort)
        original_fsync = os.fsync
        for mode in ('deadline','abort'):
            token = controller.token()
            fired = []
            def fsync(fd):
                original_fsync(fd)
                if fired: return
                fired.append(True)
                if mode == 'deadline': clock.return_value = 1.001
                else: controller.abort('late_fixture_cancel')
            with self.subTest(mode=mode), E._locked_root(root) as fd, \
                    mock.patch.object(E.time,'monotonic',return_value=0) as clock, \
                    mock.patch.object(E.os,'fsync',side_effect=fsync), \
                    self.assertRaises((E.scorer.ScorerError,AldenCancelled)):
                E._publish(fd,root,'late.json',{'phase':'evaluated'},token,1)
            self.assertEqual(list(root.glob('*.json')),[])
            self.assertEqual(list(root.glob('.evaluation-*')),[])

    def test_failure_is_not_cached_and_scoring_identity_cannot_be_substituted(self):
        failed = self.evaluate(score_fn=lambda *a,**k:{'status':'eval_unavailable','reason':'dependency_unavailable'})
        self.assertEqual(failed['phase'],'unavailable')
        self.assertEqual(self.evaluate()['phase'],'evaluated')
        with self.assertRaisesRegex(E.EvaluationError,'scoring_identity_mismatch'):
            self.evaluate(version='0.1.8',score_fn=lambda *a,**k:{'status':'ok','policy_sha256':'foreign',
                'reference_sha256':'foreign','evaluated':3})

    def test_abort_during_scoring_cannot_publish_success(self):
        abort = self.root/'abort'; abort.mkdir(mode=0o700)
        controller = AbortController(abort); token = controller.token()
        def cancelled(*a,**k):
            controller.abort('fixture_cancel')
            return {'status':'eval_unavailable','reason':'aborted'}
        with self.assertRaises(E.scorer.ScorerError): self.evaluate(token=token,score_fn=cancelled)
        self.assertEqual(list((self.root/'receipts').glob('*.json')), [])
        self.assertEqual(list((self.root/'receipts').glob('.evaluation-*')), [])

    def test_version_cli_bypasses_golden_preparation_training_and_model_recommendation(self):
        with mock.patch.object(F,'prepare_dataset') as prepare, mock.patch.object(F,'run_training') as train, \
                mock.patch.object(F,'_resolve_model') as recommend, \
                mock.patch.object(E,'run_cli',return_value=0) as run, contextlib.redirect_stdout(io.StringIO()):
            result = F.main(['--model-evaluation-dataset',str(self.dataset),'--evaluation-version','0.1.6',
                             '--evaluation-root',str(self.root/'receipts'),'--json'])
        self.assertEqual(result,0);run.assert_called_once()
        prepare.assert_not_called();train.assert_not_called();recommend.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            F.main(['--model-evaluation-dataset',str(self.dataset),'--evaluation-version','0.1.6',
                    '--evaluation-root',str(self.root/'receipts'),'--train'])

    def test_private_roots_and_receipt_symlinks_preserve_foreign_targets(self):
        foreign = self.root/'foreign'; foreign.mkdir(mode=0o700)
        link = self.root/'receipts'; link.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(OSError): self.evaluate()
        self.assertEqual(list(foreign.iterdir()), [])
        link.unlink(); result = self.evaluate()
        receipt = link/(result['evaluation_id']+'.json')
        original = receipt.read_bytes(); receipt.unlink()
        target = foreign/'receipt.json'; target.write_bytes(original); target.chmod(0o600)
        receipt.symlink_to(target)
        with self.assertRaises(OSError): self.evaluate()
        self.assertEqual(target.read_bytes(), original)
        receipt.unlink(); os.mkfifo(receipt, 0o600)
        with self.assertRaisesRegex(E.EvaluationError,'unsafe_evaluation_file'): self.evaluate()

    @unittest.skipUnless(shutil.which('shasum'), 'build fixture requires shasum')
    def test_build_runs_configured_version_evaluation_and_stops_on_failure(self):
        repo = self.root/'build space'; scripts = repo/'scripts'; scripts.mkdir(parents=True)
        desktop = repo/'desktop'; (desktop/'src-tauri').mkdir(parents=True)
        (desktop/'package.json').write_text('{}')
        (desktop/'src-tauri/tauri.conf.json').write_text('{"version":"0.1.6"}')
        shutil.copyfile(Path(__file__).parents[1]/'scripts/build-alden-desktop.sh',scripts/'build-alden-desktop.sh')
        (scripts/'asset').write_bytes(b'fixture')
        (scripts/'menubar-bytecode.sha256').write_text(hashlib.sha256(b'fixture').hexdigest()+'  scripts/asset\n')
        (scripts/'build-alden-voice-audio.sh').write_text('#!/bin/sh\nexit 0\n')
        calls = repo/'calls.jsonl'
        (scripts/'auto_reply_finetune.py').write_text(
            'import json,os,sys\nfrom pathlib import Path\n'
            'with Path(os.environ["CALLS"]).open("a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'raise SystemExit(int(os.environ.get("EVAL_STATUS","0")))\n')
        binary = repo/'target/release/openkakao-cli'; binary.parent.mkdir(parents=True)
        app = desktop/'src-tauri/target/release/bundle/macos/Alden.app/Contents/MacOS/openkakao-alden-desktop'
        app.parent.mkdir(parents=True)
        for path in (binary, app): path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
        bin_dir = repo/'bin'; bin_dir.mkdir()
        for name in ('cargo','npm'):
            path = bin_dir/name; path.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$BUILD_CALLS"\n')
            path.chmod(0o755)
        build_calls = repo/'build-calls'
        env = {**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],
               'CALLS':str(calls),'BUILD_CALLS':str(build_calls)}
        for key in list(env):
            if key.startswith('ALDEN_EVALUATION_'): del env[key]
        def build():
            return subprocess.run(['/bin/sh',str(scripts/'build-alden-desktop.sh')],env=env,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        self.assertEqual(build().returncode,0); self.assertFalse(calls.exists())
        env['ALDEN_EVALUATION_DATASET'] = str(self.dataset)
        self.assertNotEqual(build().returncode,0); self.assertFalse(calls.exists())
        env.update(ALDEN_EVALUATION_PYTHON=sys.executable,ALDEN_EVALUATION_POLICY_DIR=str(self.policy),
                   ALDEN_EVALUATION_REFERENCE_DIR=str(self.policy),ALDEN_EVALUATION_ROOT=str(self.root/'receipts'),
                   ALDEN_EVALUATION_CHECKPOINT_FORMAT='mlx-serve-qwen3_5-converted',
                   ALDEN_EVALUATION_STATE_ROOT=str(self.root/'abort'))
        self.assertEqual(build().returncode,0)
        arguments = json.loads(calls.read_text().splitlines()[0])
        self.assertEqual(arguments[arguments.index('--evaluation-version')+1],'0.1.6')
        self.assertEqual(arguments[arguments.index('--model-evaluation-dataset')+1],str(self.dataset))
        self.assertEqual(arguments[arguments.index('--state-root')+1],str(self.root/'abort'))
        self.assertNotIn('--dpo-adapter-dir',arguments)
        env['ALDEN_EVALUATION_ADAPTER_DIR']=str(self.root/'private adapter')
        self.assertEqual(build().returncode,0)
        arguments=json.loads(calls.read_text().splitlines()[-1])
        self.assertEqual(arguments[arguments.index('--dpo-adapter-dir')+1],str(self.root/'private adapter'))
        baseline = build_calls.read_bytes(); env['EVAL_STATUS'] = '2'
        self.assertEqual(build().returncode,2); self.assertEqual(build_calls.read_bytes(),baseline)


if __name__ == '__main__':
    unittest.main()
