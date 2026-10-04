# macOS Python/Rust resource contract

**Alden** is the menu-bar product name. `openkakao-cli` and this repository
retain the OpenKakao project identifiers.

The running executable selects the layout, independently of the current directory:

```text
Alden.app/Contents/
  MacOS/openkakao-alden-desktop
  Resources/
    scripts/auto-reply-menubar.py
    scripts/alden_voice.py
    scripts/<fixed support modules and CPython 3.11 bytecode>
    scripts/auto-reply-operator-prompts.json
    bin/openkakao-cli
```

Wake-word candidates live under `voice/models/experimental/` and are excluded
from the app bundle until held-out human-speech evaluation clears the release
false-accept gate. The desktop voice-start command remains disabled meanwhile.

`src-tauri/src/resource_layout.rs::DATA_FILES` is the allowlist; `tauri.conf.json`
maps every staged file to its exact destination. `build.rs` copies only these
files and the CLI from the checkout's `target/debug` or `target/release`, according
to the desktop build profile. It never traverses/copies `.venv*`, the home
directory, credentials, state, logs, caches, or arbitrary script directories.
Missing bytecode or CLI inputs stop packaging. The three frozen CPython 3.11
artifacts are pinned in this repository. The packaging step verifies their
presence but does not regenerate them.

Any `.app`, including an unsigned debug bundle, uses only its own Resources.
An incomplete/unsafe bundle never falls back to `CARGO_MANIFEST_DIR` or PATH.
All payload files and every parent component must be present and non-symlink;
files must be regular, nonempty and readable; CLI/interpreters must be executable.
Validation runs again before each bridge invocation. This is a local filesystem
boundary, not a signature verifier or protection against concurrent modification
by the same user. Signed distribution and runtime provisioning remain separate.

Python itself is **not bundled inside `Alden.app`**. Installed apps require
separately provisioned CPython 3.11 runtimes at these fixed paths (real
executables, no symlinks):

```text
~/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11
~/Library/Application Support/openkakao/runtimes/voice/bin/python3.11
```

Provision full runtimes, not just copied interpreter binaries. The menubar needs
CPython 3.11 for its frozen bytecode; the voice runtime needs the dependencies in
`voice/pyproject.toml`. Python runs with `-E -B -s` (ignore Python environment,
no bytecode writes, no user site packages). Missing runtimes report
`python_environment_missing_or_unsafe` / `voice_environment_missing` before
spawn. The app build itself does not download a runtime, dependencies, models,
or access the microphone; the tagged production release job separately fetches
only the pinned menubar runtime asset below and verifies it before publishing.
A conventional symlink-based venv is deliberately rejected.

Tagged Alden production releases carry a separate menubar runtime sidecar for a
fresh Apple Silicon Mac. It pins Astral `python-build-standalone` release
`20260924`, asset
`cpython-3.11.16+20260924-aarch64-apple-darwin-install_only_stripped.tar.gz`,
SHA-256
`e1d745b07b6acc0641dbb3237d3c5953deeeed182141bab2242684076fd86547`.
The Alden runtime archive is a byte-identical copy of that upstream asset; it is
not rebuilt from a developer machine. The release also includes the exact
provenance manifest and `install-alden-menubar-runtime.sh`. The runtime sidecar
does not inherit the app's Apple notarization claim; its trust boundary is the
pinned immutable upstream release, exact SHA-256, archive safety checks, arm64
Mach-O/rpath checks, and an isolated exact CPython 3.11.16 runtime probe.

For a fresh Mac, download the complete Alden release asset set while online:

```text
Alden-X.Y.Z-macos-arm64.zip
Alden-menubar-cpython-3.11.16-macos-arm64.tar.gz
alden-menubar-runtime-v1.json
install-alden-menubar-runtime.sh
alden-desktop-release-evidence-v1.json
SHA256SUMS.txt
```

Keep the matching `alden-vX.Y.Z` source checkout as well if you want the
repository installer/LaunchAgent cutover. In the download directory, verify all
payload hashes before going offline:

```sh
shasum -a 256 -c SHA256SUMS.txt
```

The following runtime installation path is fully offline. The installer refuses
wrong provenance/hash/architecture/version, unsafe archive members or symlinks,
unsafe install-path components, and an existing `menubar` runtime:

```sh
/bin/sh ./install-alden-menubar-runtime.sh install \
  ./Alden-menubar-cpython-3.11.16-macos-arm64.tar.gz \
  ./alden-menubar-runtime-v1.json

"$HOME/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" \
  -I -S -c 'import platform,sys; print(platform.python_implementation(), platform.python_version(), platform.machine())'
# expected: CPython 3.11.16 arm64
```

Then unpack the signed/notarized app ZIP. From the matching tagged source checkout,
the existing installer can consume that unpacked app without rebuilding it:

```sh
mkdir -p /tmp/alden-release
ditto -x -k /path/to/Alden-X.Y.Z-macos-arm64.zip /tmp/alden-release
OPENKAKAO_ALDEN_APP_SOURCE=/tmp/alden-release/Alden.app \
  /bin/sh scripts/install-alden-desktop.sh
```

That installer rechecks the fixed menubar CPython path before any app backup,
LaunchAgent staging, or cutover. The voice runtime remains a separate dependency;
the menubar runtime sidecar does not provision MLX models or voice packages.

Outside a bundle, debug builds may use the checkout and these development/test
overrides: `OPENKAKAO_RESOURCE_ROOT`, `OPENKAKAO_MENUBAR_SCRIPT`, `OPENKAKAO_BIN`,
`OPENKAKAO_PYTHON`, `OPENKAKAO_STATE_ROOT`,
`OPENKAKAO_LOGS_DIR`. Script/CLI overrides stay within the chosen resource root;
interpreter overrides must be absolute safe executable paths. The menubar uses
the existing uv CPython 3.11 path. Voice runtime provisioning is dormant while
the wake-model release gate is closed; use a copied-executable
voice environment when developing. Release executables outside a bundle fail
closed. Installed apps ignore these overrides.

State still uses `~/Library/Application Support/openkakao/auto-reply` (legacy
`bujamentor` enrollment fallback); logs still use `~/Library/Logs/AutoReplyMenu`.
Voice output and abort state stay under state, not Resources. Snapshot/action
timeouts (25/8 seconds), cancellation, and the 4 MiB stdout limit are unchanged.
Voice remains explicitly started, with its existing abort protocol; it does not
inherit the short snapshot timeout.

From the repository root, build the primary menu-bar bundle with the repository
script. The script stages the matching release CLI first and uses an unsigned
bundle unless `OPENKAKAO_SIGN_IDENTITY` is explicitly supplied:

```sh
sh scripts/build-alden-desktop.sh
```

Install the resulting app and its LaunchAgent only after reviewing the bundle.
The installer checks the fixed menubar CPython 3.11 runtime before it backs up
or changes any app or launchd state; a missing, unsafe, or nonworking interpreter
stops the cutover:

```sh
sh scripts/install-alden-desktop.sh
```

For a debug bundle, use `cd desktop && npm run tauri -- build --debug --bundles
app --no-sign` after preparing the debug CLI. Native macOS builds are supported
here. The `Alden Desktop Release` workflow builds a macOS arm64 app for exact
`alden-vX.Y.Z` tags and publishes it only after Developer ID signing, Apple
notarization, stapling, checksum verification, and GitHub asset readback. It
requires six `ALDEN_APPLE_*` signing and notarization secrets. Without them,
the release stops before packaging. The published `.app` ZIP itself still does
not contain CPython, MLX models, or voice dependencies; the separate pinned
menubar sidecar above satisfies only the fixed menubar CPython requirement. The
wake-model release gate remains closed. A signed package by itself is not an
end-to-end product check.

Focused verification: `cargo test --manifest-path desktop/src-tauri/Cargo.toml`,
and `npm test` / `npm run build` in `desktop`. Fixtures exercise moved installed
layouts, missing files, symlinks (including parents/dangling links), wrong types,
permissions, dev-only fallback/overrides and exact JSON resource declarations.
Process tests run only short synthetic Python snippets. Building/inspecting the
unsigned `.app` does not launch it or establish live KakaoTalk, mic, model,
TCC, signing, installation, or send success.
