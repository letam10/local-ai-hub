# P0/P1 manager recovery controller boundary

This package is a static refusal boundary only. Its direct CLI accepts no
arguments, tokens, paths, modes, commands, environment authority, or client
payload. It returns one finite sanitized no-op and exits 0. It never writes
Config, a journal, a forensic snapshot, or recovery state.

## Current boundary

Every call to the library preflight entrypoint returns
`preflight_blocked`, `execution: not_run`, `dry_run: true`,
`apply_allowed: false`, and `reason: manager_broker_required` before reading
any path, plan, callback, pipe, authorization object, guard, or resource
input. There is no parent/child transport, verifier, authority minting,
listener probe, apply/resume implementation, or ready-state in this package.
Thus an importer, forged callback, fake pipe, HMAC, or mapping cannot obtain an
accepted or ready result.

The canonical preservation identity remains ca998/tree
`0d6852a20d78701b948cedcd0f970b4d3bb5e27c8` with the exact 34-entry digest.
The de12 commit is executor provenance only; it is not a controller checkout
pin. A future private controller clone must measure its actual candidate
HEAD/tree and the controller/executor hashes from that same clone, alongside
the fixed consumer bindings and plan fingerprint.

## Future design B (not delivered)

A separately manager-owned external broker would need to create an anonymous
inherited child handle, authenticate bounded frames, enforce expiry/nonce and
replay rules, and own the bounded listener-table observation for fixed port
8765. It would then provide a sanitized broker projection to a separately
authorized controller. The broker must bind its measured controller/executor
identity, the ca998 canonical preservation guard, fixed targets and the exact
34-entry manifest before any decision.

That trust anchor must be a distinct service identity or remote manager
authority with private signing and replay material inaccessible to the same
user. A user-scope DPAPI secret, static Python key, or in-memory secret is not
sufficient. The external manager broker and any machine executor are not
delivered or authorized by this package.

All future failure, unknown, present-target, active-Hub, source-consumer and
guard states must remain finite blockers. No path may become an operational,
installed, running, ready, launchable, or apply-allowed claim merely from a
static plan. No machine session, Config apply, runtime/model recovery,
download, server, browser, provider, or process action is claimed here.
