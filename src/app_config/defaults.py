"""Safe defaults for the V2 shell and local services."""

DEFAULTS = {
    "start_maximized": True,
    "minimum_width": 1280,
    "minimum_height": 720,
    "max_heavy_gpu_jobs": 1,
    "model_load_policy": "on_demand",
    "bind_host": "127.0.0.1",
}
