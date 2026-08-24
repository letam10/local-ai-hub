# Local-only state index

**Discovery date:** 2026-08-24
**Classification:** local inventory and sanitized metadata only
**Repository destination:** `docs/operations/LOCAL_STATE_INDEX.md` and `docs/inventory/*.json`

## Scope and policy source

This index records a bounded, read-only discovery of the Local AI Hub machine state. Large trees were enumerated from filesystem metadata (file count and byte totals); their contents were not hashed or uploaded. The archive contains no model weights, environments, runtime binaries, generated output, personal media, databases, caches, temporary evidence, credentials, or raw machine configuration.

The requested `docs/operations/LOCAL_STATE_ARCHIVE_POLICY.md` was not present in the local checkout and was not present in `origin/main` at discovery time. That absence is recorded deliberately; the applicable controls for this batch are the repository `AGENTS.md` rules and the explicit local-only archive request. A future policy file should supersede this note without treating this index as an authority for destructive cleanup.

The initial archive was created before policy PR #106 reached `main`. After PR #106 merged, this branch was synchronized with `origin/main` at `1c03bc2354d3d690b5c57629669c0dbcf992b4b0` and all five archive files were re-reviewed against the policy now tracked there. The existing metadata remains sanitized and inventory-only; no large-tree rescan or content hash was needed for this re-review.

## Classification summary

| Classification | Discovery result | Repository action | Purpose |
|---|---:|---|---|
| `TRACKABLE_SOURCE` | 0 additional safe files | No new source was needed; canonical source, scripts, workflows, docs, tests, and first-party wrappers are already tracked | Avoid duplicating or moving existing source |
| `SANITIZED_METADATA` | 4 JSON snapshots/inventories | Added the three Config summaries and the large-runtime inventory under `docs/inventory/` | Let reviewers understand local truth without paths, secrets, or bytes |
| `LARGE_RUNTIME_DATA` | 9 inventory groups | Inventory only; never commit bytes | Preserve models, environments, runtime, external applications, output, temp, and caches |
| `USER_DATA` | Local Config state, jobs, artifacts, drafts, output, media, evidence, and recovery data | Excluded from Git | Protect user work and forensic/recovery material |
| `SECRET` | No secret content added | Excluded; raw Config was not copied | Prevent credentials, tokens, cookies, and private environment data from entering Git |

The local enumeration covered the selected repository and state roots below. Counts are filesystem metadata observations; nested groups such as external service clones are called out separately and must not be added together as a second copy of `Services`.

## Local source and repository inventory

| Local area | Classification | File count | Approx. bytes | Repository destination / disposition |
|---|---|---:|---:|---|
| `.github`, `architecture`, `Adapters`, `asset_catalog`, `creative_recipes`, `distribution`, `docs`, `extensions`, `Hub`, `MCP`, `patches`, `privacy_diagnostics`, `scripts`, `src`, `tests`, `workflow_packages`, `workflows` | `TRACKABLE_SOURCE` | 1,056 | 10,857,486 | Existing tracked source/docs/scripts/tests/workflows; no new copy required |
| `Reports` | `SANITIZED_METADATA` / `USER_DATA` | 120 | 1,441,195 | Only the existing tracked migration report is in Git; local reports and evidence stay local |
| `Services` | mixed source and external clone data | 3,727 | 553,985,884 | RF-DETR and Whisper wrappers are tracked; upstream clones remain local and are not vendored |
| `workflows/` and `workflows/comfyui/` | `TRACKABLE_SOURCE` | 13 | 20,923 | All canonical first-party workflow/template definitions are already tracked |
| `tests/fixtures`, `src/ui/assets` | `TRACKABLE_SOURCE` | 0 new | 0 new | No untracked fixture or first-party asset was found |

The source-area byte subtotal is intentionally not used as a release size: local counts include ignored interpreter caches where present, while tracked content is the reviewable source of truth. After the policy sync, `origin/main` contains 764 tracked files and this archive branch contains exactly 769 tracked files (main plus the five archive files).

## Sanitized Config snapshots

Raw local Config files are machine state and are not committed. The following bounded snapshots are the only representations added:

| Snapshot | Raw local input | Safe fields retained | Explicitly removed |
|---|---|---|---|
| `docs/inventory/local_components.snapshot.json` | `Config/components.json` | 18 component IDs, display names, coarse status, runtime/execution state, recovery state, source label, observed version | executable/model/environment/location fields and all paths |
| `docs/inventory/local_model_registry.snapshot.json` | `Config/model_registry.json` | 17 model IDs, names, engine/category, availability, recovery state, registry-reported size, source label, observed version, license-review state | model paths, provider endpoints, weights, credentials, and content hashes not already known |
| `docs/inventory/local_hub_config.snapshot.json` | `Config/hub_config.json` | safe policy and UI/upload limits | hosts, ports, FFmpeg paths, log/output/temp/cache roots, and private process details |

The following local state was inspected only for classification and remains excluded: `creative_workspace.json`, `artifacts.json`, `jobs.json`, Node Studio drafts, source-availability cache, local smoke configuration, and `v8_control.sqlite3`. They contain user history, recovery state, paths, or database bytes and are not suitable for a public archive.

## Large and external runtime inventory

The machine-only inventory is tracked in `docs/inventory/local_runtime_inventory.json`. It contains IDs, categories, providers, local state, approximate file counts/bytes, one already-observed managed-executable SHA-256, and the expected canonical role. It deliberately contains no absolute local paths and no runtime bytes.

| Inventory group | File count | Approx. bytes | Classification |
|---|---:|---:|---|
| Models | 47,381 | 69,073,445,980 | `LARGE_RUNTIME_DATA` |
| Environments | 288,867 | 49,648,134,948 | `LARGE_RUNTIME_DATA` |
| runtime | 104,278 | 17,687,597,855 | `LARGE_RUNTIME_DATA` |
| Output | 583 | 9,937,185,193 | `USER_DATA` / large output |
| Temp | 384,598 | 22,584,996,374 | `USER_DATA` / evidence and task state |
| Cache | 1 | 1,720,320 | `LARGE_RUNTIME_DATA` |
| Logs | 25 | 3,129,944 | `USER_DATA` / local diagnostics |
| External service clones (five upstream trees) | 3,720 | 553,948,534 | `LARGE_RUNTIME_DATA` / external applications |
| Stable installed application | observed outside repository | 2,082,225,015 | `LARGE_RUNTIME_DATA` / external application |

No content hash was computed for the large trees. The inventory's SHA-256 is `null` except for the previously observed stable executable digest, which is included only as an identity hint and not as permission to replace or modify the installed application. Models, Environments, runtime, Output, Reports, Logs, Temp, external applications, and the installed app remain untouched.

## Workflow, registry, installer, and report review

- All 13 first-party workflow/template definitions under `workflows/` (including the ComfyUI bridge schemas and examples) are already tracked. No new machine-independent workflow was found behind the ignore rules. User-created/private workflow data remains local.
- The tracked architecture, extension, catalog, registry, launcher/update, installer, and manifest definitions remain the repository source of truth. No ignored copy was promoted without a safe, source-level distinction.
- `Reports/GITHUB_MIGRATION_REPORT.md` is the existing tracked report. Other local Reports and Temp evidence are preserved locally; none was bulk-uploaded. A report is only suitable for a future sanitized addition when its provenance and redaction are independently reviewed.
- First-party RF-DETR and Whisper wrappers are tracked under `Services`; the five upstream application trees are inventory-only and were not vendored.

## Safety and audit record

- Discovery was local-only and read-only. No model/provider/GPU/server workload was run.
- No file outside the new documentation/inventory batch was edited. No installed executable, shortcut, `DATA_ROOT`, Config state, Output, Reports, Models, Environments, runtime, or Temp data was changed.
- Raw Config and local database files were not copied. No secrets, tokens, cookies, API keys, credentials, or private environment dumps were added.
- Secret-pattern and tracked-large-file scans were run on the candidate files before staging and again on the staged batch. The final command transcript and exact commit are recorded in the handoff.
- This archive is descriptive metadata, not an operational readiness claim and not authorization to clean or relocate machine state.
