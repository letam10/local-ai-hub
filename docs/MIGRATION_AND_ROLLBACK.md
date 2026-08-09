# Migration and Rollback

## Preconditions

Run `scripts/migrate_layout_v2.ps1` only after reviewing the ignored local
manifest. It defaults to `-DryRun`; `-Apply` is explicit and only evaluates
entries marked `planned`. Do not use it to infer source paths, download models,
or delete a legacy directory.

Before a physical move, the manifest must include:

- component, source and destination paths;
- observed size, process references and expected free-space impact;
- a same-volume atomic-move strategy when possible;
- a rollback strategy and whether a legacy junction is required; and
- a bounded launch smoke test to run after the move.

Moves that would copy more than 2 GB, leave less than 4 GB free, target an
existing destination, or list a process using the source are stopped. A Python
virtual environment is `external_managed` unless it has an explicit verified
relocation plan. Installer-managed AIRI is represented by a link/registry entry
and is never moved manually.

## Commands

```powershell
# Default behavior is a non-mutating plan.
pwsh -File scripts/migrate_layout_v2.ps1

# Explicitly apply only reviewed, planned entries.
pwsh -File scripts/migrate_layout_v2.ps1 -Apply

# Reverse manifest entries that were marked verified by a successful apply.
pwsh -File scripts/rollback_layout_v2.ps1
```

## Apply sequence

For each reviewed entry, the script verifies source/destination, records size
and projected free space, moves on the same volume when possible, verifies the
destination, updates the local manifest, and optionally creates a legacy
junction. It never stops arbitrary processes and never deletes a top-level
legacy folder.

If a move fails, record `migration_partial` in the local manifest and do not
continue with related components. Restore with the rollback script only after
checking that the legacy source path is absent and the destination still exists.

## Legacy cleanup

`Reports/LOCAL_LAYOUT_MIGRATION_REPORT.md` is machine-local and ignored. It
classifies every legacy tree as `safe_to_delete`, `still_referenced`,
`junction_only`, `not_migrated`, or `external_system_app`. Cleanup is only a
user-confirmed action after all components pass their bounded smoke tests and
`still_referenced = 0`; the migration scripts never perform it.
