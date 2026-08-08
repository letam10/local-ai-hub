# MCP boundary

The default MCP transport is stdio. The local API binds to loopback only.
Tools are explicitly allowlisted and do not accept arbitrary shell commands.
Any change to transport, bind address, authentication or tool scope requires a
reviewed Pull Request.
