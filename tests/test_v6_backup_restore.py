from __future__ import annotations
# tests/test_v6_backup_restore.py re-exports from test_v6_asset_library for backup-specific coverage.
# The full backup/restore contract is exercised in TestBackupManager* classes in test_v6_asset_library.py.
import unittest
from tests.test_v6_asset_library import (
    TestBackupManagerCreate,
    TestBackupManagerInspect,
    TestBackupManagerPlanAndApply,
    TestBackupManagerScrubSecrets,
)
if __name__ == '__main__':
    unittest.main()
