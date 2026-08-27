"""Compare the bounded full-repository test result with the known legacy baseline.

The V8 branch intentionally carries a small set of historical V6/V7 tests
whose release identity or fixture assumptions predate the V8 product contract.
This helper keeps those IDs explicit: a new failure/error cannot be silently
described as "historical" merely because the full suite was already red.
It stores test IDs only; no paths, environment values or test output are
persisted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "docs" / "operations" / "V8_LEGACY_TEST_BASELINE.json"
BASELINE_SCHEMA = "v8-known-legacy-test-baseline.v1"


class LegacyBaselineError(ValueError):
    pass


def _ids(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item or "\\" in item or "/" in item for item in value):
        raise LegacyBaselineError(f"{field}_invalid")
    normalized = sorted(set(value))
    if len(normalized) != len(value):
        raise LegacyBaselineError(f"{field}_duplicate")
    return normalized


def baseline_fingerprint(*, failures: Sequence[str], errors: Sequence[str]) -> str:
    """Return the stable digest for historical failure/error IDs only."""

    lines = [*(f"failure:{item}" for item in sorted(set(failures))), *(f"error:{item}" for item in sorted(set(errors)))]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyBaselineError("baseline_unreadable") from exc
    if not isinstance(value, dict):
        raise LegacyBaselineError("baseline_root_invalid")
    expected = {
        "schema_version", "classification", "baseline_head", "suite_command",
        "tests_run", "failures", "errors", "skipped", "fingerprint",
        "max_historical_failures", "max_historical_errors",
    }
    if set(value) != expected or value.get("schema_version") != BASELINE_SCHEMA or value.get("classification") != "historical":
        raise LegacyBaselineError("baseline_schema_invalid")
    head = value.get("baseline_head")
    if not isinstance(head, str) or len(head) not in {40, 64} or any(char not in "0123456789abcdef" for char in head):
        raise LegacyBaselineError("baseline_head_invalid")
    if not isinstance(value.get("suite_command"), str) or value["suite_command"] != "python -m unittest discover -s tests -p \"test*.py\"":
        raise LegacyBaselineError("baseline_command_invalid")
    if not isinstance(value.get("tests_run"), int) or value["tests_run"] <= 0:
        raise LegacyBaselineError("baseline_count_invalid")
    failures = _ids(value.get("failures"), "failures")
    errors = _ids(value.get("errors"), "errors")
    skipped = _ids(value.get("skipped"), "skipped")
    if not isinstance(value.get("fingerprint"), str) or len(value["fingerprint"]) != 64 or any(char not in "0123456789abcdef" for char in value["fingerprint"]):
        raise LegacyBaselineError("baseline_fingerprint_invalid")
    if value["fingerprint"] != baseline_fingerprint(failures=failures, errors=errors):
        raise LegacyBaselineError("baseline_fingerprint_mismatch")
    if value.get("max_historical_failures") != len(failures) or value.get("max_historical_errors") != len(errors):
        raise LegacyBaselineError("baseline_limits_invalid")
    return {**value, "failures": failures, "errors": errors, "skipped": skipped}


def compare_observed(baseline: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
    """Compare test IDs from an observed result without trusting its counts."""

    expected_failures = set(_ids(baseline.get("failures"), "failures"))
    expected_errors = set(_ids(baseline.get("errors"), "errors"))
    actual_failures = set(_ids(observed.get("failures"), "observed_failures"))
    actual_errors = set(_ids(observed.get("errors"), "observed_errors"))
    expected_skips = set(_ids(baseline.get("skipped"), "skipped"))
    actual_skips = set(_ids(observed.get("skipped", []), "observed_skipped"))
    new_failures = sorted(actual_failures - expected_failures)
    new_errors = sorted(actual_errors - expected_errors)
    historical_failures = sorted(actual_failures & expected_failures)
    historical_errors = sorted(actual_errors & expected_errors)
    unexpected_skips = sorted(actual_skips - expected_skips)
    observed_tests_run = observed.get("testsRun") if isinstance(observed.get("testsRun"), int) and not isinstance(observed.get("testsRun"), bool) else None
    discovery_regression = observed_tests_run is None or observed_tests_run < int(baseline.get("tests_run", 0) or 0)
    observed_fingerprint = baseline_fingerprint(failures=actual_failures, errors=actual_errors)
    return {
        "status": "PASS" if not new_failures and not new_errors and not unexpected_skips and not discovery_regression else "FAIL",
        "tests_run": observed_tests_run,
        "baseline_tests_run": baseline.get("tests_run"),
        "test_discovery_regression": discovery_regression,
        "historical_failures": historical_failures,
        "historical_errors": historical_errors,
        "new_failures": new_failures,
        "new_errors": new_errors,
        "unexpected_skips": unexpected_skips,
        "observed_fingerprint": observed_fingerprint,
        "baseline_fingerprint": baseline.get("fingerprint"),
        "historical_failure_count": len(historical_failures),
        "historical_error_count": len(historical_errors),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare a full unittest result with the known V8 legacy baseline.")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--observed", type=Path, required=True, help="JSON result containing testsRun, failures and errors test IDs.")
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
        observed = json.loads(args.observed.read_text(encoding="utf-8"))
        if not isinstance(observed, dict):
            raise LegacyBaselineError("observed_root_invalid")
        result = compare_observed(baseline, observed)
    except (LegacyBaselineError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID", "code": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BASELINE_PATH", "BASELINE_SCHEMA", "LegacyBaselineError", "baseline_fingerprint", "compare_observed", "load_baseline"]
