# Runtime installation contract

Runtime Manager separates executable/environment state from model state. It
uses `Config/runtime_catalog.example.json` and reports bounded leaf presence,
version metadata and a plan destination class without exposing workstation
paths to the browser.

A future runtime installation is one component at a time, pinned to approved
requirements/wheel hashes, with disk/process/reparse checks and an atomic
activation/receipt. It must not move or overwrite an existing environment and
must not change NVIDIA drivers or system CUDA. Runtime presence alone is
`INSTALLED_UNVERIFIED` or `PARTIAL`; only a matching bounded smoke receipt can
produce `OPERATIONAL`.
# Runtime installation and reference

Runtime Manager resolves and inspects fixed runtime leaves without executing
them at startup. A runtime can be `NOT_INSTALLED`, `PARTIAL` or
`INSTALLED_UNVERIFIED`; it is not operational merely because an executable
exists.

Runtime installation is plan-first and uses a fixed strategy allowlist:
`reference_existing`, `portable_archive`, `python_environment` or
`manual_import`. A future executor must stage, validate expected leaves and
write an atomic receipt. It must not accept commands from catalog JSON, move
an existing environment, alter NVIDIA drivers/system CUDA, or overwrite a
working runtime. The example catalog has no pinned production assets, so its
plans remain review/manual only.

The final V7 catalog is `Config/v7_production_catalog.example.json`. It lists
all supported module environments and portable tools, including FFmpeg,
ComfyUI, SAM2, AnimeSR, Whisper, OCR, vision and voice profiles. The explicit
`src.services.productization.runtime_probe.verify_python_environment` helper
can run a bounded version/import/pip check for a fixed environment after
review; discovery itself never launches Python or installs packages.
