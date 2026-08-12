"""Task-owned loopback fixture for snapshot continuity and accessibility checks.

The fixture reuses the existing synthetic V6 transport payload without starting
the Hub or any runtime worker. Its only process is a loopback HTTP server with
the inherited graceful ``/__shutdown`` endpoint.
"""

from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests import v6_capability_evidence_ui_fixture as capability_fixture


class SnapshotContinuityHandler(capability_fixture.CapabilityEvidenceHandler):
    """Serve the deterministic UI/bootstrap snapshot over loopback only."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), SnapshotContinuityHandler)
    args.port_file.parent.mkdir(parents=True, exist_ok=True)
    args.port_file.write_text(str(server.server_address[1]), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
        args.port_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
