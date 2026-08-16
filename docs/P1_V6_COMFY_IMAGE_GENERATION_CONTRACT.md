# V6 ComfyUI image-generation contract

FLUX Klein and Qwen Image remain direct, local ComfyUI workflows. This source
package does not inspect machine-local Config, start ComfyUI, execute a model,
download a model, or record a smoke result.

## Local-only workflow binding

The selected engine has its own configured studio root; FLUX never falls back
to a Qwen root and Qwen never falls back to a FLUX root. The chosen local API
JSON is a private runtime asset, not tracked Hub source. Before the Hub starts
ComfyUI or uploads an input, it verifies only the exact node/input bindings
that Hub will mutate. Missing, malformed, escaped, or reparse workflow paths
return a finite `unavailable` / `execution: not_run` result. They do not turn
into a Python key error, start a backend, submit a prompt, or publish an
artifact.

Qwen Image currently has a text-to-image contract only. A supplied IMAGE
artifact is refused unless a separately reviewed Qwen image-to-image workflow
contract is added; Hub must not silently discard that input.

## 8 GiB Qwen profile

When Hub itself starts ComfyUI for Qwen Image, it uses the fixed
`qwen_8gb_low_vram` profile with ComfyUI's `--lowvram` flag. The profile has a
single-image, 512 x 512 maximum-pixel envelope and at most 12 sampling steps.
It does not accept command-line flags, a model name, a model path, or a VRAM
claim from a request. An already-running external ComfyUI is observed rather
than reconfigured.

The profile reuses only the models already installed in the local ComfyUI
layout. It never downloads, installs, selects, moves, or duplicates model
assets. It is not proof that any particular Qwen model fits or can run on an
8 GiB GPU; a separate bounded runtime-to-model-to-adapter-to-job-to-artifact
smoke is required before `generate_qwen_image` can be operational.

## Evidence and publication

Completed FLUX/Qwen jobs continue through the shared ComfyUI `outputs` →
Hub-owned artifact publisher. Jobs and browser projections receive opaque,
job-provenanced artifact metadata only. A current, tool-specific smoke receipt
whose path-free runtime fingerprint still matches is required by the existing
capability projection before either image tool is operational. Invalid
workflow/profile outcomes remain `partial` or `unavailable`; this static
contract never promotes a capability.
