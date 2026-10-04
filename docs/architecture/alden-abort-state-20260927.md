# Alden global abort state protocol

This file defines the shared Python/Rust protocol for the latched Alden
emergency stop. The state lives in `alden-abort.json`; cooperating writers use
`alden-abort.lock`. Readers never take the lock.

## State schema

Schema version 1 is one JSON object with exactly four keys:

```json
{"schema_version":1,"epoch":1,"latched":true,"reason":"operator requested stop"}
```

- `schema_version`: integer `1`; booleans are invalid.
- `epoch`: integer `0..9007199254740991`; booleans are invalid.
- `latched`: JSON boolean.
- `reason`: string of at most 96 characters with no Unicode control
  characters.
- A latched state requires a nonempty reason. Existing arbitrary
  `abort(reason)` strings remain valid within the bounds above.
- An unlatched state is valid only as epoch `0` with an empty reason, or with
  reason `human_resume`.

A missing state file means the initial unlatched state
`epoch=0, latched=false, reason=""`. Every other malformed, unknown,
unreadable, oversized, or unsafe state is fail closed and must be treated as a
latched error.

## Filesystem contract

The state root is a real directory owned by the effective user and private
mode `0700`. The state and lock files must be regular files owned by the
effective user, mode `0600`, with link count `1`.

Readers open the root and state with `O_NOFOLLOW|O_NONBLOCK`, validate with
`fstat`, reject files larger than 4096 bytes, and perform a bounded read of
at most 4097 bytes. Symlinks, dangling symlinks, FIFOs, directories, hard
links, wrong ownership, and wrong mode all fail closed. A reader does not
spin or retry.

## Writer and resume protocol

Python and Rust writers must use this sequence:

1. Open/create the private `alden-abort.lock` with
   `O_NOFOLLOW|O_NONBLOCK`, validate owner/mode/type/link count, then take
   `flock(LOCK_EX|LOCK_NB)`. Both implementations retry at 5 ms intervals
   for at most 250 ms, then refuse the transition; a stuck writer cannot
   block an emergency request indefinitely.
2. While holding the lock, read and validate the current state with the same
   rules as readers. Never replace an invalid or unsafe existing state.
3. Reject epoch overflow at `9007199254740991`.
4. Increment the epoch and create the next state. Resume is an explicit human
   API only, is allowed only from a valid latched state, and writes
   `reason="human_resume"`.
5. Write a uniquely named new temp file with create-new/`O_EXCL`, mode
   `0600`; fsync it; atomically replace `alden-abort.json`; clean up an
   uncommitted temp on error.
6. Release the flock. Python abort callbacks run only after the new abort state
   has been committed and after the file lock is no longer held.

There is no automatic unlatch path.

## Token semantics

An `AbortToken` remembers the epoch observed at creation. It permanently
cancels when explicitly cancelled, when it observes a latched/error state, or
when it observes any epoch different from its birth epoch. Once cancelled, a
token never becomes live again, including after a human resume. Callers that
need post-resume work must create a new token from the resumed state.

## Error handling

Python exposes the fail-closed classification through `AbortState.error` and
`AbortState.is_error`. Writers raise `AbortStateError` for unsafe state or
lock files, invalid resume attempts, invalid reasons, and epoch overflow.
These errors must not trigger a fallback overwrite of the shared state.
