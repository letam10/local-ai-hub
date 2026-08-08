# GitHub Migration Report — Local AI Hub

Date: 2026-08-08
Repository: https://github.com/letam10/local-ai-hub
Visibility: private
Default branch: `main`
Import branch: `bootstrap/import-local-ai-hub`
Pull request: https://github.com/letam10/local-ai-hub/pull/1

## Commits

- `b9adffc` — `chore(repo): bootstrap GitHub repository`
- `114daa2` — `chore(repo): import sanitized Local AI Hub source`

## Imported scope

- First-party hub, adapter, application, MCP, launcher, and helper source.
- Portable configuration examples, documentation, dependency lock data, and local upstream patches/wrappers.
- 63 source and governance files in the initial source import (about 115 KiB staged source content before commit).

Third-party upstream repositories are documented in `dependencies.lock.json`; their working trees are not vendored into this repository.

## Explicit exclusions

The repository excludes model weights, Python environments, caches, temporary files, outputs, backups, logs, runtimes, media, personal voice references, credentials, and machine-local configuration. In particular, `Models/`, `Environments/`, `Cache/`, `Output/`, `Temp/`, and local `Config/*.json` files are ignored.

## Validation performed

- Staged-file audit found no files above the 50 MiB review threshold and no binary model/media artifacts.
- `.gitignore` was checked against representative model, environment, cache, output, local-config, and upstream-service paths.
- High-confidence secret-pattern scan found no credential files or values in the staged import.
- `git diff --cached --check` passed before the source-import commit.
- Python syntax checks passed for 38 tracked Python files.
- Component-registry load and adapter import smoke checks passed; no GPU inference, benchmark, or model download was run.

## Known functional limitations

- Faster-Whisper may fall back to CPU when `cublas64_12.dll` is unavailable.
- The direct SAM 2 API integration remains unfinished.
- AIRI-to-Hub end-to-end integration remains incomplete.

## Review state

Pull request #1 is open for human review. It must not be auto-merged. No direct push to `main` is permitted after the bootstrap commit.
