"""Deprecated shim for the MCP smoke check."""

from src.services.mcp.mcp_smoke import *  # noqa: F401,F403
from src.services.mcp.mcp_smoke import main

if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
