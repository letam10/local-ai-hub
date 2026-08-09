from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "src" / "services" / "mcp" / "local_ai_mcp_server.py"


async def main() -> int:
    python = os.environ.get("LOCALAIHUB_PYTHON") or sys.executable
    params = StdioServerParameters(command=python, args=[str(SERVER)], env={**os.environ, "PYTHONPATH": str(ROOT)})
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            health = await session.call_tool("get_health", {})
            models = await session.call_tool("list_models", {})
            print(json.dumps({"tool_count": len(tools.tools), "health": str(health), "models": str(models)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
