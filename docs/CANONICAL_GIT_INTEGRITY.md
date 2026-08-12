# Canonical Git integrity guard

This guard protects the top-level `D:\LocalAIHub` checkout used by LocalAIHub
automation. It is a read-only control-plane boundary: it verifies the
canonical repository identity and can return a decision for a manager-issued
disposable-worktree lease. It has no cleanup executor and never runs
`git clean`, reset, rebase, prune, worktree removal, branch deletion, force
push, process termination, or any other mutation.

## Canonical preflight

`python scripts/canonical_git_guard.py inspect` prints only a fixed sanitized
projection. `preflight` performs the same checks and appends one bounded event
to the ignored local `Reports/canonical_git_integrity.local.json` snapshot.
The snapshot is versioned, atomic, limited to 32 events, and contains only
finite states, fingerprints, a timestamp, an operation code, and a dirty
boolean. Paths, URLs, commands, Git output, filenames, logs, user values and
secrets are never persisted or printed. A malformed, oversized, linked, or
unreadable snapshot fails closed; a writer failure refuses the preflight.

The canonical root and `.git` must be real directories, the Git top level and
common directory must match the canonical identity, HEAD must be a 40-character
commit, the branch must be a safe non-default branch, and exactly one HTTPS
origin must normalize to the allowlisted LocalAIHub repository. Dirty or
ambiguous canonical state returns `CANONICAL_PRESERVATION_REQUIRED`; no
automatic stash, reset, clean, checkout, or integration is attempted.

## Disposable-worktree decision

The decision API accepts only an in-memory `OwnedTemporaryWorktree` lease
issued by the trusted manager and an injected positive
`OwnedProcessProbe(known=True, owned_count=0)`. It revalidates that the target
is an existing, clean, non-linked disposable worktree outside the canonical
root and `.git`, with the expected linked-worktree metadata, branch, base
ancestry, HEAD and common Git directory. Any uncertainty, dirty/untracked
state, path relation, identity mismatch, or owned process refuses with a
finite code. The result is a decision only; callers remain responsible for
any separately authorized action.

The guard protects LocalAIHub automation that invokes it. It cannot prevent an
Administrator, the operating system, another process, or an external tool
from deleting or changing files outside this guard.
