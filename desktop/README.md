# macOS Python/Rust resource contract

The running executable selects the layout, independently of the current directory:

```text
OpenKakao Jarvis.app/Contents/
  MacOS/openkakao-jarvis-desktop
  Resources/
    scripts/auto-reply-menubar.py
    scripts/jarvis_voice.py
    scripts/<fixed support modules and CPython 3.11 bytecode>
    scripts/auto-reply-operator-prompts.json
    voice/models/hey_jarvis_ko_ridge.onnx
    bin/openkakao-cli
```

`src-tauri/src/resource_layout.rs::DATA_FILES` is the allowlist; `tauri.conf.json`
maps every staged file to its exact destination. `build.rs` copies only these
files and the CLI from the checkout's `target/debug` or `target/release`, according
to the desktop build profile. It never traverses/copies `.venv*`, the home
directory, credentials, state, logs, caches, or arbitrary script directories.
Missing bytecode or CLI inputs stop packaging. The three existing frozen
CPython 3.11 artifacts must be supplied from a trusted build; they are currently
ignored by Git and cannot be reconstructed by this packaging step.

Any `.app`, including an unsigned debug bundle, uses only its own Resources.
An incomplete/unsafe bundle never falls back to `CARGO_MANIFEST_DIR` or PATH.
All payload files and every parent component must be present and non-symlink;
files must be regular, nonempty and readable; CLI/interpreters must be executable.
Validation runs again before each bridge invocation. This is a local filesystem
boundary, not a signature verifier or protection against concurrent modification
by the same user. Signed distribution and runtime provisioning remain separate.

Python itself is **not bundled**. Installed apps require separately provisioned
CPython 3.11 runtimes at these fixed paths (real executables, no symlinks):

```text
~/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11
~/Library/Application Support/openkakao/runtimes/voice/bin/python3.11
```

Provision full runtimes, not just copied interpreter binaries. The menubar needs
CPython 3.11 for its frozen bytecode; the voice runtime needs the dependencies in
`voice/pyproject.toml`. Python runs with `-E -B -s` (ignore Python environment,
no bytecode writes, no user site packages). Missing runtimes report
`python_environment_missing_or_unsafe` / `voice_environment_missing` before
spawn. No runtime/dependency/model download or microphone access occurs in the
packaging step. A conventional symlink-based venv is deliberately rejected.

Outside a bundle, debug builds may use the checkout and these development/test
overrides: `OPENKAKAO_RESOURCE_ROOT`, `OPENKAKAO_MENUBAR_SCRIPT`, `OPENKAKAO_BIN`,
`OPENKAKAO_PYTHON`, `OPENKAKAO_VOICE_PYTHON`, `OPENKAKAO_STATE_ROOT`,
`OPENKAKAO_LOGS_DIR`. Script/CLI overrides stay within the chosen resource root;
interpreter overrides must be absolute safe executable paths. Defaults are the
existing uv CPython 3.11 path and `.venv-voice/bin/python`; use a copied-executable
voice environment when developing. Release executables outside a bundle fail
closed. Installed apps ignore these overrides.

State still uses `~/Library/Application Support/openkakao/auto-reply` (legacy
`bujamentor` enrollment fallback); logs still use `~/Library/Logs/AutoReplyMenu`.
Voice output and abort state stay under state, not Resources. Snapshot/action
timeouts (25/8 seconds), cancellation, and the 4 MiB stdout limit are unchanged.
Voice remains explicitly started, with its existing abort protocol; it does not
inherit the short snapshot timeout.

From the repository root, prepare the matching CLI, then build an unsigned app:

```sh
cargo build --bin openkakao-cli
cd desktop
npm run tauri -- build --debug --bundles app --no-sign
```

For release, first use `cargo build --release --bin openkakao-cli`, then omit
`--debug`. Native macOS builds are supported here; cross/universal CLI staging
and signed/notarized distribution need separate packaging work.

Focused verification: `cargo test --manifest-path desktop/src-tauri/Cargo.toml`,
and `npm test` / `npm run build` in `desktop`. Fixtures exercise moved installed
layouts, missing files, symlinks (including parents/dangling links), wrong types,
permissions, dev-only fallback/overrides and exact JSON resource declarations.
Process tests run only short synthetic Python snippets. Building/inspecting the
unsigned `.app` does not launch it or establish live KakaoTalk, mic, model,
TCC, signing, installation, or send success.
