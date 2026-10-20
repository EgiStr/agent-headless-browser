import asyncio, json, pathlib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT = pathlib.Path(__file__).resolve().parents[1]
async def call(s, n, a): 
    r = await s.call_tool(n, a)
    if r.is_error: raise RuntimeError(r.content[0].text)
    try: return json.loads(r.content[0].text)
    except ValueError: return r.content[0].text
async def main():
    p = StdioServerParameters(command=str(ROOT / ".venv/Scripts/python.exe"), args=[str(ROOT / "mcp_server.py")])
    async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        await call(s, "navigate", {"url": "https://en.wikipedia.org/wiki/Main_Page", "wait_ms": 1500})
        snap = await call(s, "page_snapshot", {"max_elements": 300})
        box = next(e for e in snap["elements"] if "type=search" in e)
        print("search box:", box)
        ref = int(box[1:box.index("]")])
        print("type_text:", await call(s, "type_text", {"ref": ref, "text": "Looker Studio", "submit": True}))
        print("press_key:", await call(s, "press_key", {"key": "End"}))
        snap = await call(s, "page_snapshot", {"max_elements": 300})
        link = next(e for e in snap["elements"] if "Talk" in e)
        print("click", link, "->", await call(s, "click", {"ref": int(link[1:link.index("]")])}))
asyncio.run(main())
