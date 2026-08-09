# GitHub Migration Report - Local AI Hub

Date: 2026-08-09

## Repository and pull request

- Repository: https://github.com/letam10/local-ai-hub
- PR URL: https://github.com/letam10/local-ai-hub/pull/1
- Base branch: `main`
- Head branch: `bootstrap/import-local-ai-hub`
- PR current state: open for human review; auto-merge is disabled.
- Current PR head: See GitHub PR metadata.

## Known migration commits

- `b9adffc` - `chore(repo): bootstrap GitHub repository`
- `114daa2` - `chore(repo): import sanitized Local AI Hub source`

Two commits precede this migration report. The report deliberately does not
record the SHA of its own commit or a mutable PR head SHA.

## Imported scope

- 63 first-party source and governance files were included in the initial
  source-import commit (about 115 KiB staged source content before commit).
- Scope includes the Hub API, MCP bridge, adapters, application launchers,
  helper scripts, portable configuration examples, documentation, dependency
  lock data, and local upstream wrappers/patches.
- Third-party upstream working trees are documented in
  `dependencies.lock.json` and are not vendored into this repository.

## Explicit exclusions

The repository excludes model weights, Python environments, caches, temporary
files, outputs, backups, logs, runtimes, media, personal voice references,
credentials, and machine-local configuration. In particular, `Models/`,
`Environments/`, `Cache/`, `Output/`, `Temp/`, and local `Config/*.json` files
are ignored.

Narrow source-asset locations are exempted so that required documentation, UI,
application, and test-fixture assets can be tracked without admitting personal
or generated media.

## Validation performed for the source import

- Staged-file audit found no files above the 50 MiB review threshold and no
  binary model/media artifacts.
- `.gitignore` was checked against representative model, environment, cache,
  output, local-config, and upstream-service paths.
- High-confidence secret-pattern scan found no credential files or values in
  the staged import.
- `git diff --cached --check` passed before the source-import commit.
- Python syntax checks passed for 38 tracked Python files.
- Component-registry loading and adapter imports were smoke-tested. No GPU
  inference, benchmark, model download, or stress test was run.

## Current readiness limitations

- Component installation state and tool capability are reported separately;
  an installed component does not imply an operational tool.
- Direct SAM 2 segmentation and tracking remain unavailable because the direct
  backend adapter has not been verified.
- AnimeSR upscale is queue-only until a job executor is verified.
- Subtitle-video muxing remains unavailable until its output contract is
  verified.
- The remaining model-backed adapters are partial until each has a bounded
  functional backend smoke result.
- Faster-Whisper may fall back to CPU when `cublas64_12.dll` is unavailable.
- RF-DETR and Faster-Whisper upstream commit SHAs remain unresolved rather
  than guessed; the dependency lock records the reason for each.

## Review requirement

PR #1 must be reviewed by a human. Do not auto-merge it, do not push directly
to `main`, and do not begin filesystem migration or UI V2 work before this PR
is reviewed and merged.
