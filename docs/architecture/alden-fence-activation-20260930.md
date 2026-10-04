# Worker/native abort activation — 2026-09-30

## Delivered source and runtime

The [paired Python/native effect fence](alden-native-ax-fence-20260930.md) at commit `8af5f988e8ee3aae92942213533ceffa8435a551` was built, pushed to both existing branches and activated locally. Its GitHub checks passed **5/5** ([CI run](https://github.com/twoimo/openkakao-bot/actions/runs/36671239460)). The parent ran **111 focused integration cases**, strict CLI Clippy and formatting; the 20 worker cancellation cases also passed under the existing production Python 3.13 interpreter. This repeat is platform compatibility evidence, not 20 additional unique cases.

The parent reused the existing enrollment, exact ordered selectors and immutable config. Before control changes, all three rooms were ready and idle, with zero pending watcher candidates and zero nonterminal queue rows. The CLI, monitor plist and configuration were backed up privately. The existing monitor was unloaded, its exact user-owned watchdog was checked against the runtime entry and process identity, and SIGTERM invoked its normal child-drain protocol. All child exits were confirmed before packaging the replacement.

The fixed CLI path kept the same Apple Development designated requirement. Read-only aggregate preflight passed before and after staging: `network=false`, `will_send=false`, `workers_started=false`. All **20 immutable runtime assets** matched their manifest digests, and the new Python worker/abort files matched the reviewed source. The monitor was then activated with the replacement immutable runtime. No queue, candidate or watermark reset was performed; private queue backups remain available. No test message was sent.

## Bounded runtime readback

Four samples were recorded over **76.284 seconds**. The first showed all three rooms fenced during startup; its reason was not captured. The subsequent three samples showed **3/3 rooms ready and idle**, a healthy host, the new runtime's actual watchdog process, attempt 1 and **zero watchdog restarts**. The span between these three successful samples is **51 seconds** at the receipt's one-second timestamp resolution. Every sample retained nonregressing acknowledgement watermarks.

The receipt deliberately preserves `passed=false`: its condition required all four samples to be ready, which startup did not satisfy. The later three successful samples establish a bounded recovery observation. They do not establish improved conversational latency, lower skip rate, confirmed new delivery, future uptime or uninterrupted startup.

## Installed app and permissions

Alden was rebuilt from the same source and reinstalled using the existing backed-up installer. **28/28 installed files** matched the built bundle, with **zero differences**; strict deep codesign verification passed and LaunchAgent readback showed the installed process running. The desktop bundle is locally ad-hoc signed. Public Developer ID signing/notarization remains outstanding.

Computer Use read the actual macOS Accessibility screen: the existing three `openkakao-cli` entries, Terminal, Python 3.13 and Codex Computer Use were already enabled. No approval dialog or permission toggle was changed. A direct `getApp('/Applications/Alden.app')` screen request timed out after **5.106 seconds**, so installed visual rendering is not claimed. A physical global-shortcut-to-AX cancellation and its latency remain unverified.

The [structured activation receipt](alden-fence-activation-20260930.json) includes the failed startup sample and successful later samples. It omits chat names, IDs, message bodies and credentials. The preceding [confirmed queue repair](alden-queue-reconciliation-20260930.md) remains a separate historical operation; it was not repeated during deployment.
