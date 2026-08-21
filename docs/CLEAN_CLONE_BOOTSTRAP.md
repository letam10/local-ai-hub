# Clean clone bootstrap

A clean Windows clone starts with Core only. Run `python -B scripts/setup_local_ai_hub.py`
to inspect/plan; the default is read-only. Applying Core setup creates only
absent Core folders and configuration. It never installs models, moves user
data, changes Python/CUDA/NVIDIA drivers, or downloads optional AI.

Use the desktop shortcut after a Hub-capable `pythonw.exe` is verified. The
native shell starts one loopback API and one WebView window; repeated launches
return without opening a second window.
