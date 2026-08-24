# Local AI Hub local-state archive policy

## Goal

Keep enough important, machine-independent project state in GitHub that a reviewer can understand, audit and reproduce Local AI Hub without depending on one Windows working copy. GitHub remains a source/configuration/provenance repository, not a bulk backup for models, environments, generated media or private machine data.

## What should be preserved in Git when it exists only locally

Prefer to track these items after sanitization and review:

- first-party source code and wrappers;
- build, installer, launcher, repair and migration scripts;
- schemas and migrations;
- workflow/template definitions and node metadata;
- small deterministic UI/branding assets;
- machine-independent example configuration;
- dependency/version lock metadata;
- sanitized component/model/runtime inventories containing identifiers, versions, relative classifications, sizes and hashes;
- sanitized acceptance/diagnostic summaries needed to understand product behavior;
- recovery/provenance metadata that does not contain secrets or user files;
- documentation describing important local-only directories and why they exist.

## What must not be committed as ordinary Git content

Do not commit the actual bytes of:

- `Models/` model weights;
- `Environments/` virtual environments;
- `runtime/` or other large runtime trees;
- `Cache/`, `Temp/`, `Output/`, `Backups/`, generated logs;
- SQLite/job/artifact databases;
- user media, personal voice references or generated outputs;
- API keys, credentials, tokens, cookies or authentication state;
- system-managed application installations;
- arbitrary upstream repository clones.

For these categories, preserve a sanitized inventory/checksum/manifest instead when that improves reproducibility. An actual binary archive or Git LFS plan requires separate user approval because of size, privacy and GitHub quota implications.

## Local archive procedure for Codex

When the user asks to archive important local-only project state:

1. Inventory candidate files read-only first. Do not rely only on `.gitignore`.
2. Classify every candidate as `TRACKABLE_SOURCE`, `SANITIZED_METADATA`, `LARGE_RUNTIME_DATA`, `USER_DATA`, or `SECRET`.
3. Exclude `SECRET` and `USER_DATA` automatically. Do not print secret values while classifying them.
4. For `LARGE_RUNTIME_DATA`, create only a sanitized manifest unless the user separately approves another storage mechanism.
5. Normalize tracked material so it does not depend on workstation-specific absolute paths when a relative/project identity is sufficient.
6. Run secret scan and tracked-large-file checks before staging.
7. Add the safe files in one logical archive batch rather than one push per file.
8. Update a tracked local-state index in `docs/operations/` with the archive date, category, repository destination, purpose, approximate file count/size and integrity hash where practical.
9. Run local validation before the single batch push. Do not trigger repeated GitHub Actions during discovery.
10. Open/update a PR and stop for user review. Codex never merges `main`.

## Recommended inventory representation

For data that remains local, prefer a compact sanitized record such as:

```json
{
  "schema_version": "local-ai-hub-local-state-index.v1",
  "category": "model_inventory",
  "items": [
    {
      "id": "example-model-id",
      "version": "known-version-or-null",
      "size_bytes": 0,
      "sha256": "known-hash-or-null",
      "state": "present-local-only"
    }
  ]
}
```

Do not put raw credentials, arbitrary environment dumps, full process environments or unnecessary absolute user-profile paths into tracked inventory files.

## CI economy

Archive discovery is local work. A branch with an open PR should normally receive one remote CI cycle after a locally-green archive batch. Do not push each discovered file separately and do not use no-op commits to trigger GitHub Actions.
