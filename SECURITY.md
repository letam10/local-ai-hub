# Security policy

Local AI Hub is currently maintained as a private repository. Privacy does not
replace secret hygiene: API keys, tokens, credentials, private voice references,
model weights and machine-specific configuration must never be committed.

The default API/MCP design is local-only (`127.0.0.1` and stdio). Do not add a
LAN/public bind, tunnel, port forward, shell-execution tool, or credential
logging without explicit review.

If a secret is exposed, stop the push, revoke/rotate it at its provider, remove
it from the Git history if needed, and report the incident without pasting the
secret value.
