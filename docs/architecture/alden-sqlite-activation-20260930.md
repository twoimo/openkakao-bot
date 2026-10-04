# SQLite replica runtime activation — 2026-09-30

Commit `410fc3961b66dc4fd9973db6192b6be068157ec0` was built and activated in immutable runtime `20260930T0610Z-sqlite-410fc39`. The existing three-room configuration, production Python 3.13, queue state and acknowledged watermarks were preserved. The old owned watchdog drained normally; queue/state backups were captured after drain. Both aggregate read-only preflights returned `valid=true`, `network=false`, `will_send=false`, `workers_started=false`. The stable CLI retained the same Apple Development designated requirement; **20/20** packaged assets matched their manifest and the installed stable CLI matched the newly signed build.

## Actual readback

Four discrete readbacks over **75.964 seconds** all found the new runtime process, watchdog attempt **1**, restart count **0**, **3/3 running and ready rooms**, and nonregressed watermarks. The context-sync timestamps advanced in all three rooms during these samples, establishing that the new runtime performed fresh source synchronization.

The complete strict idle/empty-queue receipt is **`passed=false`**, retained without alteration: sample two had a pending inbound item and `host_healthy=false`; sample three had seven nonterminal jobs and a processing worker; sample four had three nonterminal jobs. Natural traffic arrived during observation. Host health was true in **3/4** samples. This is not a four-sample idle pass or proof of continuous health.

The rebuilt `/Applications/Alden.app` matched **28/28 files**, with **zero differences**, passed strict deep codesign verification and had one running LaunchAgent process at readback. The app is locally ad-hoc signed. Computer Use confirmed the Sol Web response in the real Web app and found no approval dialog. Alden/menu-bar window acquisition timed out; the installed visual surface and a physical global abort/resume shortcut remain unverified. Public Developer ID signing/notarization remains outstanding.

## New natural conversation evidence

A read-only snapshot at **06:20:19 UTC** selected jobs created at or after **06:10:00 UTC**. Twelve incoming conversation jobs appeared in one configured room. Their queue ingress ages (`created_at − sent_at`) had median **6.975 s**, minimum **1.484 s**, maximum **27.224 s**. This sample differs from the earlier outage backlog; it is not a paired improvement measurement.

At that snapshot, **10** jobs were `burst_superseded`, **1** was `stale_backlog`, and **1** remained pending with `model_runner_failed`. No job in this window was confirmed `sent` by the queue. These are queue jobs, not twelve distinct human conversations; nine supersession edges were internal to the window, so the available edge count alone does not establish a complete conversation denominator. Worker logs separately showed image/video content rejected with HTTP **400** because the current local 27B server had no vision tower, and a formed reaction encountered an unavailable pre-send check before becoming stale. Neither condition is fixed by the DB snapshot patch. No manual test message, resend, cursor reset or queue reset was performed.

## CI follow-up

[CI run 36676596909](https://github.com/twoimo/openkakao-bot/actions/runs/36676596909) failed one CLI assertion: `RUST_BACKTRACE=1` added a backtrace to Anyhow's Debug formatting of the retry marker. The installed production monitor does not set this flag, but exact retry signaling must remain independent of debug environment settings. A separately scoped native Sol Web child completed the [typed termination correction](alden-sqlite-termination-20260930.md), and parent rerun passed **245 CLI tests** with that flag enabled; the original failed run remains recorded. This correction is newer than the activation recorded above and requires its own deployment evidence.

See the [structured activation/readback receipt](alden-sqlite-activation-20260930.json) and [source correction and fixtures](alden-sqlite-replica-20260930.md).
