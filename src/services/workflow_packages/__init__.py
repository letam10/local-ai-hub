"""Static contracts for portable workflow packages.

The package layer is intentionally side-effect free: it validates, compares,
and projects descriptors but never executes a graph or writes a user workflow.
"""

from .catalog import discover_managed_packages, load_managed_package, preflight_workflow_package
from .diffing import diff_workflow_packages, plan_workflow_migration
from .io import export_workflow_package, safe_import_workflow_package
from .linting import lint_workflow_package

__all__ = [
    "diff_workflow_packages",
    "discover_managed_packages",
    "export_workflow_package",
    "lint_workflow_package",
    "load_managed_package",
    "plan_workflow_migration",
    "preflight_workflow_package",
    "safe_import_workflow_package",
]
