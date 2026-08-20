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
