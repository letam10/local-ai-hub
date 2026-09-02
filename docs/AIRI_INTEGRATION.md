# AIRI integration

AIRI is an external Windows installer-managed application. Local AI Hub does
not embed it, relocate it, edit its configuration, or read its credentials.
The Hub owns only a small, path-free application projection and an explicit
ID-only launch route.

## Discovery order

The server uses these sources in order:

1. `Config/application_registry.local.json` when the local JSON document is
   valid and contains an AIRI entry.
2. The Windows AIRI installer identity in the bounded Uninstall registry and
   exact targets published by Windows App Paths.
3. `unavailable` when no trusted candidate can be established.

The tracked `Config/application_registry.example.json` is documentation only;
it never becomes live machine state. The server does not recursively scan a
drive, derive an executable name from an install directory, or parse an
uninstall command.

## Candidate verification

An AIRI candidate is launchable only when all of the following are true:

- the product identity is `AIRI` and the publisher is `Moeru AI`;
- the target is an absolute `.exe` regular file;
- every existing path component is free of symlink/junction/reparse aliases;
- the configured working directory is a verified directory;
- the executable version-resource identity is read successfully; and
- the executable fingerprint is stable across the bounded read.

If more than one distinct verified target remains, the public state is
`ambiguous` and no target is selected. Before every launch, the identity and
fingerprint are verified again, so a changed or replaced executable is
refused.

## Public/API contract

`GET /api/applications` returns the required application fields:

```text
id
display_name
discovery_state
launch_state
launchable
running
running_state
reason_code
```

It may include other sanitized compatibility metadata, but never a raw
executable path, working directory, user-profile path, arguments, command
line, secret, or exception text.

`running_state` is `running`, `not_running`, or `unknown`. It is `running` only
when a bounded Windows-native process enumeration reads an exact executable
path matching the still-verified candidate. A basename match is insufficient;
an access-denied process path is reported as `unknown` and never as a positive
running claim.

The launch contract is:

```http
POST /api/applications/{id}/launch
```

The browser sends only the application ID. The backend resolves the target
from its verified allowlist and ignores browser-supplied executable,
working-directory, argument, or command fields.

The basic UI shows `Mở AIRI` only for a single verified candidate. It shows
`Chưa tìm thấy AIRI` plus `Làm mới trạng thái` when discovery is unavailable,
and explains the local-registry correction without opening an ambiguous
candidate.

No runtime/model/GPU workload is part of this integration. A successful
bounded native launch must be recorded separately from static projection
tests, with the task-owned process identified and closed before handoff.
