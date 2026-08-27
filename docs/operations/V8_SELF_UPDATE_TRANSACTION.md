# V8 self-update transaction and restart ownership

The installed updater is a two-phase transaction. The route
`POST /api/app-update/prepare` downloads and verifies the GitHub Actions
artifact, imports the candidate, starts it on an ephemeral loopback port for
health/bootstrap preflight, and stores a bounded `staged-update.json` record.
It does not change `current.json` or create `pending-health.json`.

The native restart bridge is the only phase-2 owner. After the desktop close
gate has verified that all jobs owned by this desktop are idle, it revalidates
the staged payload under the installer update lock, records the previous
pointer, writes `pending-health.json`, and atomically switches `current.json`.
It then writes a nonce-bound `restart-session.json`, starts the watchdog from
the old verified runtime, authorizes the controller's
`update_restart_committed` state, and performs exactly one native window
destroy. A failed watchdog launch, close authorization, or window destroy
rolls the pointer back immediately and clears pending state.

The watchdog waits for the exact old desktop PID. The new desktop publishes its
selected loopback API port, payload ID, source commit, API PID and frontend
status into the nonce-bound session record. The watchdog probes only that port;
it never scans all ports and never terminates a foreign listener. A same-
installation API whose build identity does not match the current payload is
classified as `compatible_installation_wrong_build`; the candidate therefore
selects a fallback loopback port and starts its own bundled API.

An external compatible API with zero active jobs may remain running while the
desktop closes. External active jobs are observable but are never offered a
desktop cancellation action; update commit is blocked until the external owner
is idle. Unknown ownership or an invalid health response is a fail-closed
close/update veto, never a fabricated active count.

`pending-health.json` is removed only after both exact API build identity and
the explicit frontend-ready bridge handshake match. If the candidate API or
frontend fails to become healthy, the watchdog restores the previous verified
payload and relaunches it. The old payload and rollback pointer remain intact;
models, environments, runtime data, user media and `DATA_ROOT` are not moved
or rewritten by this transaction.

GitHub update checks use at most three attempts for transient network/service
failures (bounded backoff). Authentication, permission, malformed JSON,
identity and hash failures are permanent and are not retried.
