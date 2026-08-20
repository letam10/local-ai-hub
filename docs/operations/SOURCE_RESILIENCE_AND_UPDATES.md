# Source resilience and component updates

The tracked production catalog now uses `v7-production-catalog.v2` (the
loader remains backward-compatible with v1 fixtures).  Its companion schema
`Config/v7_production_catalog.v2.schema.json` defines reviewed source identity,
fallback identity, installation strategy, dependency/runtime binding, exact
leaves, size/hash evidence and separate upstream-versus-supported revisions.

V7 keeps two independent facts for every component:

1. **Local installation state** — the managed leaves, receipts, runtime
   compatibility and bounded verification evidence owned by this installation.
2. **Upstream source state** — whether the reviewed primary or an identity-matched
   trusted fallback can currently provide new bytes.

An upstream 404, private repository, timeout or rate limit never changes an
existing local `OPERATIONAL` component into `NOT_INSTALLED`. It only affects a
new install, repair that needs missing bytes, or an update that needs a source.
The public source projection contains finite status codes and fingerprints, not
URLs, local paths or response bodies.

## Source checking

Normal catalog and dashboard reads use the ignored local source-status cache.
There is no startup scan of every provider and no 24/7 polling. An explicit
`Check Source`, `Check Update`, or `Check All Updates` request may perform a
bounded HTTPS metadata probe. Results are cached with `checked_at`, `expires_at`,
safe reason code and the selected primary/fallback identity.

Allowed source states are `AVAILABLE`, `DEGRADED`, `AUTH_REQUIRED`,
`LICENSE_REQUIRED`, `RATE_LIMITED`, `UNAVAILABLE` and `UNKNOWN`. A fallback is
usable only when its catalog identity matches the primary artifact/version.
Random mirrors and client-supplied URLs are never accepted.

## Update policy

The default policy is `manual`. Optional policies are `startup_24h`, `daily` and
`weekly`; they perform metadata checks only and never install automatically.
The UI exposes per-component `Check Update`, global `Check All Updates`, and the
schedule selector. A check returns current revision, latest upstream revision,
latest supported revision, source state, compatibility and changed parts.

The four changed-part categories are `backend`, `runtime`, `dependencies` and
`model`. An upstream revision newer than the supported compatibility range is
reported as `UPSTREAM_NEWER_UNSUPPORTED`, not offered as a destructive update.

## Plans, activation and rollback

An update plan binds the component ID, catalog fingerprint, current revision,
candidate fingerprint and changed parts. Only a catalog-owned immutable
candidate can be staged. The candidate is verified before activation; the
currently active managed version is moved into a component-owned rollback slot
and is never deleted as part of activation. A failed switch restores the active
version. Rollback is explicit and plan-independent only after a receipt proves a
retained candidate slot.

If the catalog has no pinned candidate, the result is `update_candidate_unavailable`
and the user is offered manual import or a reviewed source plan. The system never
pretends that a plan-only response installed bytes.

## Manual import and reuse

Native desktop selection creates a short-lived opaque selection ID. The browser
does not send a raw path. The server validates the selection against the fixed
catalog leaves, size/hash evidence and reparse policy, then either copies into a
previously absent managed model root or references an already managed location.
Existing targets are never overwritten. Successful imports write a path-free
`component-install-receipts.v2` record with `source=manual_import` and state
`INSTALLED_UNVERIFIED` until the real adapter verification passes.

The Components page also exposes a composite bundle plan.  A model request is
expanded into the reviewed runtime/dependency graph in dependency order; shared
dependencies are counted once and already-installed leaves are marked
`reuse_existing`.  Confirmation delegates each step to the same server-owned
component installer used by the normal UI route.  It never accepts a client
path, URL, command or executable.  A complete managed installation can be
registered separately with the receipt-only Existing Install Reuse action; it
does not copy, redownload or overwrite bytes.

The native desktop bridge exposes the picker under a nested
`component_import.select_source` namespace.  It stores the selected file/folder
server-side and returns only the short-lived selection ID to the WebView.  A
browser-only session therefore cannot manufacture a local path or bypass the
catalog validation.

Repair can refresh a receipt when all owned leaves remain present and safe. If a
leaf is missing and no trusted candidate is available, repair stops with
`repair_source_required`; it does not fabricate or delete files. Uninstall is
explicit, stale-plan checked and removes only catalog-owned ordinary leaves;
shared dependencies and user data are preserved.

These contracts are part of the same server-owned component architecture as
normal jobs and adapters. They do not add Web/Cloud control, provider accounts,
or automatic model downloads.
