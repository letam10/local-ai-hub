"""
  FILE NOTE
  - Má»¥c Ä‘Ã­ch: Safe defaults cÃ³ phiÃªn báº£n cho V6 shell vÃ  local services, chia theo section
  - LiÃªn káº¿t trá»±c tiáº¿p: src/app_config/schema.py, src/app_config/settings_service.py
  - VÃ¹ng áº£nh hÆ°á»Ÿng khi sá»­a: GiÃ¡ trá»‹ máº·c Ä‘á»‹nh khi chÆ°a cÃ³ file settings hoáº·c sau per-section reset
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
