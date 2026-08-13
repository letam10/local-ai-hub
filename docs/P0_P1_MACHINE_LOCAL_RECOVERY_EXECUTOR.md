# P0/P1 manager machine-local recovery executor

This package defines a narrow, manager-owned boundary for a future machine-local
recovery session. It is deliberately limited to inspect, plan and preflight.
The checked-in entrypoint does not apply a plan, write Config, create a journal,
start a process, probe a provider, or recover a registry. An invocation without
an explicitly supplied manager session returns a bounded `not_run` message.

## Evidence contract

The manager supplies fixed metadata for the canonical installation and the
preservation guard. The executor independently reads only the fixed Git
identity, the 34-entry preservation manifest, the four source-consumer hashes,
and the four fixed local targets:

- `components.json`
- `model_registry.json`
- `hub_config.json`
- `application_registry.local.json`

Inspection and planning are sanitized and deterministic. They contain no
absolute paths, commands, URLs, credentials, executable claims, or model data.
Only components and model targets that are proven absent can be described as
static candidates; present, malformed, unknown, reparse, hub, and application
targets remain manual review. Candidate projections are `configured` with
`execution: not_run`; they do not mean installed, launchable, running, ready,
or operational.

## Preflight boundary

`execution-authorization.v1` is a manager-issued, one-time, expiring capability
bound to the executor identity, canonical head/tree, preservation digest, fixed
targets, expected target hashes, and plan fingerprint. It is consumed only by
creating a marker in the manager task root. The preflight result requires the
expected preservation guard code, a clean active-Hub/process/lock gate, and a
bounded disk margin. It still returns `apply_allowed: false`.

The capability is an accidental-automation boundary, not protection from an
Administrator or the operating system. Static constants are not proof of
consumer ownership, private controller identity, a process probe, or a live
machine guard. A separate manager-owned executor and fresh explicit authority
would be required before any apply implementation could be considered. This
package intentionally provides no such implementation and does not authorize
machine recovery.

## Verification boundary

Tests use only synthetic temporary roots and injected Git results. They cover
identity and manifest drift, fixed-target states, redaction, capability
expiry/replay/mismatch, guard and resource gates, and the no-write inspect/plan
boundary. No canonical source, local Config, environment, model, runtime,
server, browser, provider, media tool, download, install, or process is used.
