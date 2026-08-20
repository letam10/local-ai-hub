# V7 source-availability cache provenance

Source availability is a read-only metadata signal. A normal catalog read may
reuse a local cache entry only when the entry is still within its bounded TTL
and its server-owned binding matches the current record exactly. The binding
covers the component identity, provider/kind, primary and fallback source
descriptors, canonical source identity, source URL digest, record revision,
install strategy, and the current catalog schema/version/fingerprint.

The cache is a bounded `source-availability-cache.v1` envelope. Its parser
rejects duplicate JSON keys, unknown fields, non-finite numbers, malformed
hashes/timestamps/statuses/reason codes, oversized state, symlinked state, and
records without a binding fingerprint. Only finite statuses, reason codes,
source fingerprints, timestamps, and retry bounds are persisted. URLs, local
paths, credentials, response bodies, and arbitrary probe text are never
persisted or returned.

`ProductionCatalog` constructs the current binding from its server-owned
catalog record before reading source status. V2 `UpdateResolver` checks carry
the full catalog binding; the legacy V1 resolver path uses an explicit
record-only binding because V1 has no versioned catalog context. Missing
context, V2 catalog drift, source drift, component mismatch, and legacy
unbound cache records fail closed to `UNKNOWN` / `not_checked`. A force check
is the only path allowed to perform the existing bounded HTTPS probe; it does
not install, apply, or promote a component to operational.

The cache writer remains same-directory atomic and fsynced. The cache is local
machine state and is not a release/catalog source of truth. Existing source
availability classifications, fallback same-identity handling, TTLs, and
manual update semantics remain intact; no startup polling or automatic apply
is introduced.
