# Local configuration

`Config/components.json`, `Config/model_registry.json`, `Config/hub_config.json`
and `Config/local.json` are machine-local and ignored. Commit only the matching
`*.example.json` templates.

Use environment variables or the local configuration to provide paths for
external installations, for example `SAM2_HOME`, `ANIMESR_HOME`,
`WHISPER_HOME`, `FFMPEG_PATH`, `FFPROBE_PATH`, `AIRI_EXECUTABLE` and
`LOCAL_AI_HOME`. Never put secrets or absolute personal paths in tracked files.
