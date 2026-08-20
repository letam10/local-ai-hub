# Managed component maintenance

Phase 2 maintenance is plan-first and server-owned. The browser submits only a
fixed component ID and one of `repair`, `update`, or `uninstall`; it never
submits a path, URL, command, executable or file list.

The flow is:

1. inspect bounded catalog leaves, receipts and shared-dependency references;
2. create an opaque maintenance plan with a state fingerprint;
3. show owned files, shared consumers, preserved data and destructive warnings;
4. require explicit confirmation for any write or removal;
5. revalidate the fingerprint immediately before an authorized executor;
6. stage and verify new data before finalization, then write an atomic receipt.

The source-only Phase 2 package provides the inspect/plan/API/UI foundation.
The current tracked catalogs intentionally have incomplete source digests, so
production download and machine mutation remain unavailable until a reviewed
source-specific recipe is added. Existing Models, Environments, runtime,
Output, Projects, Config and Backups are never recursively removed. Shared
models/runtimes remain preserved while another module references them.

Installation jobs and capability states are separate. A completed plan does
not mean a component is installed or operational; bounded verification and a
matching runtime evidence receipt are required for those states.
