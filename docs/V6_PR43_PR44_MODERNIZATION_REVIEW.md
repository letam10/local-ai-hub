# V6 review of the parked PR #43 and PR #44 lanes

Status: completed semantic review; preservation-only, no cherry-pick or source restore.

## Scope and trusted baseline

The review compares the preserved branches with the current trusted V6 branch
`feature/local-ai-hub-v6` at `22fc521594f6ceb0dc6f5ac0e0ae03cf08e04d51`.
The canonical checkout and its local-only preservation bundle were not used as
an import source.  The parked branches remain evidence-only:

| lane | head | base | changed paths | decision |
| --- | --- | --- | --- | --- |
| PR #43 / LAH 2 | `6b8e56f4e2051c9c3c954e2c6d4fa3f0983cd59b` | `4e1b13ee563c6648df5532a3b7c429f214a339c9` | durable-output docs, `jobs.py`, `artifact_store.py`, `job_manager/durable.py`, and one test file | superseded by V6 |
| PR #44 / LAH 1 | `7ce0317f77a3d5cf6d26e1e64d46d87fcacb91d7` | `4e1b13ee563c6648df5532a3b7c429f214a339c9` | snapshot/a11y docs, UI shell/pages/styles, and two test/fixture files | superseded by V6 |

Both heads are based on the pre-integrity V6 base and are not ancestors of the
current branch.  Whole-commit application would therefore reintroduce older
source context and is explicitly rejected.

## PR #43: durable output and artifact publication

The branch is not obsolete in intent, but its implementation is superseded.
The current V6 sequence `6b97824`, `e5434f6`, `3e78a06`, `d88510f`,
`71a4fde`, `0c3494d`, `eef45f3`, and `e8db8f8` provides the durable admission,
crash recovery, output-root containment, opaque artifact publication,
cancellation serialization, and batch-atomic index behavior.  The current
voice publication fix at `22fc521` also routes Qwen3-TTS and Seed-VC output
through the same job-provenanced artifact path.

The old PR test file is not copied wholesale.  Its behavioral coverage is
represented by the current durable/job/video/vision/voice suites, whose tests
exercise the newer contracts and current data shapes.  There is no identified
PR #43 hunk that can be applied without either duplicating current behavior or
reintroducing pre-V6 persistence assumptions.

Decision: `SUPERSEDED_BY_V6`; no recovery package proposed.

## PR #44: snapshot continuity and accessibility

The branch's goals are represented by the current UI sequence `403c96b`,
`e23a17e`, `d4ebbdc`, `b708b8d`, and `beb892f`.  These changes now provide the
single-window shell, bounded responsive layout, static-only translation,
snapshot values kept outside i18n, focus return for artifact preview, and
finite status labels.  Current tests cover navigation, refresh continuity,
accessibility, shell/dashboard rendering, and evidence projections with the
current V6 API shapes.

The old snapshot fixture and pages/styles hunks are not imported: their base
context predates the current recovery/evidence and UI boundaries, so a
whole-file or whole-commit restore would be a regression.

Decision: `SUPERSEDED_BY_V6`; no recovery package proposed.

## Review result

- `RECOVER_RECOMMENDED`: 0
- `SUPERSEDED_BY_V6`: 2 lanes (all PR #43/#44 changes)
- `OBSOLETE`: 0
- `LOCAL_ONLY`: 0
- `TEST_HELPER`: 0
- `NEEDS_HUMAN_REVIEW`: 0

This review does not authorize source mutation, PR merge, canonical changes, or
runtime/GPU work.  If a future regression is found, it must be raised as a new
scoped package against the current V6 base with a fresh test and QA boundary;
the parked branches remain preserved evidence only.
