"""Sign in to the headless agent Chrome from any browser: a live view you can click and type into.

  python login_view.py --cdp http://127.0.0.1:9333     # then open http://127.0.0.1:9500

On a CLI VPS run it there and tunnel the port: ssh -N -L 9500:127.0.0.1:9500 user@vps
Keystrokes go straight to Chrome and are never logged or stored.
"""
import argparse
import asyncio
import json

from patchright.async_api import async_playwright

ACCOUNTS_URL = "https://accounts.google.com/ListAccounts?gpsia=1&source=ChromiumBrowser&json=standard"
LOGIN_URL = "https://accounts.google.com/ServiceLogin?continue=https://myaccount.google.com/"

HTML = b"""<!doctype html><meta charset=utf-8><title>Agent Chrome sign-in</title>
<style>body{margin:0;font:14px system-ui;background:#111;color:#eee}
header{padding:8px 12px;display:flex;gap:12px;align-items:center}
img{display:block;max-width:100%;cursor:crosshair;outline:2px solid #444}
#st{color:#8f8}</style>
<header><b>Agent Chrome</b><span id=st>connecting</span>
<span>Click the page, then type on your keyboard. Enter/Tab/Backspace work.</span></header>
<img id=v tabindex=0>
<script>
const v=document.getElementById('v'), st=document.getElementById('st');
const post=(p,b)=>fetch(p,{method:'POST',body:JSON.stringify(b)});
function tick(){const i=new Image();i.onload=()=>{v.src=i.src;setTimeout(tick,500)};i.onerror=()=>setTimeout(tick,1500);i.src='/shot.jpg?'+Date.now()}
tick();
setInterval(async()=>{const s=await (await fetch('/status')).json();
 st.textContent=s.accounts.length?('signed in: '+s.accounts.join(', ')+' - you can close this tab'):s.url},2000);
v.onclick=e=>{const r=v.getBoundingClientRect();v.focus();
 post('/click',{x:(e.clientX-r.left)*v.naturalWidth/r.width,y:(e.clientY-r.top)*v.naturalHeight/r.height})};
const keys=new Set(['Enter','Tab','Backspace','Delete','Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End']);
document.onkeydown=e=>{if(e.ctrlKey||e.metaKey||e.altKey)return;
 if(e.key.length===1){post('/type',{text:e.key});e.preventDefault()}
 else if(keys.has(e.key)){post('/key',{key:e.key});e.preventDefault()}};
document.onpaste=e=>{post('/type',{text:e.clipboardData.getData('text')});e.preventDefault()};
</script>"""


async def main(a):
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(a.cdp)
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "accounts.google" in pg.url), None)
        if page is None:
            page = await ctx.new_page()
            await page.goto(LOGIN_URL)
        await page.bring_to_front()
        lock = asyncio.Lock()

        async def handle(reader, writer):
            try:
                head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
                method, path = head.split(" ", 2)[:2]
                length = next((int(l.split(":")[1]) for l in head.split("\r\n") if l.lower().startswith("content-length:")), 0)
                body = json.loads(await reader.readexactly(length)) if length else {}
                ctype, data = "application/json", b"{}"
                async with lock:
                    if path == "/":
                        ctype, data = "text/html", HTML
                    elif path.startswith("/shot.jpg"):
                        ctype, data = "image/jpeg", await page.screenshot(type="jpeg", quality=60)
                    elif path == "/click" and method == "POST":
                        await page.mouse.click(body["x"], body["y"])
                    elif path == "/type" and method == "POST":
                        await page.keyboard.type(body["text"])
                    elif path == "/key" and method == "POST":
                        await page.keyboard.press(body["key"])
                    elif path == "/status":
                        r = await ctx.request.post(ACCOUNTS_URL)
                        accts = [x[3] for x in json.loads(await r.text())[1] if len(x) > 3] if r.status == 200 else []
                        data = json.dumps({"url": page.url[:100], "accounts": accts}).encode()
                writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\n"
                             "Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + data)
                await writer.drain()
            except Exception as e:
                writer.write(b"HTTP/1.1 500 Error\r\nConnection: close\r\n\r\n" + str(e)[:200].encode())
            finally:
                writer.close()

        server = await asyncio.start_server(handle, a.bind, a.port)
        print(f"open http://{a.bind}:{a.port} to sign in (Ctrl+C to quit)", flush=True)
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://127.0.0.1:9333")
    ap.add_argument("--port", type=int, default=9500)
    ap.add_argument("--bind", default="127.0.0.1")
    asyncio.run(main(ap.parse_args()))
