# First run and AI setup

## One command

From a clean Windows checkout run:

```powershell
.\scripts\setup_local_ai_hub.ps1
.\scripts\setup_local_ai_hub.ps1 -Apply
```

The first command is inspect/plan-only. `-Apply` creates only missing Core
directories and configuration from tracked examples, then registers a
Hub-capable desktop shortcut. It does not download models, install Python
environments, change CUDA/NVIDIA drivers, or overwrite existing user files.

The desktop shell always starts the same API, Job Manager, adapter and Artifact
Store used by an existing owner installation. Optional models and runtimes are
shown as `Not installed`, `Partial`, `Manual review`, or `Unavailable`; they
are never hidden and never promoted to operational by file presence alone.

## Install a component

Open **Components / AI Setup** or **Models & Storage**, review the server-owned
plan, and choose the action shown by the catalog:

- **Download & Install** only for a pinned, trusted, license-complete source;
- **Authorize & Install** for a gated provider;
- **Review License** when acceptance is required;
- **Import Model** when a user-owned manual import is the only trustworthy path.

The plan includes the model, runtime, Python environment, dependencies and
module binding. Existing compatible installations are reused; conflicting or
unknown files require manual review and are not overwritten.

## Recovery and maintenance

Repair, update and uninstall are separate explicit plans. Uninstall preserves
Models, Environments, runtime, Config, Projects, Output, Backups and Reports.
Only installer-owned staging can be discarded after cancellation.

## Limitations

The tracked production catalog intentionally does not invent download sizes or
checksums. A model without a pinned official file set is `MANUAL_IMPORT_ONLY`,
`AUTH_REQUIRED`, `LICENSE_REQUIRED`, or `UNSUPPORTED_SOURCE`. AIRI remains an
external installer-managed application and no local conversational/chat LLM is
installed by this productization package.
