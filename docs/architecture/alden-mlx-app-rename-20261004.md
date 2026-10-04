# Alden 0.3.15 — installed MLX bundle resolution

The current Mac has signed **MLX-Serve.app 26.10.1**, bundle identifier `com.dalcu.mlx-core`. `/Applications/MLX Core.app/Contents/MacOS/mlx-serve` no longer exists. Alden's launcher used only that old path, so preparing a local model stopped at `launch_executable_unsafe` before reaching the model or memory checks. The already running 27B process remains alive with its original PID; its mapped executable reports the previous bundle path. That is not authority to restart or adopt it.

The launcher now recognizes the two fixed application paths. It prefers the legacy executable when present and otherwise checks the renamed bundle. It does not search PATH, create an alias, repair the MLX installation, change model IDs or lower the memory requirement. Existing signature, exact command, port, PID-start, ownership and memory checks remain responsible for authorizing a launch. An unsafe legacy candidate, including a dangling executable symlink, is not bypassed by selecting another bundle.

## Actual preflight and limits

On this Mac, old-path validation returned `launch_executable_unsafe`; resolving the renamed path passed the existing signature/executable check. A real source CLI action for the fixed Flash-Next iQ model progressed through executable, model-directory, models-directory and port checks, then returned **`launch_memory_insufficient`**. Port 11235 remained closed and no model server was started. This verifies the repaired resolution and retained safety gate, not successful Flash-Next inference.

The subsequent memory observation was **53,005,877,248 bytes** available under Alden's existing `vm_stat` rule, versus a **64,424,509,440-byte** requirement:

`deficit = max(0, required − available) = 11,418,632,192 bytes`

That is approximately **49.37 GiB available / 60 GiB required / 10.63 GiB short**. This is a separate observation after the rejected action; it is not the exact internal sample used by that action. Memory pressure's percentage is a different OS metric and cannot replace the launch budget. No reduction of precision, memory checks or OS reserve is claimed as an optimization.

The local iQ tensor index references **3,167 tensors in 101 shards**, with **0 missing shards**. The n-gram file is **32,000,153,976 bytes** on disk. This inventory proves file presence and index coverage, not complete checksum validation, resident memory, model loading, Korean quality or response latency. The previously pinned model revision is preserved. The pinned model-card URL could not be read through the web tool during this check; current compatibility evidence comes from the installed CLI and local files.

## Verification

The focused lifecycle/action suite passed **68 cases**; the complete menu suite passed **177 cases** on the pinned CPython 3.11 runtime. Regressions cover the renamed bundle, legacy preference, unsafe alias, missing bundles and retained signature rejection. Installed delivery separately checks source-resource and built/installed hashes, model preference files and the five existing model, embedding, voice and automation process identities.

Installed 0.3.15 passed the same preflight through its packaged script with the native bridge's `-E -B -s` Python flags. The app contains exactly **41** expected files, all matching the build, and passes deep/strict ad-hoc signature verification. Three model preference files, the absent residency file, three configuration/CLI baselines and all five existing process identities stayed unchanged. An earlier direct audit call omitted `-B` and created eight unsealed bytecode files; those exact audit-created files were privately backed up and removed. The corrected audit created no files in the bundle. The normal native bridge already uses `-B`; no runtime restriction was weakened or added to hide the audit error.

This update fixes one observed software blocker. It does not establish a latency improvement or a new running Flash-Next service. The previous [Raw/delta delivery](alden-raw-delta-20261004.md), history pages, model defaults and approved automation scope are preserved. Developer ID/notary credentials, physical voice/primary interaction, actual Dot invocation and the remaining model/media targets continue to be tracked under the full goal.
