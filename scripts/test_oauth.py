"""Interactive local OAuth smoke test; never prints tokens or credentials."""

import asyncio
import os

from fastmcp import Client


async def main() -> None:
    server_url = os.getenv("MCP_TEST_URL", "http://localhost:8000/mcp")
    async with Client(server_url, auth="oauth") as client:
        tools = await client.list_tools()
        print("Authenticated MCP tools:")
        for tool in tools:
            print(f"- {tool.name}")


if __name__ == "__main__":
    asyncio.run(main())
