# Asset Intelligence Authoring Guide

Author static descriptors under `asset_catalog/` only. They describe metadata already supplied by a managed system; they do not locate, upload, inspect, hash, decode, transform, tag, caption, embed, delete, or move an asset.

## Asset records and catalogs

Every `asset-record.v1` has a bounded opaque `id`, safe label, typed asset block, logical source reference, retention declaration, and closed fingerprint object.

```json
{
  "schema_version": "asset-record.v1",
  "id": "review-source",
  "label": "Review source metadata",
  "asset": {
    "kind": "image",
    "media_type": "image/png",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "bytes": 1024
  },
  "source": {"kind": "managed-catalog", "reference": "review-seed"},
  "retention": {"classification": "standard", "days": 180},
  "fingerprints": {"perceptual": [], "embedding": []}
}
```

Valid kinds are `audio`, `document`, `image`, `other`, and `video`; media type is a small frozen MIME allowlist. SHA-256 is lowercase 64-character hexadecimal metadata. Do not place a path, URL, filename, binary payload, raw media, secret, command, or provider configuration in any field.

An `asset-catalog.v1` wraps one or more complete records. Asset IDs must be unique. Equal SHA-256 values are allowed: static duplicate grouping reports only a `declared_sha256_match` for those equal strings; it never recalculates a hash or proves asset bytes.

### Optional fingerprints

Perceptual entries contain only algorithm (`phash-v1`/`dhash-v1`), bounded hexadecimal digest, and `status: not_run`. Embedding entries contain only opaque provider/model IDs, dimension, digest, and `status: not_run`. Do not add vectors, scores, captions, tags, output files, model names/paths, checkpoints, or runtime settings.

## Provenance lineage

`provenance-lineage.v1` uses opaque nodes and typed edges. The valid relationships are `source_to_recipe`, `source_to_workflow`, `recipe_to_workflow`, `workflow_to_package`, `package_to_run`, and `run_to_derivative`. Node/edge IDs are unique and the graph is a DAG.

Every lineage has `retention_bound_days`; each node's declared `retention_days` must not exceed it. This is descriptive policy only. The retention plan is always dry-run and does not alter an asset.

## Smart Collections

Use a closed condition or nested `all`/`any` group:

```json
{
  "schema_version": "smart-collection.v1",
  "id": "exact-images",
  "label": "Exact duplicate images",
  "query": {
    "all": [
      {"field": "asset.kind", "operator": "eq", "value": "image"},
      {"field": "exact_duplicate", "operator": "eq", "value": true}
    ]
  }
}
```

The DSL has no script/callback/expression field. It evaluates only validated in-memory catalog metadata and returns sorted opaque asset IDs. `in` values are typed, bounded, and unique; numeric comparison is available only for `asset.bytes`. Both nesting and the total number of query nodes are bounded.

## Static validation and export

Run the repository-owned command only:

```powershell
python scripts/validate_asset_intelligence.py --format json
python scripts/validate_asset_intelligence.py --format markdown --catalog-id portrait-asset-catalog
```

The CLI has no arbitrary input/output path option. It reads fixed managed descriptors and prints a `not_run` report to stdout. Python consumers may call the pure import/export, catalog, collection, lineage, report, diff, and dry-run plan functions in `src.services.asset_intelligence`; consumers must revalidate and project only server-owned results.

`Config/asset_intelligence.example.json` is an integration handoff template, not an automatically loaded local-machine configuration in M6C. A future authorized integration may adopt its explicit allow/deny defaults only after it implements its own server-owned configuration validation; this static CLI intentionally never reads an arbitrary configuration path.
