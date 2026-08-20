# V7 release provenance

The V7 release packager emits a detached `release_manifest.v2.json` sidecar
under the ignored, task-owned staging directory `dist/.v7-release-staging/`.
It never rewrites the historical `distribution/release_manifest.json`, and
the attestation sidecar is deliberately not embedded in a ZIP or installer
whose digest it records.

## Identity and chain

The reviewed release input is the existing `src/shared/version.py` value
`7.1.0`, with intended tag `v7.1.0` and source branch
`feature/v7-operational-closure`. A build is refused unless the checkout is
clean, the branch is exact, the intended tag resolves, and the tag commit is
the current source/build commit. The packager reads explicit refs; it does
not infer release identity from tag descriptions and it never creates, moves,
deletes, or retags anything.

The v2 chain is:

`source_commit -> build_commit -> build_input_fingerprint -> artifact metadata -> intended_tag`

For this packager `build_commit == source_commit`; a post-build hash commit is
never accepted. The input fingerprint covers the sorted source selection,
current file sizes/digests, version, fixed ZIP parameters, and the installer
selection parity digest. ZIP entries use sorted names, Deflate level 9, fixed
1980-01-01 UTC metadata, normalized permissions, and bounded chunked copying.
No current time or filesystem mtime participates in the build.

The manifest is closed as `release-provenance.v2`. JSON parsing rejects
duplicate keys and non-finite values. The manifest self-hash is the SHA-256 of
canonical sorted-key compact UTF-8 JSON with only `manifest_sha256` set to
`null`; all other fields remain in the digest. Artifact filenames are single
safe leaves, artifact IDs are unique and allowlisted, and every recorded
digest is exactly 64 lowercase hexadecimal characters.

## Safe invocation

From a clean release checkout, a reviewed ZIP-only build is:

```text
python -B scripts/build_installer.py --no-exe
```

The installer path is checked against the same source selection as the Core
ZIP. An installer build passes the reviewed version explicitly to Inno Setup,
but one build never claims EXE reproducibility. A future claim requires two
identical clean-clone builds with the same pinned toolchain and epoch; absent
that evidence the status remains `not_claimed`, `blocked`, or
`reproducibility_failed`.

The detached verifier is read-only:

```text
python -B scripts/verify_release_provenance.py --historical
python -B scripts/verify_release_provenance.py --manifest <sidecar> --artifact-root <artifact-directory>
```

Verifier output contains only finite status and refusal codes. It does not
return local paths, commands, URLs, credentials, timestamps, or source
contents. The legacy manifest is classified as historical/inconsistent when
its old commit, branch, version, or tag relationship no longer matches; it is
preserved byte-for-byte.

Release tooling is control-plane only. It does not run the Hub, media
processing, models, GPU workloads, or installers during validation.
