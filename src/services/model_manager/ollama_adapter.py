from __future__ import annotations

import json
import urllib.request


def list_models() -> dict:
    request = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - machine-state dependent
        return {"status": "unavailable", "error": str(exc)}
