"""Deprecated shim for the Local AI MCP server."""

from src.services.mcp.local_ai_mcp_server import *  # noqa: F401,F403
from src.services.mcp.local_ai_mcp_server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
