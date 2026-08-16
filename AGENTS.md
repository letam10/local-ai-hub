# Local AI Hub repository rules

1. Never push directly to `main` after bootstrap.
2. Never merge pull requests.
3. Every task uses a dedicated branch (`feature/*`, `fix/*`, `test/*`, or `docs/*`).
4. Run bounded functional or smoke tests only.
5. Do not benchmark unless explicitly requested.
6. Never commit model weights, environments, caches, outputs, media, personal voice references, or secrets.
7. Preserve existing SAM 2, AnimeSR, Whisper, FFmpeg and AIRI installations by default. A specifically approved migration plan may relocate managed portable components, but only under rules 24-26.
8. Do not modify NVIDIA drivers, CUDA, or system software without explicit approval.
9. Keep commits small and scoped.
10. Use Conventional Commits.
11. Push completed work to its dedicated branch.
12. Open or update a Pull Request for review.
13. Stop after the PR is ready for human review.
14. Never force-push `main`.
15. Never merge `main` automatically.
16. Before every push, run secret and tracked-large-file checks.
17. Do not change dependency or model versions inside an unrelated feature/fix PR.
18. Any configuration schema change must update the corresponding tracked example configuration.
19. Never vendor an upstream repository into Local AI Hub unless explicitly approved.
20. Never rewrite shared Git history with reset, rebase, or force-push without explicit approval.
21. If a backend is incomplete, expose its tool status as partial or unavailable instead of operational.
22. Never claim that a route or tool is functional unless one bounded functional smoke test has passed.
23. Never run `git clean` with `-x`, `-X`, or `-fdx` against `D:\LocalAIHub`; ignored folders contain real environments, models, cache and runtime data.
24. Filesystem relocation of existing AI installations is prohibited by default. It is permitted only during an explicitly approved migration task with a path manifest, rollback plan and verification.
25. Never delete a legacy installation path until the replacement path has passed bounded functional validation and all known launchers/adapters have been updated.
26. System-installed applications such as AIRI must not be manually relocated out of their installer-managed location unless an official portable migration path exists.
27. API keys, secrets and authentication material must never be copied into Hub configuration, logs, screenshots or PR descriptions.
28. `D:\LocalAIHub` is the canonical project filesystem boundary. Read-only inspection outside this root is allowed when required for system-managed applications, diagnostics, process inspection, or dependency discovery.
29. NEVER delete, remove, uninstall, prune, recycle, truncate, overwrite-for-removal, or move-away any file, directory, worktree, installation, cache, temporary directory, document, model, application data, or other filesystem object whose resolved target is outside `D:\LocalAIHub` without explicit user approval for that specific destructive action.
30. The external-delete rule applies regardless of tool or mechanism, including PowerShell `Remove-Item`, cmd `del`/`rmdir`, `rm`, Python `os.remove`/`shutil.rmtree`, Git worktree removal/cleanup, package uninstallers, cleanup scripts, application APIs, and deletion from `%TEMP%`, `%LOCALAPPDATA%`, `%APPDATA%`, Documents, user profile directories, or other drives.
31. Before requesting approval for any destructive action outside `D:\LocalAIHub`, report the resolved absolute target, reason, expected file/directory count or size when practical, whether the target is a symlink/junction/reparse point, and whether a non-destructive alternative exists. Do not interpret a general task approval as permission to delete an unspecified external target.
32. A path lexically located inside `D:\LocalAIHub` but resolving through a symlink, junction, mount point, or other reparse point to a target outside `D:\LocalAIHub` is treated as EXTERNAL for destructive operations and requires explicit user approval.
33. Codex-owned scratch data should be created under `D:\LocalAIHub\Temp` when practical. Do not clean Codex/recovery/worktree data created outside `D:\LocalAIHub` without explicit user approval, even if it appears temporary.
34. Inside `D:\LocalAIHub`, deletion is not automatically unrestricted: preserve Models, Environments, runtime, Output, Config local state, user media, recovery bundles, forensic evidence, and other persistent data unless the active task explicitly authorizes the specific cleanup. Automatic cleanup is limited to clearly Hub-owned/task-owned disposable Temp/Cache data whose ownership has been verified.
35. Never bypass these filesystem rules because a process has Administrator/full-access permission. Permission to access a path is not permission to destroy it.

The local installation and its ignored configuration are not repository source.
Use the tracked `Config/*.example.json` files when documenting machine setup.
