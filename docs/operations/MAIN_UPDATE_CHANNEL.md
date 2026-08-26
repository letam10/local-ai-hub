# Main update channel

## User experience

The installed Local AI Hub product is designed for this flow:

1. The user merges an approved pull request into `main`.
2. Repository validation runs on the exact `main` SHA.
3. Only after validation succeeds, GitHub Actions builds the `local-ai-hub-main-update` artifact.
4. Dashboard checks the latest successful main artifact.
5. The user can review the commit list, choose **Cập nhật Local AI Hub**, then restart.
6. The stable `LocalAIHub.exe`, Desktop/Start Menu shortcuts and `DATA_ROOT` stay unchanged.

The installed application never runs `git pull` and never executes a Git checkout. The update artifact is an APP_ONLY package built from tracked source. The currently reviewed bundled runtime is copied into the new side-by-side payload, imports are checked, the payload is promoted under `versions/`, and `current.json` is switched atomically.

## Private repository authentication

The repository is private. The updater transport order is:

1. native GitHub OAuth Device Flow when `LOCALAIHUB_GITHUB_OAUTH_CLIENT_ID` is configured and a token has been validated against the private repository;
2. an already-installed and authenticated GitHub CLI (`gh`) fallback;
3. a bounded `oauth_configuration_required` or unavailable state.

The OAuth client id is not invented or hard-coded in this repository. Tokens are stored only through Windows Credential Manager under an installer-owned target; they never appear in Config, logs, UI payloads, diagnostics, process arguments or Git. If the repository owner has not supplied a valid OAuth App client id, native login remains explicitly blocked while the authenticated `gh` fallback continues to work.

Device login exposes only the verification URI, user code and bounded expiry/poll state. Logout removes the local credential; no remote token is copied or printed.

## Build identity versus product version

Ordinary main merges do not require a semantic version bump or a tag. Product version can remain, for example, `8.0.1`, while the installed build identity is the exact main commit SHA. New payload IDs use `main-<12 hex SHA>`.

Tags remain optional and user-controlled. Main merge readiness and this update channel do not depend on a tag.

## Safety and rollback

- A main artifact is accepted only from a successful push workflow on `main`.
- The artifact manifest binds product ID, product version, channel, exact source commit, payload ID, file count and archive SHA-256.
- ZIP extraction is bounded and rejects traversal/symlink entries.
- Update is blocked while active jobs exist.
- Old payloads are retained.
- The previous `current.json` pointer is retained under installer-owned `update-state/` and can be restored with the rollback API.
- A pending-health marker is written before activation. The new payload clears it only after matching product/API identity and build SHA are healthy; a failed proof restores the previous verified pointer without touching `DATA_ROOT`.

### Candidate API preflight and restart safety

An APP_ONLY candidate is not activated after ZIP/hash/import validation alone.
Before `previous-current.json`, `pending-health.json`, or `current.json` is
changed, the staged payload starts its bundled API on an owned ephemeral
loopback port with an isolated temporary data root.  The child must remain
alive while `/health` proves the product/API identity and the exact
`build_source_commit`/`build_payload_id`; a failed or timed-out probe preserves
the candidate and its bounded preflight log and leaves the current pointer
untouched.  The production API port and persistent data root are not used by
this probe.

Restart is guarded by `src.app.update_watchdog`, an updater-owned helper started
from the currently running old verified payload/runtime before the old desktop
closes.  It waits for the old desktop PID, starts
the stable launcher, and accepts the new payload only after the pending-health
marker is cleared by matching health/build identity.  On crash, identity
mismatch, or timeout it terminates only the launcher process it started,
atomically restores the verified previous pointer, and relaunches the stable
launcher once.  Foreign loopback listeners and `DATA_ROOT` are never killed or
modified.

If the desktop API cannot start, the native shell leaves the indefinite loading
state and shows a Vietnamese recovery screen with a bounded error code and
payload short SHA.  It offers only **Thử lại** and, when the installer-owned
previous pointer and manifest are independently verified, **Khôi phục phiên bản
trước**.
- Models, Environments, runtime assets in `DATA_ROOT`, Output, Config, Projects and user media are never included in the GitHub artifact and are not moved/deleted by the updater.

## Runtime strategy and limitation

The initial channel is `APP_ONLY`/`reuse-current`: source/UI/workflow changes reuse the installed bundled Python runtime. The update contract records the app protocol, minimum launcher compatibility and exact source commit. This keeps ordinary updates small and avoids reinstalling the launcher/runtime for every merge. The updater performs an import preflight before switching the pointer.

A change that genuinely requires a new Python/native runtime must use a separately reviewed `FULL` update/installer path. A FULL contract must carry a bundled runtime version and inventory hash; the APP_ONLY channel refuses such a payload and never silently downloads or replaces the Core runtime. The checked-in builder exposes the contract boundary, while Windows launcher/runtime replacement remains a separately validated prerequisite.

## One-time bootstrap

An installation created before this updater existed cannot update itself into the updater. After the updater PR is merged and main CI creates the first artifact, the existing machine needs **one final manual stable-payload synchronization** (for example through Codex/repair tooling) to install an updater-capable payload. After that bootstrap, future successful main merges are discoverable from Dashboard without rebuilding the stable launcher.
