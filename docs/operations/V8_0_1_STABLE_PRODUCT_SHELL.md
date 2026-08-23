# V8.0.1 Stable Product Shell

This document defines the installed Windows product boundary for V8.0.1. It
does not activate a release, create `v8.0.1`, modify `main`, or repair a
machine shortcut by itself.

## Stable entrypoint

The only production entrypoint is `{APP_ROOT}\LocalAIHub.exe`. The small
windowed launcher derives `APP_ROOT` from its own executable, sets the stable
AppUserModelID `LocalAIHub.Desktop`, validates `product.json`,
`installation.json`, and `current.json`, then launches the bundled
`pythonw.exe` from the selected `versions/<version>` payload. It never uses
the current working directory, a Git checkout, PATH, system Python, `cmd`,
`wscript`, or a Temp/test root as a fallback.

The active layout is bounded and versioned:

```text
{APP_ROOT}\LocalAIHub.exe
{APP_ROOT}\local-ai-hub.ico
{APP_ROOT}\product.json
{APP_ROOT}\installation.json
{APP_ROOT}\current.json
{APP_ROOT}\versions\8.0.1\manifest.json
{APP_ROOT}\versions\8.0.1\app\...
{APP_ROOT}\versions\8.0.1\runtime\Python312\pythonw.exe
{APP_ROOT}\staging\...
```

`current.json` contains only the validated version, the fixed relative payload
name, and the SHA-256 of that payload manifest. Activation writes it through a
same-directory fsync/replace operation and refuses a missing, mismatched, or
reparse-backed payload. The previous pointer remains untouched when staging
or verification fails.

## App/data separation

`installation.json` is written by the installer/repair boundary and binds the
installed `APP_ROOT` to a separate persistent `DATA_ROOT`. The application
resolves Config, Models, Environments, runtime, Cache, Output, Projects,
Backups, Reports and user media through that data authority. A source checkout
or a historical Temp install is never inferred as production data. Versioned
payload updates do not own, move, or delete persistent data.

Development uses `scripts/dev_launch.ps1`, which requires an explicitly
isolated Hub-owned Temp data root. It cannot rewrite installed manifests or
production shortcuts. `scripts/update_managed_shortcuts.ps1` is a dry-run
repair tool unless `-Apply` is explicit; it refuses Temp, `.git`, test and
reparse roots and writes Desktop/Start Menu links only to the stable EXE with
the canonical ICO.

## Canonical identity and lifecycle truth

The product mark is the existing UI `LA` identity: a rounded square with the
`#80aaff` to `#4d7dff` blue gradient and white bold `LA`. The SVG source is
`assets/branding/local-ai-hub.svg`; packaging uses the deterministic
multi-resolution `distribution/assets/local-ai-hub.ico` (16, 24, 32, 48, 64,
128 and 256 pixels). Tray and WebView icon use this identity when the host
API supports it; no Python/system icon is substituted.

Desktop close prompts carry typed verification. A job count is displayed and
the cancel action is offered only for a verified API process owned by this
desktop. API-down, malformed, or externally owned state is `unknown` with no
fabricated count and no global cancellation.

The release provenance helper has an `integration` mode (the default) plus
explicit `pre_tag` and `post_tag` modes. Integration and ordinary merge CI do
not inspect or require a tag. Pre-tag validation requires an explicitly
requested, unoccupied candidate tag; post-tag validation accepts only an
existing immutable tag peeled to the exact expected commit. Historical V7
manifests and tags remain outside this V8.0.1 preparation.

## Payload repair authority

The stable `LocalAIHub.exe` launcher is the immutable installation entrypoint.
Repairs and updates replace only the validated version payload behind
`current.json`; they never repoint shortcuts to a source branch or rebuild the
launcher solely to update application code. Installed startup uses the active
payload's bundled `pythonw.exe`, while the stable installation root and
persistent `DATA_ROOT` remain the identity and storage authorities.
