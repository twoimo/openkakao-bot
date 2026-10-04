from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import alden_dpo_adapter as A, alden_dpo_training as T
from scripts import alden_dpo_scorer as S, alden_model_evaluation as E, auto_reply_finetune as F
from scripts.alden_abort import AbortController
from tests import test_alden_dpo_scorer as fixtures

DATASET=Path(__file__).parent/'fixtures/alden-model-evaluation/preferences.json'


class DpoContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve()
        self.config={'schema_version':1,'objective':'dpo','base_sha256':'a'*64,
                     'checkpoint_format':'auto','num_layers':1,
                     'lora_parameters':{'rank':2,'scale':4,'dropout':0,'keys':['mlp.down_proj']}}

    def candidate(self):
        root=self.root/'candidate';root.mkdir(mode=0o700)
        for name,raw in (('adapter_config.json',json.dumps(self.config).encode()),('adapters.safetensors',b'fixture')):
            p=root/name;p.write_bytes(raw);p.chmod(0o600)
        return root

    def test_invalid_layers_bindings_and_parameter_contract_fail_before_import(self):
        for field,value in (('num_layers',0),('num_layers',-1),('num_layers',True),('objective','sft'),('base_sha256','b'*64)):
            config=json.loads(json.dumps(self.config));config[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(A.AdapterError):
                A.validate_config(config,'a'*64,'auto')
        for field,value in (('keys',['lm_head']),('keys',['mlp.down_proj']*2),('dropout',.1),('rank',0),('rank',True)):
            config=json.loads(json.dumps(self.config));config['lora_parameters'][field]=value
            with self.subTest(field=field),self.assertRaises(A.AdapterError):A.validate_config(config,'a'*64,'auto')

    def test_snapshot_rejects_symlink_fifo_public_mode_and_foreign_base(self):
        root=self.candidate();asset=root/'adapters.safetensors'
        with A.snapshot(root,base_sha256='a'*64,checkpoint_format='auto') as value:
            self.assertEqual((value['path']/'adapters.safetensors').read_bytes(),b'fixture')
            copy=value['path']
        self.assertFalse(copy.exists())
        with self.assertRaises(A.AdapterError):A.fingerprint(root,base_sha256='b'*64,checkpoint_format='auto')
        original=asset.read_bytes();asset.unlink();target=self.root/'foreign';target.write_bytes(original);target.chmod(0o600)
        asset.symlink_to(target)
        with self.assertRaises(OSError):A.fingerprint(root,base_sha256='a'*64,checkpoint_format='auto')
        self.assertEqual(target.read_bytes(),original);asset.unlink();os.mkfifo(asset,0o600)
        with self.assertRaisesRegex(A.AdapterError,'unsafe_adapter_asset'):A.fingerprint(root,base_sha256='a'*64,checkpoint_format='auto')
        asset.unlink();asset.write_bytes(original);asset.chmod(0o644)
        with self.assertRaisesRegex(A.AdapterError,'unsafe_adapter_asset'):A.fingerprint(root,base_sha256='a'*64,checkpoint_format='auto')

    def test_invalid_training_options_and_abort_do_not_load_model_or_create_root(self):
        policy=fixtures._checkpoint(self.root,'model')
        options=dict(dataset_path=DATASET,policy_dir=policy,reference_dir=policy,training_root=self.root/'train',checkpoint_format='auto')
        for key,value in (('steps',0),('rank',-1),('num_layers',0),('learning_rate',float('nan')),('seed',True)):
            with self.subTest(key=key),self.assertRaises(T.TrainingError):T.train_offline(**options,**{key:value})
        abort=self.root/'abort';abort.mkdir(mode=0o700);controller=AbortController(abort);controller.abort('fixture')
        with self.assertRaises(S.ScorerError):T.train_offline(**options,token=controller.token())
        self.assertFalse((self.root/'train').exists())

    def test_training_cli_is_separate_from_sft_evaluation_and_preparation(self):
        with mock.patch.object(T,'run_cli',return_value=0) as run, \
                mock.patch.object(F,'prepare_dataset') as prepare,mock.patch.object(F,'run_training') as sft:
            self.assertEqual(F.main(['--dpo-train-dataset',str(DATASET),'--dpo-training-root',str(self.root/'train')]),0)
        run.assert_called_once();prepare.assert_not_called();sft.assert_not_called()
        for flag in ('--train','--evaluate','--prepare-only','--dpo-score-local','--evaluation-final-test'):
            with self.subTest(flag=flag),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                F.main(['--dpo-train-dataset',str(DATASET),'--dpo-training-root',str(self.root/'train'),flag])

    def test_missing_mlx_runtime_returns_unavailable_json(self):
        output=io.StringIO()
        with mock.patch.object(T,'train_offline',side_effect=ImportError('missing MLX')),contextlib.redirect_stdout(output):
            code=F.main(['--dpo-train-dataset',str(DATASET),'--dpo-training-root',str(self.root/'train'),
                         '--state-root',str(self.root/'abort')])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(output.getvalue())['reason'],'ImportError')
        self.assertFalse((self.root/'train').exists())


@unittest.skipUnless(importlib.util.find_spec('mlx') is not None,'MLX numerical runtime required')
class DpoGradientTests(unittest.TestCase):
    def setUp(self):
        import mlx.core as mx
        import mlx.nn as nn
        self.mx,self.nn=mx,nn
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve();self.policy=fixtures._checkpoint(self.root,'model')
        class Tokenizer:
            def encode(self,text,add_special_tokens=False):return list(text.encode())
            def apply_chat_template(self,messages,**kwargs):return '<u>'+messages[0]['content']+'</u><a>'
        class MLP(nn.Module):
            def __init__(self):
                super().__init__();self.down_proj=nn.QuantizedLinear.from_linear(nn.Linear(32,256),group_size=32,bits=4)
            def __call__(self,x):return self.down_proj(x)
        class Layer(nn.Module):
            def __init__(self):super().__init__();self.mlp=MLP()
            def __call__(self,x):return self.mlp(x)
        class Model(nn.Module):
            def __init__(self):super().__init__();self.embedding=nn.Embedding(256,32);self.layer=Layer()
            @property
            def layers(self):return [self.layer]
            def __call__(self,ids,cache=None):return self.layer(self.embedding(ids))
        class Backend(S.MlxBackend):
            def __init__(self,checkpoint,adapter=None):
                mx.random.seed(123);model=Model();model.eval()
                if adapter is not None:A.apply(model,adapter,mx)
                super().__init__(model,Tokenizer(),mx,nn,lambda _:[])
        self.factory=Backend

    def train(self,**extra):
        return T.train_offline(dataset_path=DATASET,policy_dir=self.policy,reference_dir=self.policy,
            training_root=self.root/'training',checkpoint_format='auto',steps=3,rank=2,
            learning_rate=.001,backend_factory=self.factory,**extra)

    def test_real_gradient_updates_only_adapter_and_reloads_with_heldout_separation(self):
        result=self.train()
        self.assertTrue(result['weights_trained']);self.assertFalse(result['model_promoted'])
        self.assertEqual(result['split_counts'],{'train':3,'validation':3,'test':3})
        self.assertFalse(result['final_test_evaluated']);self.assertTrue(result['base_checkpoint_unchanged'])
        self.assertEqual(result['backend_kind'],'injected_backend')
        self.assertTrue(result['adapter_reload_verified']);self.assertTrue(all(x>0 for x in result['gradient_l1']))
        for phase in ('train_before','train_after'):self.assertTrue(all(x['pair_id'].startswith('train-') for x in result[phase]['pairs']))
        for phase in ('validation_before','validation_after'):self.assertTrue(all(x['pair_id'].startswith('validation-') for x in result[phase]['pairs']))
        candidate=self.root/'training'/result['candidate_id']
        self.assertEqual({p.name for p in candidate.iterdir()},{'adapters.safetensors','adapter_config.json','receipt.json'})

    def test_abort_after_reload_removes_owned_stage_without_publishing_candidate(self):
        controller=AbortController(self.root/'abort');loads=[];original=self.factory
        class Backend(original):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs);loads.append(self);self.number=len(loads)
            def close(self):
                super().close()
                if self.number==3:controller.abort('cancel before publication')
        self.factory=Backend
        with self.assertRaisesRegex(S.ScorerError,'aborted'):self.train(token=controller.token())
        self.assertEqual(len(loads),3)
        self.assertEqual({p.name for p in (self.root/'training').iterdir()},{'evaluation.lock'})

    def test_non_finite_gradient_does_not_publish_candidate(self):
        def factory(model,loss_fn):
            return lambda *args:(self.mx.array(float('nan')),model.trainable_parameters())
        with mock.patch.object(self.nn,'value_and_grad',side_effect=factory), \
                self.assertRaisesRegex(T.TrainingError,'invalid_or_zero_dpo_gradient'):
            self.train()
        self.assertEqual({p.name for p in (self.root/'training').iterdir()},{'evaluation.lock'})

    def test_abort_before_fresh_load_skips_third_model_and_cleans_stage(self):
        controller=AbortController(self.root/'abort');loads=[];original=self.factory
        class Backend(original):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs);loads.append(self);self.number=len(loads)
            def close(self):
                super().close()
                if self.number==2:controller.abort('cancel before fresh load')
        self.factory=Backend
        with self.assertRaisesRegex(S.ScorerError,'aborted'):self.train(token=controller.token())
        self.assertEqual(len(loads),2)
        self.assertTrue(all(x.model is None for x in loads))
        self.assertEqual({p.name for p in (self.root/'training').iterdir()},{'evaluation.lock'})

    def test_stage_setup_failure_closes_loaded_policy(self):
        loads=[];original=self.factory;mkdir=os.mkdir
        def factory(*args,**kwargs):
            value=original(*args,**kwargs);loads.append(value);return value
        def fail(path,*args,**kwargs):
            if str(path).startswith('.training-'):raise OSError('stage creation failed')
            return mkdir(path,*args,**kwargs)
        self.factory=factory
        with mock.patch.object(T.os,'mkdir',side_effect=fail),self.assertRaisesRegex(OSError,'stage creation failed'):
            self.train()
        self.assertEqual(len(loads),2)
        self.assertTrue(all(x.model is None for x in loads))

    def test_commit_rejects_replaced_stage_and_preserves_foreign_directory(self):
        root=self.root/'training';controller=AbortController(self.root/'abort');token=controller.token()
        guard=token.commit_guard;stages=[];synced=[];fsync=os.fsync
        def sync(fd):
            info=os.fstat(fd);synced.append((info.st_dev,info.st_ino));return fsync(fd)
        @contextlib.contextmanager
        def replace():
            with guard():
                stage=next(root.glob('.training-*'));info=stage.stat()
                self.assertIn((info.st_dev,info.st_ino),synced)
                preserved=root/'preserved-stage';stage.rename(preserved);stage.mkdir(mode=0o700)
                (stage/'foreign.txt').write_text('preserve');stages.append(stage)
                yield
        with mock.patch.object(token,'commit_guard',side_effect=replace),mock.patch.object(T.os,'fsync',side_effect=sync), \
                self.assertRaisesRegex(T.TrainingError,'training_stage_changed'):
            self.train(token=token)
        self.assertEqual((stages[0]/'foreign.txt').read_text(),'preserve')
        self.assertTrue((root/'preserved-stage/adapters.safetensors').exists())
        self.assertFalse(list(root.glob('dpo-*')))

    def test_adapter_scorer_does_not_reuse_trained_policy_as_reference(self):
        result=self.train();candidate=self.root/'training'/result['candidate_id']
        pairs,_=E.load_dataset(DATASET)
        scored=S.score_local_dpo_pairs(pairs['validation'],policy_dir=self.policy,reference_dir=self.policy,
            policy_adapter_dir=candidate,backend_factory=self.factory)
        self.assertEqual(scored['status'],'ok');self.assertFalse(scored['reference_reused'])
        self.assertFalse(scored['identical_checkpoint_delta_zero'])
        self.assertEqual(scored['policy_adapter_sha256'],result['adapter_sha256'])
        def score(rows,**kw):return S.score_local_dpo_pairs(rows,backend_factory=self.factory,**kw)
        options=dict(dataset_path=DATASET,policy_dir=self.policy,reference_dir=self.policy,
            policy_adapter_dir=candidate,version='test',evaluation_root=self.root/'receipts',checkpoint_format='auto',score_fn=score)
        evaluated=E.evaluate_version(**options);cached=E.evaluate_version(**options)
        self.assertEqual(evaluated['phase'],'evaluated');self.assertTrue(cached['cached'])
        self.assertEqual(evaluated['key']['policy_adapter_sha256'],result['adapter_sha256'])

    def test_adapter_loader_rejects_base_weight_injection_and_wrong_shapes(self):
        backend=self.factory(self.policy)
        config={'schema_version':1,'objective':'dpo','base_sha256':'a'*64,'checkpoint_format':'auto','num_layers':1,
                'lora_parameters':{'rank':2,'scale':4,'dropout':0,'keys':['mlp.down_proj']}}
        folder=self.root/'assets';folder.mkdir(mode=0o700)
        value={'config':config,'path':folder}
        for name,shape,reason in (('embedding.weight',(256,32),'adapter_parameter_names_mismatch'),
                                 ('layer.mlp.down_proj.lora_a',(1,1),'adapter_parameter_shape_or_dtype')):
            fresh=self.factory(self.policy);params=A.install(fresh.model,config)
            arrays={k:self.mx.zeros(v.shape) for k,v in params.items()}
            if name=='embedding.weight':arrays[name]=self.mx.zeros(shape)
            else:arrays[name]=self.mx.zeros(shape)
            self.mx.save_safetensors(str(folder/'adapters.safetensors'),arrays)
            with self.assertRaisesRegex(A.AdapterError,reason):A.apply(backend.model,value,self.mx)
            backend.close();backend=self.factory(self.policy)
        backend.close()

    def test_prompt_logits_are_excluded_from_loss_and_gradient(self):
        mx,nn=self.mx,self.nn
        class Fixed(nn.Module):
            def __init__(self):super().__init__();self.logits=mx.zeros((1,4,8))
            def __call__(self,ids):return self.logits
        model=Fixed();prepared={'full_ids':(1,2,3,4,5),'response_mask':(False,False,True,True)}
        fn=nn.value_and_grad(model,lambda m:T.response_logprob(m,prepared,mx,nn))
        value,gradient=fn(model);mx.eval(value,gradient)
        self.assertEqual(float(mx.sum(mx.abs(gradient['logits'][:,:2])).item()),0)
        self.assertGreater(float(mx.sum(mx.abs(gradient['logits'][:,2:])).item()),0)
        self.assertAlmostEqual(float(value.item()),-2*__import__('math').log(8),places=5)


if __name__=='__main__':unittest.main()
