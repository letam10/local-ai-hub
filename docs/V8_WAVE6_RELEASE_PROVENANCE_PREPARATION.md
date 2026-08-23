# V8 Wave 6 — Release Provenance Preparation

Status: **SOURCE POLICY IMPLEMENTED; V8.0.1 IDENTITY PREPARED; RELEASE EXECUTION NOT APPROVED**.

Wave 6 does not release Local AI Hub V8. It prepares a generation-specific, read-only release identity/provenance contract so Wave 5 no longer depends on V7 release constants just to explain why V8 is blocked.

## 1. Why this wave exists

Wave 5 originally detected an unreviewed V8 release by importing the historical V7 provenance constants. That was safe as a blocker, but it mixed generations. Wave 6 separates the contracts:

- historical V7 release evidence remains immutable;
- V8 gets its own tracked release policy;
- the V8 policy may describe what a valid release identity must look like without selecting or activating one;
- user approval is required before candidate identity preparation; tag creation,
  main merge and release build/publish remain separately user-controlled.

## 2. Tracked source contract

Owner files:

- `architecture/v8_release_policy.json`
- `scripts/v8_release_provenance.py`
- `tests/test_v8_wave6_release_provenance.py`

The policy fixes these invariants:

- generation is V8;
- `release_branch` records the V8 generation/integration line as historical
  metadata (`feature/local-ai-hub-v8`); it is not the current checkout,
  temporary PR branch, or release authorization authority;
- stable release version must be SemVer major 8;
- tag must match the version exactly as `v<version>` and start with `v8.`;
- release tags are immutable;
- candidate version/tag are selected in the tracked policy only after explicit
  user preparation approval;
- release identity approval is explicit and separate from tag creation, main
  merge, and publication approval;
- historical V7 release evidence is immutable;
- all Windows acceptance gates and exact source binding are required before release activation.

## 3. Read-only candidate dry-run

A proposed release identity can be checked without writing anything:

```powershell
python scripts/v8_release_provenance.py --candidate-version 8.0.0 --candidate-tag v8.0.0
```

This command only validates syntax/policy and checks that the proposed tag is not already occupied in the local Git repository. It does not change tracked files or refs.

`8.0.0 / v8.0.0` in the example is **not an approved release identity**. It is an example input only.

## 4. Wave 5 re-audit hardening

Wave 6 starts only after re-auditing Wave 5. The re-audit found that a PASS gate previously carried a declared `report_sha256` but the preflight did not independently open the corresponding local report and compare actual bytes.

The acceptance contract is now stronger:

- every PASS gate requires sibling local file `reports/<gate_id>.json` next to `evidence.json`;
- actual report bytes are bounded and SHA-256 hashed by the preflight;
- actual digest must equal the digest declared in `evidence.json`;
- report must bind exact gate ID, `windows-x64`, exact source commit and PASS status;
- report checks are finite and every declared check must be `true`;
- report/evidence paths are never emitted in the public result.

This closes the gap where an arbitrary digest string could otherwise stand in for evidence.

## 4A. Source-branch and exact-commit binding

The preparation branch may be a temporary review branch such as
`fix/v8.0.1-stable-product-shell`. The policy's `release_branch` value is not
compared with the current Git checkout and must not authorize a candidate merely
because a branch name matches.

The binding authority is instead:

- the exact source commit/tree in acceptance evidence and the candidate
  manifest;
- the exact source commit checked by the acceptance verifier; and
- after activation, an immutable tag whose peeled target equals the approved
  release commit. A wrong tag target is rejected even when the tag name and
  version are correct.

This keeps historical V8 generation metadata useful without turning a
temporary branch name into a permanent trust boundary.

## 5. What remains blocked

Even after Wave 6 source policy passes, V8 release remains blocked until:

1. Codex/local QA completes the required Windows gates in `Plan_Miss.md` and creates valid local report/evidence files;
2. the user explicitly chooses and approves the final V8 version/tag identity;
3. a separate activation package updates product version and V8 release provenance/build inputs consistently;
4. strict acceptance passes on the exact release source commit;
5. the user explicitly approves main merge/tag/release actions.

Do not edit V7 historical release manifests/tags to satisfy V8 gates. Do not create a V8 tag merely because a dry-run candidate is available.
