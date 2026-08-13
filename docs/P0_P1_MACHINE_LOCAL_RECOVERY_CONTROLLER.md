# P0/P1 manager recovery controller boundary

This package is a static controller contract only. Its direct CLI accepts no
arguments, tokens, paths, modes, commands, environment authority, or client
payload. It returns a finite sanitized inspect/plan-only no-op and exits 0.
It never writes Config, a journal, a forensic snapshot, or any recovery state.

## Parent-child protocol

The internal protocol is an anonymous inherited-child-pipe framing contract.
The parent creates an OS anonymous pipe and spawns a synthetic child process;
there is no public in-process session constructor. The parent creates a fresh
random session identifier and key, binds the session to the locally measured
private-clone controller/executor head/tree and module/script hashes, the pinned
canonical head/tree, the exact 34-entry preservation digest, four consumer
bindings, fixed Config target names, and the plan fingerprint. Frames are
bounded canonical JSON with a strict schema/kind/payload enum, HMAC
authentication, expiry, sequence/nonce replay checks and transcript binding.

A manual child without the exact inherited parent pipe is refused. No command
line, environment variable, public socket or client payload can create a
session. Session internals are not returned as readiness or recovery data.

## Static preflight boundary

The controller may use `inspect_canonical()` and bounded executor inspection in
synthetic manager fixtures. It reads the guard's `operation_code` field and
never calls the snapshot-writing preflight helper or writes the canonical
forensic snapshot. Only the fixed 34 preservation entries
and four Config leaves are delegated to the read-only executor checks.

Every `inspect_plan_projection` result is `apply_blocked`, `execution:
not_run`, `dry_run: true`, and `apply_allowed: false`. Canonical preservation
guard failure, source-consumer mismatch, target mismatch, plan mismatch and
active Hub state are explicit blockers. Raw paths, hashes, file content, URLs,
commands and credentials are omitted from projections.

Most importantly, this library has no verifier, controller authority minting,
apply/resume implementation or machine executor. A future separately managed
controller process with an authenticated child-pipe protocol must own any
authority and ready-state decision. That process is not delivered or
authorized here. No machine session, Config apply, runtime/model recovery,
download, server, browser, provider or process action is claimed.
