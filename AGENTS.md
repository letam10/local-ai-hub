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

The local installation and its ignored configuration are not repository source.
Use the tracked `Config/*.example.json` files when documenting machine setup.
