# V8.0.1 Startup Payload Repair

This corrective lane repairs the installed application payload without
rebuilding or replacing the stable `LocalAIHub.exe` launcher and without
rewriting user shortcuts or persistent data.

## Stable installation authority

The installation identity is derived from the stable installation root plus
the persistent data root. A versioned payload under `versions/<version>/app`
is not an installation identity and may be replaced atomically during a
repair/update. Desktop and API health therefore compute the same opaque
identity when `LOCALAIHUB_INSTALL_ROOT`, `LOCALAIHUB_APP_ROOT`, and
`LOCALAIHUB_DATA_ROOT` are all different.

## Bundled runtime authority

In installed mode, the desktop resolves the active `current.json` payload and
uses its validated `runtime/Python312/pythonw.exe`. It does not consult
`DATA_ROOT/Environments/core`, `DATA_ROOT/Environments/hub`, `PATH`, system
Python, or `LOCALAIHUB_PYTHON`. Those resolver paths remain available only to
development/legacy non-installed mode.

## Owned startup failure and diagnostics

The identity-free frontend compatibility handshake is limited to the explicit
historical `8.0.1` payload.  Any other version-looking pointer (including test
or hotfix labels) is rejected.  When `product.json`, `installation.json`,
`current.json` metadata or a version manifest is present, the handshake also
verifies the fixed product/install identity and manifest digest; a pending
health marker always keeps the strict recovery path active.

Every API process spawned by the desktop remains owned until readiness succeeds.
Early exit, timeout, malformed/foreign health, identity mismatch, or WebView
startup failure terminates and reaps only that child tree. Existing external
listeners are never stopped.

Startup diagnostics are appended to the canonical data-root `Logs` authority,
never to a mutable version payload. Records contain only bounded fields:
selected port, probe class, runtime location class, child exit code, and a
fixed startup code (`API_STARTUP_EXITED`, `API_IDENTITY_MISMATCH`, or
`API_STARTUP_TIMEOUT`). The WebView error page exposes the fixed code and a
Diagnostics instruction, not an exception, local path, or traceback.

## Repair boundary

An authorized repair stages and verifies a new version payload, including its
manifest and bundled runtime, then activates the pointer atomically. The
stable launcher binary, Desktop/Start Menu shortcuts, icon fields, and
`DATA_ROOT` remain unchanged. A real shortcut smoke must prove the active
payload starts its own bundled API while an incompatible external listener
occupies the default port, and must prove that failed startup leaves no owned
fallback listener.
