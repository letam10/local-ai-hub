# Contributing

## Workflow

1. Start from an up-to-date `main`.
2. Create one dedicated branch for one task.
3. Make a small, scoped change and run bounded local checks.
4. Use a Conventional Commit such as `fix(whisper): read configured runtime`.
5. Push the branch and open a Pull Request.
6. A human reviews and merges; Codex stops when the PR is ready.

Do not commit model files, environments, cache, output/media, credentials, or
machine-local JSON. Heavy GPU tests run locally and are recorded in the PR;
GitHub CI is limited to lightweight checks.
