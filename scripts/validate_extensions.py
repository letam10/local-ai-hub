"""Static validator and report exporter for Local AI Hub extension descriptors.

It only reads repository-managed metadata and optionally creates one safe
descriptor scaffold under ``extensions/``.  It never imports an extension,
starts a workload, probes a GPU, downloads anything, or installs a dependency.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.extension_platform import (  # noqa: E402
    build_compatibility_report,
    compatibility_report_json,
    discover_extensions,
    generate_extension_scaffold,
    load_extension_config,
    preflight_extensions,
    render_compatibility_markdown,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Statically validate repository-managed Local AI Hub extensions.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Report format written to standard output.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero unless every extension is operational.")
    parser.add_argument("--generate", metavar="EXTENSION_ID", help="Create a safe planned scaffold below this repository's extensions directory.")
    parser.add_argument("--display-name", help="Optional display name used only with --generate.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.display_name and not args.generate:
        raise SystemExit("--display-name requires --generate")
    if args.generate:
        scaffold = generate_extension_scaffold(args.generate, root=ROOT, display_name=args.display_name)
        print(f"Created planned scaffold for {scaffold['extension_id']}: {', '.join(scaffold['files'])}", file=sys.stderr)
    discovery = discover_extensions(ROOT)
    configuration = load_extension_config(ROOT)
    preflight = preflight_extensions(discovery, configuration=configuration)
    report = build_compatibility_report(discovery, preflight=preflight)
    output = compatibility_report_json(report) if args.format == "json" else render_compatibility_markdown(report)
    sys.stdout.write(output)
    if report["status"] == "unavailable" or (args.strict and report["status"] != "operational"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

