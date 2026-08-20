# Clean-clone core bootstrap

From a fresh checkout:

```powershell
git clone https://github.com/letam10/local-ai-hub.git
Set-Location .\local-ai-hub
.\scripts\bootstrap_core.ps1
```

The bootstrap resolves the checkout dynamically, creates ignored machine roots
and initializes missing local Config JSON from tracked examples without
overwriting existing files. It checks only the core Python/WebView readiness
surface. It does **not** download AI models, install module GPU dependencies,
start a server/worker or change system Python, CUDA or drivers.

The output is a sanitized JSON receipt. AI modules show `NOT_INSTALLED` or
`UNAVAILABLE` until an explicit Module/Model Manager plan is approved.

After Core is available, open `Components / AI Setup` in the same Hub shell.
It reads the tracked model/runtime catalogs through server-owned APIs and
offers inspect, install-plan, import-plan, verify and maintenance-plan
actions. Plan responses expose only safe location classes (`models_root`,
`runtime_root`, `environment_root`) and opaque plan IDs. The current example
catalogs deliberately remain manual-import/review-only until a pinned source,
size and digest are reviewed.
