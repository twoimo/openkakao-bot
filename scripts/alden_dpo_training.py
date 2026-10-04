"""Bounded offline DPO gradient updates; publishes private adapters, never promotes."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import secrets
import shutil
import time
from pathlib import Path

try:
    from scripts import alden_dpo_scorer as S, alden_dpo_adapter as A, alden_model_evaluation as E
except ImportError:
    import alden_dpo_scorer as S
    import alden_dpo_adapter as A
    import alden_model_evaluation as E

MAX_TRAIN_TOKENS = 512


class TrainingError(ValueError):
    pass


def response_logprob(model, prepared, mx, nn):
    """Differentiable teacher forcing. Prompt tokens contribute no loss."""
    ids = prepared["full_ids"]
    logits = model(mx.array([list(ids[:-1])]))
    logps = nn.log_softmax(logits.astype(mx.float32))
    targets = mx.array([list(ids[1:])])[..., None]
    gathered = mx.take_along_axis(logps, targets, axis=-1)[..., 0]
    mask = mx.array([list(prepared["response_mask"])])
    return mx.sum(mx.where(mask, gathered, 0.0))


def dpo_tensor_loss(model, chosen, rejected, reference, beta, mx, nn):
    chosen_sum = response_logprob(model, chosen, mx, nn)
    rejected_sum = response_logprob(model, rejected, mx, nn)
    ref_chosen, ref_rejected = reference
    delta = (chosen_sum - mx.stop_gradient(mx.array(ref_chosen))) - (
        rejected_sum - mx.stop_gradient(mx.array(ref_rejected)))
    return mx.logaddexp(mx.array(0.0), -beta * delta)


def _score_backend(backend, pairs, abort, deadline):
    rows = []
    for index, pair in enumerate(pairs):
        S._check_abort(abort, deadline)
        chosen, rejected = S.prepare_pair_tokens(backend.tokenizer, pair, index)
        if max(len(chosen["full_ids"]), len(rejected["full_ids"])) > MAX_TRAIN_TOKENS:
            raise TrainingError("training_sequence_too_long")
        rows.append({"pair_id": chosen["pair_id"],
                     "chosen": S.score_fixed_response(backend, chosen, abort_check=abort, deadline=deadline),
                     "rejected": S.score_fixed_response(backend, rejected, abort_check=abort, deadline=deadline)})
    return rows


def _summary(policy, reference, beta):
    loss_fn = S._default_dpo_loss_fn()
    rows = []
    for p, r in zip(policy, reference):
        S.validate_pair_scores(p, r)
        loss = loss_fn(chosen_logprobs=p["chosen"]["logprobs"], rejected_logprobs=p["rejected"]["logprobs"],
                       ref_chosen_logprobs=r["chosen"]["logprobs"], ref_rejected_logprobs=r["rejected"]["logprobs"],
                       beta=beta, require_reference=True)
        if loss["status"] != "ok":
            raise TrainingError("training_evaluation_invalid")
        rows.append({"pair_id": p["pair_id"], "loss": loss["loss"], "delta": loss["delta"],
                     "policy_chosen_logprob": sum(p["chosen"]["logprobs"]),
                     "policy_rejected_logprob": sum(p["rejected"]["logprobs"])})
    if not rows or len(policy) != len(reference):
        raise TrainingError("training_evaluation_count_mismatch")
    return {"pairs": rows, "mean_loss": sum(r["loss"] for r in rows)/len(rows),
            "chosen_logprob_higher_count": sum(r["policy_chosen_logprob"] > r["policy_rejected_logprob"] for r in rows)}


def train_offline(*, dataset_path, policy_dir, reference_dir, training_root, checkpoint_format,
                  steps=10, learning_rate=1e-5, beta=.1, rank=8, num_layers=1, seed=42,
                  deadline_seconds=300, token=None, backend_factory=None, admission=None):
    for name, value, maximum in (("steps", steps, 100), ("rank", rank, 32), ("layers", num_layers, 16)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise TrainingError("invalid_training_"+name)
    if type(seed) is not int or not 0 <= seed <= 2**32-1:
        raise TrainingError("invalid_training_seed")
    if (type(learning_rate) not in (int,float) or not math.isfinite(learning_rate)
            or not 0 < learning_rate <= .001 or type(beta) not in (int,float)
            or not math.isfinite(beta) or not 0 < beta <= 1
            or type(deadline_seconds) not in (int,float) or not math.isfinite(deadline_seconds)
            or not 1 <= deadline_seconds <= 1800):
        raise TrainingError("invalid_training_numeric_option")
    started = time.monotonic(); deadline = started + deadline_seconds
    abort = token.is_cancelled if token is not None else None
    S._check_abort(abort, deadline)
    splits, dataset_sha = E.load_dataset(Path(dataset_path))
    source_sha = E.source_fingerprint()
    policy = S._checkpoint(policy_dir, "missing_policy_dir")
    reference = S._checkpoint(reference_dir, "missing_reference_dir")
    checkpoint_format = S._checkpoint_format(checkpoint_format)
    root = Path(training_root)
    with E._locked_root(root) as root_fd:
        base_sha = S.checkpoint_fingerprint(policy, abort, deadline)
        reference_sha = base_sha if policy == reference else S.checkpoint_fingerprint(reference, abort, deadline)
        if S.tokenizer_fingerprint(policy, abort, deadline) != S.tokenizer_fingerprint(reference, abort, deadline):
            raise TrainingError("training_tokenizer_mismatch")
        if admission is not None:
            admission(policy, reference)
        import mlx.core as mx
        import mlx.nn as nn
        import mlx.optimizers as optim
        from mlx.utils import tree_flatten
        mx.random.seed(seed)
        factory = backend_factory or (lambda p: S.MlxBackend.from_checkpoint(p, checkpoint_format=checkpoint_format))
        pairs = splits["train"] + splits["validation"]
        S._check_abort(abort, deadline)
        reference_backend = factory(reference)
        sums = None
        try:
            reference_backend.model.eval()
            reference_scores = _score_backend(reference_backend, pairs, abort, deadline)
            reference_backend.model.freeze(); reference_backend.model.train()
            reference_sums=[]
            for index,pair in enumerate(splits["train"]):
                S._check_abort(abort,deadline)
                c,r=S.prepare_pair_tokens(reference_backend.tokenizer,pair,index)
                sums=[response_logprob(reference_backend.model,p,mx,nn) for p in (c,r)]
                mx.eval(sums)
                values=tuple(float(v.item()) for v in sums)
                if any(not math.isfinite(v) or v>0 for v in values):
                    raise TrainingError("invalid_reference_training_logprob")
                reference_sums.append(values)
                del sums
        finally:
            sums = None
            reference_backend.close()
        reference_digest = hashlib.sha256(E._encode([reference_scores,reference_sums])).hexdigest()
        temporary = ".training-" + secrets.token_hex(16)
        candidate = "dpo-" + secrets.token_hex(16)
        backend = stage_info = None
        frozen=current=parameters=initial=learned=gradient_fn=optimizer=grads=loss=None
        try:
            S._check_abort(abort, deadline)
            backend = factory(policy)
            os.mkdir(temporary, 0o700, dir_fd=root_fd)
            stage_info=os.stat(temporary,dir_fd=root_fd,follow_symlinks=False)
            stage = root/temporary
            backend.model.eval()
            before = _score_backend(backend, pairs, abort, deadline)
            config = {"schema_version":1,"objective":"dpo","base_sha256":base_sha,
                      "checkpoint_format":checkpoint_format,"num_layers":num_layers,
                      "lora_parameters":{"rank":rank,"scale":20,"dropout":0,"keys":list(A.ALLOWED_KEYS)}}
            A.validate_config(config, base_sha, checkpoint_format)
            frozen = dict(tree_flatten(backend.model.parameters()))
            parameters = A.install(backend.model, config)
            initial = {k: mx.array(v) for k,v in parameters.items()}
            untrained = _score_backend(backend, pairs, abort, deadline)
            train_tokens = [S.prepare_pair_tokens(backend.tokenizer,p,i) for i,p in enumerate(splits["train"])]
            optimizer = optim.Adam(learning_rate=learning_rate)
            def loss_fn(model, chosen, rejected, ref):
                return dpo_tensor_loss(model, chosen, rejected, ref, beta, mx, nn)
            gradient_fn = nn.value_and_grad(backend.model, loss_fn)
            backend.model.train(); mx.reset_peak_memory(); losses=[]; gradient_norms=[]
            for step in range(steps):
                S._check_abort(abort, deadline)
                index = step % len(train_tokens)
                loss, grads = gradient_fn(backend.model, *train_tokens[index], reference_sums[index])
                mx.eval(loss, grads)
                norm = sum(float(mx.sum(mx.abs(g)).item()) for _,g in tree_flatten(grads))
                value = float(loss.item())
                if not math.isfinite(norm) or not math.isfinite(value) or norm <= 0:
                    raise TrainingError("invalid_or_zero_dpo_gradient")
                S._check_abort(abort, deadline)
                optimizer.update(backend.model, grads); mx.eval(backend.model.parameters(), optimizer.state)
                losses.append(value); gradient_norms.append(norm)
                mx.clear_cache()
            peak = int(mx.get_peak_memory())
            backend.model.eval()
            after = _score_backend(backend, pairs, abort, deadline)
            learned = dict(tree_flatten(backend.model.trainable_parameters()))
            if set(learned) != set(initial) or not any(not bool(mx.array_equal(learned[k],initial[k]).item()) for k in learned):
                raise TrainingError("adapter_weights_unchanged")
            current = {k.replace(".linear.","."):v for k,v in tree_flatten(backend.model.parameters()) if not k.endswith((".lora_a",".lora_b"))}
            if set(current) != set(frozen) or any(current[k] is not frozen[k] for k in frozen):
                raise TrainingError("frozen_base_parameter_changed")
            if reference_digest != hashlib.sha256(E._encode([reference_scores,reference_sums])).hexdigest():
                raise TrainingError("frozen_reference_changed")
            (stage/"adapter_config.json").write_bytes(E._encode(config)); (stage/"adapter_config.json").chmod(0o600)
            previous_umask=os.umask(0o077)
            try:
                mx.save_safetensors(str(stage/"adapters.safetensors"), learned)
            finally:
                os.umask(previous_umask)
            (stage/"adapters.safetensors").chmod(0o600)
            frozen=current=parameters=initial=learned=gradient_fn=optimizer=grads=loss=None
            backend.close()
            S._check_abort(abort, deadline)
            restored = factory(policy)
            try:
                with A.snapshot(stage, base_sha256=base_sha, checkpoint_format=checkpoint_format,
                                abort_check=abort, deadline=deadline) as snapshot:
                    adapter_sha = snapshot["sha256"]
                    A.apply(restored.model, snapshot, mx)
                reloaded = _score_backend(restored, pairs, abort, deadline)
            finally:
                restored.close()
            for trained, loaded in zip(after, reloaded):
                for label in ("chosen","rejected"):
                    a,b=trained[label]["logprobs"],loaded[label]["logprobs"]
                    if len(a)!=len(b) or any(not math.isclose(x,y,rel_tol=1e-5,abs_tol=1e-5) for x,y in zip(a,b)):
                        raise TrainingError("adapter_reload_score_mismatch")
            if S.checkpoint_fingerprint(policy,abort,deadline)!=base_sha or (
                    policy!=reference and S.checkpoint_fingerprint(reference,abort,deadline)!=reference_sha):
                raise TrainingError("base_checkpoint_changed")
            if E.source_fingerprint()!=source_sha:
                raise TrainingError("training_source_changed")
            count=len(splits["train"])
            receipt={"schema_version":1,"phase":"trained","objective":"dpo","candidate_id":candidate,
                     "base_sha256":base_sha,"reference_sha256":reference_sha,"adapter_sha256":adapter_sha,
                     "dataset_sha256":dataset_sha,"checkpoint_format":checkpoint_format,
                     "backend_kind":"injected_backend" if backend_factory is not None else "local_mlx_checkpoint",
                     "source_sha256":source_sha,
                     "steps":steps,"learning_rate":learning_rate,"beta":beta,"seed":seed,
                     "split_counts":{k:len(v) for k,v in splits.items()},"final_test_evaluated":False,
                     "training_step_losses":losses,"gradient_l1":gradient_norms,
                     "train_before":_summary(before[:count],reference_scores[:count],beta),
                     "train_untrained_adapter":_summary(untrained[:count],reference_scores[:count],beta),
                     "train_after":_summary(after[:count],reference_scores[:count],beta),
                     "validation_before":_summary(before[count:],reference_scores[count:],beta),
                     "validation_untrained_adapter":_summary(untrained[count:],reference_scores[count:],beta),
                     "validation_after":_summary(after[count:],reference_scores[count:],beta),
                     "adapter_reload_verified":True,"base_checkpoint_unchanged":True,
                     "frozen_base_parameter_objects_preserved":True,"reference_scores_unchanged":True,
                     "reference_training_forward":"uncached_train_mode_response_mask",
                     "reference_evaluation_forward":"cached_eval_mode_teacher_forcing",
                     "weights_trained":True,"model_promoted":False,"quality_improvement_claimed":False,
                     "peak_bytes":peak,"peak_bytes_scope":"training_after_model_load",
                     "runtime_seconds":round(time.monotonic()-started,6),"versions":S._versions()}
            (stage/"receipt.json").write_bytes(E._encode(receipt)); (stage/"receipt.json").chmod(0o600)
            for name in ("adapter_config.json","adapters.safetensors","receipt.json"):
                with (stage/name).open("rb") as handle: os.fsync(handle.fileno())
            stage_fd=os.open(temporary,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root_fd)
            try:
                actual=os.fstat(stage_fd)
                if (actual.st_dev,actual.st_ino)!=(stage_info.st_dev,stage_info.st_ino):
                    raise TrainingError("training_stage_changed")
                os.fsync(stage_fd)
            finally:
                os.close(stage_fd)
            guard=token.commit_guard() if token is not None else contextlib.nullcontext()
            with guard:
                actual,held=root.lstat(),os.fstat(root_fd)
                if (actual.st_dev,actual.st_ino)!=(held.st_dev,held.st_ino):
                    raise TrainingError("training_root_changed")
                actual=os.stat(temporary,dir_fd=root_fd,follow_symlinks=False)
                if (actual.st_dev,actual.st_ino)!=(stage_info.st_dev,stage_info.st_ino):
                    raise TrainingError("training_stage_changed")
                S._check_abort(abort,deadline)
                os.rename(temporary,candidate,src_dir_fd=root_fd,dst_dir_fd=root_fd)
            os.fsync(root_fd)
            return receipt
        finally:
            frozen=current=parameters=initial=learned=gradient_fn=optimizer=grads=loss=None
            if backend is not None:
                backend.close()
            try:
                actual=os.stat(temporary,dir_fd=root_fd,follow_symlinks=False)
                if stage_info is not None and (actual.st_dev,actual.st_ino)==(stage_info.st_dev,stage_info.st_ino):
                    shutil.rmtree(temporary,dir_fd=root_fd)
            except FileNotFoundError:
                pass


def run_cli(args):
    try:
        from scripts.alden_abort import AbortController, AldenCancelled
    except ImportError:
        from alden_abort import AbortController, AldenCancelled
    support=Path.home()/"Library/Application Support/openkakao"
    legacy,modern=support/"bujamentor",support/"auto-reply"
    state=args.state_root or (modern if (modern/"enrollment.json").is_file() or not (legacy/"enrollment.json").is_file() else legacy)
    try:
        result=train_offline(dataset_path=args.dpo_train_dataset,policy_dir=args.dpo_policy_dir,
            reference_dir=args.dpo_reference_dir,training_root=args.dpo_training_root,
            checkpoint_format=args.dpo_checkpoint_format,steps=args.dpo_training_steps,
            learning_rate=args.dpo_training_learning_rate,beta=args.dpo_beta,rank=args.dpo_training_rank,
            num_layers=args.dpo_training_layers,deadline_seconds=args.dpo_training_deadline,
            token=AbortController(state).token(),admission=E.memory_admission)
    except (TrainingError,A.AdapterError,S.ScorerError,E.EvaluationError,AldenCancelled,OSError,ValueError,TypeError,ImportError) as exc:
        result={"phase":"unavailable","reason":str(exc) if isinstance(exc,(TrainingError,A.AdapterError,S.ScorerError,E.EvaluationError,AldenCancelled)) else type(exc).__name__,
                "weights_trained":False,"model_promoted":False}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result["phase"]=="trained" else 2
