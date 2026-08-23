# V8 managed runtime update candidate

The V2 production catalog may carry one server-owned `update_candidate` for a
portable runtime. The candidate is immutable metadata: revision, source
identity, HTTPS source, verified archive size/digest, fixed archive-to-leaf
mapping and bounded extracted-size estimate. It is never accepted from an API
request or inferred by scanning an archive.

Runtime updates remain an explicit plan-and-confirm action. The updater
downloads only the catalog-pinned HTTPS archive through the trusted downloader,
rechecks its size and SHA-256, extracts into a task-owned staging directory and
validates every declared executable leaf before activation. The active V8 slot
is switched with same-volume directory renames; the legacy runtime junction
and unrelated runtime slots are not moved or overwritten. The previous slot is
kept in a server-owned rollback area and the V3 receipt records the candidate
revision and rollback binding.

Rollback is also plan-bound and idempotent. It swaps the active V8 slot back
only when the catalog binding, managed root and rollback directory remain safe;
receipt failure or identity drift fails closed and preserves the ambiguous
bytes for manual review. Repair and uninstall continue to use the existing
catalog-owned maintenance path and never remove unknown files.

No runtime process, FFmpeg command, model, GPU, provider or operational status
is implied by installation or update. A separate bounded verification remains
required before a runtime can be reported operational.
