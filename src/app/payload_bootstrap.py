"""Fixed child-process bootstrap for an exact LocalAIHub app payload.

Embedded Python can retain the runtime's previous application directory ahead
of ``PYTHONPATH``.  The updater and native desktop must therefore bootstrap a
new interpreter with the reviewed payload root explicitly inserted before
resolving the server module.  This helper only constructs a fixed Python
argument vector; it accepts no browser, provider, filesystem, or shell input.
"""

from __future__ import annotations

import json
from pathlib import Path


def api_server_command(app_root: Path) -> list[str]:
    """Return fixed interpreter arguments that bind ``src`` to *app_root*."""

    root = Path(app_root).absolute()
    # JSON is an unambiguous quoted Python literal, including on Windows where
    # paths contain backslashes. No shell interprets this value.
    quoted_root = json.dumps(str(root))
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{quoted_root});"
        "runpy.run_module('src.services.api.api_server',run_name='__main__')"
    )
    return ["-c", bootstrap]


__all__ = ["api_server_command"]
