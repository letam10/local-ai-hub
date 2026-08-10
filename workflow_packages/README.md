# Managed workflow packages

This directory contains repository-managed, static `workflow-package.v1`
descriptors and human-only `evaluation-scenario.v1` files.  They are not
executed by the validator or by the package services.

Use `python scripts/validate_workflow_packages.py --format markdown` to inspect
the managed catalog.  The CLI intentionally accepts no arbitrary input or
output path: it reads only this tracked directory and writes reports to stdout.
