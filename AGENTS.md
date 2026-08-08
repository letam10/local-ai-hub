# Local AI Hub repository rules

1. Never push directly to `main` after bootstrap.
2. Never merge pull requests.
3. Every task uses a dedicated branch (`feature/*`, `fix/*`, `test/*`, or `docs/*`).
4. Run bounded functional or smoke tests only.
5. Do not benchmark unless explicitly requested.
6. Never commit model weights, environments, caches, outputs, media, personal voice references, or secrets.
7. Preserve existing SAM 2, AnimeSR, Whisper, FFmpeg and AIRI installations.
8. Do not modify NVIDIA drivers, CUDA, or system software without explicit approval.
9. Keep commits small and scoped.
10. Use Conventional Commits.
11. Push completed work to its dedicated branch.
12. Open or update a Pull Request for review.
13. Stop after the PR is ready for human review.
14. Never force-push `main`.
15. Never merge `main` automatically.

The local installation and its ignored configuration are not repository source.
Use the tracked `Config/*.example.json` files when documenting machine setup.
