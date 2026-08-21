# V7 release provenance

The V7 release tooling has separate historical, pre-tag, and tagged phases.
It does not create, move, publish, delete, or retag any Git tag.

## Identity phases

The reviewed application version remains the single `src/shared/version.py`
value `7.1.0`; `7.1.1` and `7.2.0` are refused. Git object IDs are measured
from the repository's actual HEAD. This repository uses 40-hex SHA-1 IDs; a
64-hex repository is accepted only when every source/build/tag ID is measured
as 64-hex consistently. Artifact SHA-256 and content fingerprints remain
exactly 64 lowercase hexadecimal characters.

The historical verifier reads the byte-preserved v1
`distribution/release_manifest.json` and returns only finite redacted mismatch
codes. It does not reinterpret that record as a v2 release and never writes it.

The pre-tag verifier accepts only the currently reviewed `intended_tag`
`v7.1.0`, when it is unoccupied and paired with `tag_commit: null`. It proves
the current clean source/build identity but does not select, create, or publish
a tag. `v7.1.1` and `v7.2.0` return the fixed
`INTENDED_TAG_VERSION_MISMATCH` refusal until a separate roadmap-approved
package changes the version/tag allowlist. The tagged verifier requires the
existing reviewed intended tag to resolve exactly to the current source/build
commit. No safe tag syntax is treated as roadmap approval.

Refusal precedence is deterministic: a source branch mismatch is reported
before dirty-source or tag-source mismatch, followed by tag occupancy or
intended-tag availability/version errors. `TAG_SOURCE_MISMATCH` describes a
tag that resolves to a different commit; it is not a substitute for
`SOURCE_BRANCH_MISMATCH`, and neither code authorizes a new tag.

## Detached v2 framing and chain

The v2 record is a closed `release-provenance.v2` object. Its raw detached file
bytes must be canonical sorted-key compact UTF-8 JSON followed by exactly one
terminal LF. Pretty JSON, reordered keys, leading/trailing whitespace, an extra
LF, or a missing LF is rejected even when the object has a matching self-hash.
The self-hash is SHA-256 over the canonical JSON object with only
`manifest_sha256` set to `null`; no other field is removed or normalized.

The provenance chain is:

`source_commit -> build_commit -> build_input_fingerprint -> artifact -> intended_tag`

For this packager `build_commit == source_commit`; it cannot be a post-build
hash commit. Artifact records contain only an allowlisted ID, safe leaf
filename, bounded size, SHA-256 and content fingerprint. No absolute path,
arbitrary URL, command, secret, client mapping or raw Git output is accepted.

## Deterministic selection and output

Core ZIP and Inno Setup consume one explicit tracked-file selection contract.
The verifier audits the actual Inno `Source` rules, expands the real filesystem,
rejects untracked/generated files (except explicitly excluded `__pycache__` and
`.pyc`), and rejects historical v1 manifest inclusion. This prevents parity
from being claimed by comparing one helper with itself.

ZIP entries are sorted and use Deflate level 9, fixed `SOURCE_DATE_EPOCH`,
fixed 1980-01-01 UTC entry metadata, normalized permissions, and bounded
chunked copying. Output overwrite and unsafe staging are refused. The detached
v2 sidecar is written only to the ignored task-owned staging directory and is
not embedded in an artifact whose hash it records. EXE reproducibility remains
`not_claimed` unless two identical clean-clone builds with a pinned toolchain
and epoch prove identical bytes.

The Core `--build` command invokes the same clean/source/ref/build gate before
loading build inputs or creating output/staging paths. Release tooling is
control-plane only: no server, browser, installer execution, media, model,
GPU, download, install, or runtime workload is part of validation.
