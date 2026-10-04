# Worker exit evidence — 2026-09-27

Scope is metadata only. The Rust auto-reply root records the first unexpected
room-supervisor exit for one activation in `aggregate-status.json`. The bounded
record contains only `observed_at_unix_ms`, `monotonic_elapsed_seconds`,
`worker_kind`, canonical `room_selector` (`id:<chat_id>`), `pid`, `exit_code`,
`signal`, and `failure_class`. It does not add message, prompt, room-name, or
credential content.

Aggregate status writes are observational. Failure to write the initial,
periodic, first-exit, or stopped diagnostic snapshot does not terminate healthy
room supervisors.

The Python session watchdog copies worker-exit evidence only from an owned,
single-link, regular `0600` file within the size bound, opened with
`O_NOFOLLOW | O_NONBLOCK` so a FIFO cannot stall the reader. All eight fields must be present and type-valid, and the
`failure_class` must agree with `exit_code`/`signal`. Before each guardian spawn,
the watchdog snapshots the prior validated evidence and records the attempt
start time. After an unexpected guardian exit it accepts evidence only when it
was observed during the current attempt and differs from the prior snapshot;
otherwise the previous `last_worker_exit` remains unchanged. The copied value
is reduced to the fixed field set and tagged with `session_attempt`.

Focused fake-only regression coverage lives in
`tests/test_auto_reply_service_entry.py` for incomplete/invalid evidence,
unsafe evidence files, stale prior-attempt attribution, and fresh bounded
propagation. Rust unit coverage verifies aggregate persistence and first-exit
capture semantics.

Parent verification confirmed actual Web send, visible response and completion
for launcher trace `1f9cb6d4ddcc-7b744d8c`, with native route
`chatgpt-web/gpt-5.6-sol`, `xhigh`. The completed response was reused. Parent
review additionally reproduced and fixed two malformed-field exceptions
(non-ASCII numeric selector and non-string failure class), and added a bounded
FIFO regression. Deployment to the running immutable worker runtime is separate
from these source checks; past exits without this metadata cannot be recovered.
