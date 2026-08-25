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

The repository is private. The first implementation intentionally does **not** embed a GitHub PAT/token in Local AI Hub. It uses an already-installed and authenticated GitHub CLI (`gh`) as the transport boundary. If `gh` is absent or not authenticated, Dashboard explains that update checking is unavailable and leaves the installed payload untouched.

A future GitHub Device Flow / Windows Credential Manager transport can replace the CLI transport without changing the update artifact or pointer contract.

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
- Models, Environments, runtime assets in `DATA_ROOT`, Output, Config, Projects and user media are never included in the GitHub artifact and are not moved/deleted by the updater.

## Runtime strategy and limitation

The initial channel is `reuse-current`: source/UI/workflow changes reuse the installed bundled Python runtime. This keeps ordinary updates small and avoids reinstalling the launcher/runtime for every merge. The updater performs an import preflight before switching the pointer.

A change that genuinely requires a new Python/native runtime must use a separately reviewed FULL update/installer path. The APP_ONLY channel must not silently download or replace the Core runtime.

## One-time bootstrap

An installation created before this updater existed cannot update itself into the updater. After the updater PR is merged and main CI creates the first artifact, the existing machine needs **one final manual stable-payload synchronization** (for example through Codex/repair tooling) to install an updater-capable payload. After that bootstrap, future successful main merges are discoverable from Dashboard without rebuilding the stable launcher.
