# V7 Workflow Package Discovery Storage

Workflow Package discovery is a read-only, server-owned static projection. Its
only filesystem authority is the repository-managed `workflow_packages`
directory; callers do not supply a root, descriptor path, archive, URL, or
execution command.

Before a descriptor is read, the catalog validates the fixed root, its existing
ancestors, every directory down to the descriptor parent, and the descriptor
itself with no-follow `lstat` checks. Reparse points, symlinks, escapes,
non-regular descriptors, missing parents, and identity/stat failures are
refused with finite redacted error codes. The read is bounded by the existing
package size limit, and the complete directory/leaf identity chain is checked
again after the read. This catches replacement races, including a replacement
with the same size and bytes.

Discovery uses a bounded no-follow directory walker rather than recursive
path resolution. Nested directories are limited by depth and total entries;
descriptor-shaped directories, reparses, non-regular entries, directory
identity changes, and scan bounds produce fixed errors without echoing a path,
filename, exception, URL, secret, or object value. Package and scenario JSON
continue to use the existing duplicate-key, finite-number, closed-schema,
catalog-ready, and reference-ambiguity validation. Only unambiguous,
catalog-ready records are projected.

The public result remains static and truthful: `partial` or `unavailable`,
fixed reasons/actions and errors, and `execution: "not_run"`. Capability
Gateway and Module Manager consumers continue to receive the existing
server-owned projection and cannot turn discovery into runtime execution or an
operational claim. This package performs no subprocess, import execution,
socket, network, provider, download, install, workflow, or runtime action.
