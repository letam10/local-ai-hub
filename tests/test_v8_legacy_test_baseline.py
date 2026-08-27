"""Contract tests for the explicit historical full-suite baseline."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.v8_legacy_test_baseline import BASELINE_PATH, baseline_fingerprint, compare_observed, load_baseline


class V8LegacyTestBaselineTests(unittest.TestCase):
    def test_baseline_is_path_free_and_fingerprint_bound(self) -> None:
        value = load_baseline(BASELINE_PATH)
        self.assertEqual(value["classification"], "historical")
        self.assertEqual(value["max_historical_failures"], len(value["failures"]))
        self.assertEqual(value["max_historical_errors"], len(value["errors"]))
        self.assertEqual(value["fingerprint"], baseline_fingerprint(failures=value["failures"], errors=value["errors"]))
        serialized = json.dumps(value, ensure_ascii=True)
        self.assertNotIn("D:\\", serialized)
        self.assertNotIn("C:\\", serialized)

    def test_new_failure_or_error_is_not_historical(self) -> None:
        baseline = load_baseline(BASELINE_PATH)
        result = compare_observed(baseline, {"testsRun": 1, "failures": ["new.test"], "errors": []})
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["new_failures"], ["new.test"])
        self.assertTrue(result["test_discovery_regression"])

    def test_discovery_regression_and_unexpected_skip_are_explicit_failures(self) -> None:
        baseline = load_baseline(BASELINE_PATH)
        result = compare_observed(baseline, {"testsRun": baseline["tests_run"], "failures": [], "errors": [], "skipped": ["new.skip"]})
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["test_discovery_regression"])
        self.assertEqual(result["unexpected_skips"], ["new.skip"])


if __name__ == "__main__":
    unittest.main()
