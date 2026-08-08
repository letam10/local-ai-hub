# Architecture

Local AI Hub is a local coordinator. First-party code belongs in `Hub/`,
`MCP/`, `Adapters/`, `Apps/` and `Scripts/`. Heavy runtimes, model weights,
cache and outputs remain machine-local and are installed outside Git.

The coordinator exposes a loopback API and an allowlisted stdio MCP bridge.
Adapters call installed services through explicit configuration; they do not
download or embed model weights in the repository.
