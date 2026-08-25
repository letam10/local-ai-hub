# First watchdog-capable updater bootstrap

The installed `LocalAIHub.exe` is a stable shell and older installed payloads
may not contain the updater-owned watchdog.  Do not use the old Dashboard
updater to install the first watchdog-capable payload: the old payload cannot
prove the new runtime's restart boundary.

After the updater fix has been reviewed and merged, use a reviewed source
checkout and the exact successful `main` artifact commit:

```powershell
python -B scripts/bootstrap_first_watchdog_payload.py `
  --source-root <reviewed-source-checkout> `
  --install-root <installed-LocalAIHub-root> `
  --expected-commit <exact-successful-main-commit>
```

The command without `--activate` performs only source/identity readiness and
does not change the installed pointer.  Add `--activate` only after confirming
the exact successful main run and the intended installed root:

```powershell
python -B scripts/bootstrap_first_watchdog_payload.py `
  --source-root <reviewed-source-checkout> `
  --install-root <installed-LocalAIHub-root> `
  --expected-commit <exact-successful-main-commit> `
  --activate
```

The wrapper freezes the exact successful main candidate, verifies the artifact
manifest/contract/hash, stages side-by-side, starts the candidate API on an
owned ephemeral port, checks `/health`, `/ui/`, and `/api/bootstrap`, and only
then records the previous pointer/pending-health marker and atomically switches
`current.json`.  A failed preflight preserves bounded staging evidence and
requires the old pointer to remain unchanged; no model, environment, runtime
or `DATA_ROOT` bytes are moved.  The pending marker remains until the new
frontend calls the explicit ready handshake.  The watchdog then runs from the
old verified payload/runtime for the first restart and can restore the previous
pointer if the new payload exits or fails identity/health checks.

This procedure is a one-time bootstrap aid, not a release activation command.
It does not create tags, merge `main`, rebuild `LocalAIHub.exe`, publish a
release, or upload local evidence.
