# V7 Workflow Package Discovery Storage Boundary

The workflow-package catalog is a repository-managed static source. Discovery
uses the fixed `workflow_packages` root and never accepts a client path,
imports a Python entrypoint, starts a process, contacts a provider or claims
runtime readiness.

Discovery walks the root without following symlinks or reparse points. The
root, its existing ancestors, every descriptor parent and the descriptor leaf
must be regular, contained and identity-stable. Descriptor size is checked
before reading. The bounded walk and the read path revalidate directory and
leaf identity after enumeration/read; replacement, same-size swaps, reparse
appearance, directory leaves, containment failures and lstat/read errors are
fixed `managed_*` refusals. Paths, exceptions and descriptor contents never
appear in public errors or catalog records.

Duplicate package ID/version identities remain ambiguous and unavailable.
Malformed, oversized, unknown-field and schema-invalid descriptors remain
excluded. Valid records retain the existing static `partial` and
`execution=not_run` projection; catalog discovery is not evidence that a
workflow graph or any model/runtime backend is operational.

The storage contract is validated with task-owned temporary fixtures for root,
ancestor, parent and leaf reparse/escape cases, directory and lstat failures,
identity replacement during read, bounded oversized reads, duplicate identity
and path-free public errors. No production repository, machine Config,
runtime, model, Output, network, server or browser state is used by these
tests.
