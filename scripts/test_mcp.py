"""Drive mcp_server.py over real stdio and exercise each tool."""
import asyncio, json, os, sys, pathlib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parents[1]

def show(name, res):
    for c in res.content:
        t = getattr(c, "text", None)
        print(f"--- {name}:", (t[:700] if t else f"<{c.type} {len(getattr(c, 'data', ''))} b64 chars>"))
    if res.is_error: print("   !! isError")

async def main():
    params = StdioServerParameters(command=str(ROOT / ".venv/Scripts/python.exe"), args=[str(ROOT / "mcp_server.py")])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        print("tools:", [t.name for t in (await s.list_tools()).tools])
        for name, a in [("browser_status", {}),
                        ("navigate", {"url": "https://myaccount.google.com/", "wait_ms": 2000}),
                        ("page_snapshot", {"max_elements": 8}),
                        ("evaluate", {"script": "document.title"}),
                        ("screenshot", {}),
                        ("login_finish", {}),            # signed in -> exercises stop + headless restart
                        ("browser_status", {}),
                        ("health_check", {}),
                        ("looker_export", {"report_url": os.environ["LOOKER_REPORT_URL"],
                                           "out_dir": str(ROOT / "out/mcp_test")})]:
            show(name, await s.call_tool(name, a))

asyncio.run(main())
