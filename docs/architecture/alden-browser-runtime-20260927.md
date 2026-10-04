# Dedicated Alden browser runtime

Date: 2026-09-27 KST. No KakaoTalk test message is sent by these checks.

## Runtime contract

Browser jobs select the fixed interpreter at `~/Library/Application Support/openkakao/runtimes/browser/bin/python3.11`. Snapshot, settings and other menu jobs retain the existing menubar interpreter. Voice retains its separate runtime. There is no installed runtime override or fallback to a checkout venv.

The provisioner pins CPython **3.11.16** for macOS arm64, browser-use **0.13.10**, Playwright **1.63.0**, Chromium/headless revision **1243**, and ffmpeg revision **1011**. It verifies the CPython archive and frozen requirements SHA-256, checks archive traversal and entry types, installs only local hashed wheels with `--no-index --require-hashes`, and publishes a new runtime directory after readiness checks. An existing destination is refused rather than overwritten. Chromium is copied to that runtime's own `ms-playwright` directory.

The Rust bridge validates the fixed interpreter and browser path, supplies `PLAYWRIGHT_BROWSERS_PATH` and `ANONYMIZED_TELEMETRY=false`, and retains Python `-E -B -s`, installed resource validation, stdin tasks, output/timeout limits and the existing emergency-abort grace. The menu entrypoint redirects Python browser progress output to stderr before printing its single JSON result. Browser inference remains bound by the existing loopback-only adapter; this runtime change does not add remote inference.

## Provisioning

Prepare the upstream CPython archive returned by `scripts/alden-browser-runtime.sh source-url`. Prepare a wheelhouse using a working CPython 3.11 pip:

```sh
python3.11 -m pip download --require-hashes --only-binary=:all: \
  --dest /private/tmp/alden-browser-wheelhouse \
  -r browser/requirements-runtime.txt

sh scripts/alden-browser-runtime.sh preflight \
  browser/requirements-runtime.txt /private/tmp/alden-browser-wheelhouse \
  "$HOME/Library/Caches/ms-playwright"

sh scripts/alden-browser-runtime.sh install \
  /private/tmp/alden-cpython-3.11.16.tar.gz \
  browser/requirements-runtime.txt /private/tmp/alden-browser-wheelhouse \
  "$HOME/Library/Caches/ms-playwright"
```

The source Playwright cache must contain the three pinned revisions. Provisioning neither restarts the app nor starts a browser or Kakao worker. Updating an existing runtime requires a separately reviewed replacement procedure; the installer deliberately does not provide one.

## Parent review and focused checks

The native Web child used `chatgpt-web/gpt-5.6-sol`, `xhigh`. Launcher checkpoints for `4f8f5fa21a42-39dd7be5` confirmed send acceptance and a visible response. The child then ended with the transport error `ChatGPT changed a completed text block that was already streamed to Codex`; it did not provide a completed final answer. The parent preserved the partial patch, reviewed it, corrected archive-case ordering so `python/../` cannot pass the `python/*` allowlist, added a traversal regression and libpython architecture validation, and continued integration locally. No base-model substitution was used.

Focused checks on the installed voice CPython 3.11 runtime and the desktop Rust target:

- Runtime payload/preflight tests: **6/6**, including tampered requirements, traversal and wrong/symlinked browser revision.
- Menu browser action tests: **5/5**, including stdout isolation.
- Rust Python bridge tests: **69/69**, including fixed action-specific routing, stdin task transport and abort deadlines.

The downloaded wheelhouse contains **110 wheels**, **98,209,110 bytes**. All downloads passed the frozen requirements hash contract. That is disk payload, not resident memory or an improvement measurement. Actual runtime provisioning and installed-app result readback are recorded below when completed; these source checks alone do not establish installed-app operation.

## Local provisioning readback

The parent provisioned the previously absent fixed runtime without overwriting the menubar or voice directories. The new runtime readiness probe returned `ready=true`, both exact package versions, `chromium_installed=true`, and adapter binding `browser`. This verifies installed dependency/binding readiness. An actual installed-app browser job and the Rust routing deployment remain separate checks.

## Installed backend check at approximately 23:24 KST

The app was rebuilt from committed source `51ebd9c` and installed after strict ad-hoc signature verification. All **24** bundled Python resources matched that commit; the built and installed app had **zero byte differences**. LaunchAgent readback showed one installed Alden main process, PID **65142**. The installer retained backup `20260927T232241-64947`. Developer ID signing and notarization are still absent.

The parent invoked the installed `auto-reply-menubar.py --action tool-browser` using the fixed dedicated browser interpreter, an isolated private job state directory, task input on stdin, and a minimal environment without cloud credentials. The example.com title task returned `ok=true`, `status=completed`, `result=Example Domain` in **28.769829 s**. Stdout parsed as exactly one JSON object; browser progress appeared on stderr (**1,432 bytes**). Dedicated Chromium process counts were **0 before / 0 after** the job. An independent Playwright DOM read with the provisioned Chromium returned HTTP **200** and the same `Example Domain` title in **0.943118 s**.

This checks the installed Python entrypoint and browser runtime. It does **not** exercise a Tauri invoke event, graphical caller, physical emergency shortcut, or general web accuracy. The single task does not establish a latency improvement. The [raw live receipt](alden-installed-browser-live-20260927.json) records the source and these boundaries. After the job, the Kakao host remained healthy with **3/3** rooms ready and idle; its existing watchdog was still on attempt **3**, restart count **2**, child PID **68702**. The new worker-exit metadata is source-only until an immutable worker runtime is separately rolled out.
