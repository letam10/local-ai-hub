# Local configuration

`Config/components.json`, `Config/model_registry.json`, `Config/hub_config.json`
and `Config/local.json` are machine-local and ignored. Commit only the matching
`*.example.json` templates. The V2 examples also include
`Config/app.example.json` and `Config/models.example.json`.

For Windows launchers, copy `Config/local.env.example.cmd` to the ignored
`Config/local.env.cmd` and fill in only local filesystem paths. Do not put
tokens, passwords or API keys in either file. `Config/` is the canonical case
on Windows; do not create a parallel lowercase config directory.

Use environment variables or the local configuration to provide paths for
external installations, for example `SAM2_HOME`, `ANIMESR_HOME`,
`WHISPER_HOME`, `FFMPEG_PATH`, `FFPROBE_PATH`, `AIRI_EXECUTABLE` and
`LOCAL_AI_HOME`. Never put secrets or absolute personal paths in tracked files.
