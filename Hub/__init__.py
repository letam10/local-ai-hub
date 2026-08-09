"""Deprecated compatibility package; use :mod:`src.services.api`.

The aliases keep legacy imports working without duplicating the implementation.
"""

from __future__ import annotations

import importlib
import sys


for _legacy, _canonical in {
    "api_server": "src.services.api.api_server",
    "config": "src.services.api.config",
    "core": "src.services.api.core",
    "gpu": "src.services.api.gpu",
    "jobs": "src.services.api.jobs",
}.items():
    sys.modules[f"{__name__}.{_legacy}"] = importlib.import_module(_canonical)
