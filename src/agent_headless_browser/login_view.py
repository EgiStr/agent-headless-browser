"""Sign in to the headless agent Chrome from any browser: a live view you click and type into.

Made for CLI-only servers: run ``ahb login-view`` there, tunnel the port with
``ssh -N -L 9500:127.0.0.1:9500 user@server`` and open http://127.0.0.1:9500.
Keystrokes go straight to Chrome and are never logged or stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from patchright.async_api import Page, async_playwright

from . import google
from .chrome import LOGIN_URL

log = logging.getLogger(__name__)

HTML = b"""<!doctype html><meta charset=utf-8><title>Agent Chrome sign-in</title>
<style>body{margin:0;font:14px system-ui;background:#111;color:#eee}
header{padding:8px 12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
img{display:block;max-width:100%;cursor:crosshair;outline:2px solid #444}#st{color:#8f8}</style>
<header><b>Agent Chrome</b><span id=st>connecting</span>
<span>Click the page, then type on your keyboard. Enter, Tab and Backspace work.</span></header>
<img id=v tabindex=0 alt="live view of the headless Chrome tab">
<script>
const v=document.getElementById('v'), st=document.getElementById('st');
const post=(p,b)=>fetch(p,{method:'POST',body:JSON.stringify(b)});
function tick(){const i=new Image();i.onload=()=>{v.src=i.src;setTimeout(tick,500)};
 i.onerror=()=>setTimeout(tick,1500);i.src='/shot.jpg?'+Date.now()}
tick();
setInterval(async()=>{const s=await (await fetch('/status')).json();
 st.textContent=s.accounts.length?('signed in: '+s.accounts.join(', ')+'. You can close this tab.'):s.url},2000);
v.onclick=e=>{const r=v.getBoundingClientRect();v.focus();
 post('/click',{x:(e.clientX-r.left)*v.naturalWidth/r.width,y:(e.clientY-r.top)*v.naturalHeight/r.height})};
const keys=new Set(['Enter','Tab','Backspace','Delete','Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End']);
document.onkeydown=e=>{if(e.ctrlKey||e.metaKey||e.altKey)return;
 if(e.key.length===1){post('/type',{text:e.key});e.preventDefault()}
 else if(keys.has(e.key)){post('/key',{key:e.key});e.preventDefault()}};
document.onpaste=e=>{post('/type',{text:e.clipboardData.getData('text')});e.preventDefault()};
</script>"""


def _response(status: str, ctype: str, body: bytes) -> bytes:
    head = (
        f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + body


async def serve(cdp_url: str, host: str = "127.0.0.1", port: int = 9500) -> None:
    """Serve the live view until cancelled."""
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]
        page: Page | None = next((pg for pg in ctx.pages if "accounts.google" in pg.url), None)
        if page is None:
            page = await ctx.new_page()
            await page.goto(LOGIN_URL)
        await page.bring_to_front()
        lock = asyncio.Lock()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
                method, path = head.split(" ", 2)[:2]
                length = next(
                    (
                        int(line.split(":")[1])
                        for line in head.split("\r\n")
                        if line.lower().startswith("content-length:")
                    ),
                    0,
                )
                body: dict[str, Any] = json.loads(await reader.readexactly(length)) if length else {}
                ctype, data = "application/json", b"{}"
                async with lock:
                    if path == "/":
                        ctype, data = "text/html", HTML
                    elif path.startswith("/shot.jpg"):
                        ctype, data = "image/jpeg", await page.screenshot(type="jpeg", quality=60)
                    elif method == "POST" and path == "/click":
                        await page.mouse.click(float(body["x"]), float(body["y"]))
                    elif method == "POST" and path == "/type":
                        await page.keyboard.type(str(body["text"]))
                    elif method == "POST" and path == "/key":
                        await page.keyboard.press(str(body["key"]))
                    elif path == "/status":
                        accounts = await google.signed_in_accounts(ctx)
                        data = json.dumps({"url": page.url[:100], "accounts": accounts}).encode()
                writer.write(_response("200 OK", ctype, data))
                await writer.drain()
            except Exception as exc:
                log.debug("login-view request failed", exc_info=True)
                writer.write(_response("500 Internal Server Error", "text/plain", str(exc)[:200].encode()))
            finally:
                writer.close()

        server = await asyncio.start_server(handle, host, port)
        log.info("open http://%s:%d to sign in (Ctrl+C to quit)", host, port)
        async with server:
            await server.serve_forever()
