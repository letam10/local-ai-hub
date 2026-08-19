"""
  FILE NOTE
  - Mục đích: Safe defaults có phiên bản cho V6 shell và local services, chia theo section
  - Liên kết trực tiếp: src/app_config/schema.py, src/app_config/settings_service.py
  - Vùng ảnh hưởng khi sửa: Giá trị mặc định khi chưa có file settings hoặc sau per-section reset
"""

from __future__ import annotations

from src.app_config.schema import SETTINGS_SCHEMA_VERSION, SETTINGS_SECTION_DEFAULTS

# Backwards-compatible flat view for legacy callers inside main.py / tray.py.
DEFAULTS = {
    "schema_version": SETTINGS_SCHEMA_VERSION,
    "settings_revision": 0,
    "start_maximized": SETTINGS_SECTION_DEFAULTS["window"]["start_maximized"],
    "minimum_width": SETTINGS_SECTION_DEFAULTS["window"]["minimum_width"],
    "minimum_height": SETTINGS_SECTION_DEFAULTS["window"]["minimum_height"],
    "max_heavy_gpu_jobs": SETTINGS_SECTION_DEFAULTS["jobs"]["max_heavy_gpu_jobs"],
    "model_load_policy": SETTINGS_SECTION_DEFAULTS["jobs"]["model_load_policy"],
    "bind_host": SETTINGS_SECTION_DEFAULTS["network"]["bind_host"],
}
