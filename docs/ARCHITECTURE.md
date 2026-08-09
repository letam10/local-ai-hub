# Architecture

Local AI Hub is a local coordinator. First-party code belongs under `src/`:
`src/services/api`, `src/services/mcp`, `src/modules`, `src/app`, and
`src/shared`. Heavy runtimes, model weights, cache and outputs remain
machine-local and are installed outside Git. Deprecated `Hub/`, `MCP/`, and
`Adapters/` packages are thin compatibility shims, not the canonical source.

The coordinator exposes a loopback API and an allowlisted stdio MCP bridge.
Adapters call installed services through explicit configuration; they do not
download or embed model weights in the repository.
