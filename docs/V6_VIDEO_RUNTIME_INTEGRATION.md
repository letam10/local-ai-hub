# V6 Video Runtime Integration

This contract connects the existing AnimeSR, Practical-RIFE, and Real-ESRGAN
installations to Local AI Hub jobs.  It does not mark any of them operational:
that requires a separately recorded bounded Hub job that produces an artifact.

## Boundaries

- AnimeSR uses its configured Python environment and the checked runtime CLI.
  The worker passes `--netscale 4`, never sends the obsolete
  `--low-memory-frame-write` flag, and supplies the exact canonical FFmpeg
  executable through AnimeSR's `ffmpeg_exe_path` setting.
- Practical-RIFE needs its configured Python environment, the runtime-relative
  `train_log/RIFEv4.26_0921/flownet.pkl` leaf, and both configured FFmpeg and
  FFprobe executables.  The upstream script's basename FFmpeg lookup is
  constrained to a worker environment containing only the canonical tool
  directory and the Windows command host; it never inherits the interactive
  process PATH.  Hub remuxes and validates the resulting video with that same
  configured pair.
- Real-ESRGAN accepts one server-owned `realesr-animevideov3` model record
  below `Models`.  The worker always passes that existing local file by
  `--model_path`, so upstream download behavior is not reachable.

All source inputs are resolved internally after the public API's opaque
artifact boundary.  Each worker writes a new timestamped result in its
Hub-owned Output subtree and returns it to the normal Job Manager/artifact
publication flow.  Before a worker reads a runtime/tool/model leaf or creates
an Output/Temp path, it verifies the whole canonical-root chain has no
symlink/junction/reparse escape.  The workers do not delete media, model
weights, runtimes, or environments.  They may remove only their own
successful `Temp/jobs` directory with a matching task prefix.

## Status and smoke evidence

These adapters remain `partial` until a bounded job proves the complete
pipeline: runtime, selected model, adapter, job, artifact, and preview/output.
An installed standalone runtime or model file is not evidence of an
operational Hub capability.  Missing environments, models, FFmpeg/FFprobe, or
an unsuccessful smoke must remain unavailable or partial with a next action.

The source package performs no environment rebuild, model download, GPU work,
or functional smoke.  Those machine-local actions require their own preflight
for active jobs and RTX 4060 availability.
