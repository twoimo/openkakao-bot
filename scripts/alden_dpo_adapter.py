"""Private, base-bound LoRA snapshots for offline Alden DPO candidates."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import stat
import tempfile
import time
from pathlib import Path

MAX_ADAPTER_BYTES = 256 * 1024 * 1024
ALLOWED_KEYS = ("self_attn.q_proj", "self_attn.v_proj", "self_attn.o_proj",
                "linear_attn.in_proj_qkv", "linear_attn.out_proj", "mlp.down_proj")


class AdapterError(ValueError):
    pass


def _check(abort_check, deadline):
    if abort_check is not None and abort_check():
        raise AdapterError("aborted")
    if deadline is not None and time.monotonic() >= deadline:
        raise AdapterError("deadline_exceeded")


def validate_config(config, base_sha256, checkpoint_format):
    fields = {"schema_version", "objective", "base_sha256", "checkpoint_format",
              "num_layers", "lora_parameters"}
    if (not isinstance(config, dict) or set(config) != fields
            or type(config["schema_version"]) is not int or config["schema_version"] != 1
            or config["objective"] != "dpo" or config["base_sha256"] != base_sha256
            or config["checkpoint_format"] != checkpoint_format):
        raise AdapterError("adapter_binding_mismatch")
    params = config["lora_parameters"]
    if (type(config["num_layers"]) is not int or not 1 <= config["num_layers"] <= 16
            or not isinstance(params, dict) or set(params) != {"rank", "scale", "dropout", "keys"}
            or type(params["rank"]) is not int or not 1 <= params["rank"] <= 32
            or type(params["scale"]) not in (int, float) or not math.isfinite(params["scale"])
            or not 0 < params["scale"] <= 64 or type(params["dropout"]) not in (int, float)
            or params["dropout"] != 0 or not isinstance(params["keys"], list)
            or not 1 <= len(params["keys"]) <= len(ALLOWED_KEYS)
            or any(not isinstance(key, str) or key not in ALLOWED_KEYS for key in params["keys"])
            or len(set(params["keys"])) != len(params["keys"])):
        raise AdapterError("adapter_config_invalid")
    return config


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AdapterError("duplicate_adapter_json_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(AdapterError("invalid_adapter_number")))


@contextlib.contextmanager
def snapshot(directory, *, base_sha256, checkpoint_format, abort_check=None, deadline=None):
    """Copy only validated regular assets once; model loading uses this owned copy."""
    path = Path(directory)
    if not path.is_absolute():
        raise AdapterError("adapter_path_not_absolute")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    root_fd = os.open(path, flags | os.O_DIRECTORY)
    try:
        root = os.fstat(root_fd)
        if root.st_uid != os.geteuid() or stat.S_IMODE(root.st_mode) != 0o700:
            raise AdapterError("unsafe_adapter_root")
        with tempfile.TemporaryDirectory(prefix="alden-adapter-") as temporary:
            target = Path(temporary); target.chmod(0o700)
            digest = hashlib.sha256()
            for name, limit in (("adapter_config.json", 16 * 1024),
                                ("adapters.safetensors", MAX_ADAPTER_BYTES)):
                _check(abort_check, deadline)
                fd = os.open(name, flags, dir_fd=root_fd)
                with os.fdopen(fd, "rb") as source:
                    info = os.fstat(source.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                            or not 0 < info.st_size <= limit):
                        raise AdapterError("unsafe_adapter_asset")
                    digest.update(name.encode() + b"\0")
                    copied = 0
                    with (target/name).open("xb") as output:
                        os.chmod(target/name, 0o600)
                        while chunk := source.read(min(1024 * 1024, limit + 1 - copied)):
                            copied += len(chunk)
                            if copied > limit:
                                raise AdapterError("adapter_asset_too_large")
                            output.write(chunk); digest.update(chunk)
                            _check(abort_check, deadline)
                    after = os.fstat(source.fileno())
                    if (copied != info.st_size or info.st_mtime_ns != after.st_mtime_ns
                            or info.st_size != after.st_size):
                        raise AdapterError("adapter_asset_changed")
            config = validate_config(_json((target/"adapter_config.json").read_bytes()),
                                     base_sha256, checkpoint_format)
            yield {"config": config, "path": target, "sha256": digest.hexdigest()}
    finally:
        os.close(root_fd)


def fingerprint(directory, **kwargs):
    with snapshot(directory, **kwargs) as value:
        return value["sha256"]


def install(model, config):
    """Freeze the base and add only declared LoRA matrices; zero layers is forbidden."""
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from mlx.utils import tree_flatten
    if config["num_layers"] > len(model.layers):
        raise AdapterError("adapter_layer_count_exceeds_model")
    model.freeze()
    linear_to_lora_layers(model, config["num_layers"], config["lora_parameters"])
    parameters = dict(tree_flatten(model.trainable_parameters()))
    if not parameters or any(not name.endswith((".lora_a", ".lora_b")) for name in parameters):
        raise AdapterError("non_lora_trainable_parameter")
    return parameters


def apply(model, value, mx):
    parameters = install(model, value["config"])
    weights = mx.load(str(value["path"]/"adapters.safetensors"))
    if set(weights) != set(parameters):
        raise AdapterError("adapter_parameter_names_mismatch")
    for name, array in weights.items():
        if array.shape != parameters[name].shape or not mx.issubdtype(array.dtype, mx.floating):
            raise AdapterError("adapter_parameter_shape_or_dtype")
        if not bool(mx.all(mx.isfinite(array)).item()):
            raise AdapterError("adapter_parameter_non_finite")
    model.load_weights(list(weights.items()), strict=False)
    model.eval()
