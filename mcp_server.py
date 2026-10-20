"""MCP server: a headless Chrome on a logged-in agent profile, for AI agents.

One long-lived Chrome (serve_chrome.py) owns the profile; every tool attaches
to it over CDP. Config via env: AGENT_BROWSER_PORT (9333), AGENT_BROWSER_PROFILE
(profiles/agent), AGENT_BROWSER_OUT (out).
"""
import asyncio
import contextlib
import functools
import json
import os
import pathlib
import sys
import types

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from patchright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import healthcheck  # noqa: E402
import looker_crawl  # noqa: E402
import serve_chrome  # noqa: E402

PORT = int(os.environ.get("AGENT_BROWSER_PORT", "9333"))
PROFILE = os.environ.get("AGENT_BROWSER_PROFILE", "profiles/agent")
OUT = pathlib.Path(os.environ.get("AGENT_BROWSER_OUT", ROOT / "out"))
CDP = f"http://127.0.0.1:{PORT}"
ACCOUNTS_URL = "https://accounts.google.com/ListAccounts?gpsia=1&source=ChromiumBrowser&json=standard"

mcp = MCPServer("agent-browser", instructions=(
    "Headless Chrome on a dedicated, already logged-in Google profile. Call browser_status first. "
    "If no account is signed in, call login_begin, let the human sign in, then login_finish. "
    "Use page_snapshot refs with click/type_text. Never copy the profile or export its cookies: "
    "that revokes the Google session."))

_state = types.SimpleNamespace(pw=None, browser=None, page=None)


def tool(fn):
    """Register fn as an MCP tool whose failures reach the agent as readable errors."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"{type(e).__name__}: {str(e)[:1500]}") from e
    return mcp.tool()(wrapper)


async def _page():
    """The agent's working tab, reconnecting to Chrome if needed."""
    if _state.browser is None or not _state.browser.is_connected():
        if not serve_chrome.version(PORT):
            raise RuntimeError(f"agent Chrome is not running on {CDP}; call browser_start")
        _state.pw = _state.pw or await async_playwright().start()
        _state.browser = await _state.pw.chromium.connect_over_cdp(CDP)
        _state.page = None
    if _state.page is None or _state.page.is_closed():
        _state.page = await _state.browser.contexts[0].new_page()
    return _state.page


async def _drop_connection():
    if _state.browser is not None:
        with contextlib.suppress(Exception):
            await _state.browser.close()  # disconnect only; Chrome keeps running
    _state.browser = _state.page = None


async def _accounts() -> list[str]:
    page = await _page()
    r = await page.context.request.post(ACCOUNTS_URL)
    if r.status != 200:
        return []
    return [a[3] for a in json.loads(await r.text())[1] if len(a) > 3 and a[3]]


@tool
async def browser_status() -> dict:
    """Is the agent Chrome running, headless or not, and which Google accounts are signed in."""
    v = serve_chrome.version(PORT)
    if not v:
        return {"running": False, "endpoint": CDP, "profile": PROFILE}
    return {"running": True, "endpoint": CDP, "profile": PROFILE, "browser": v["Browser"],
            "headless": serve_chrome.mode(PROFILE) == "headless", "google_accounts": await _accounts()}


@tool
async def browser_start(headful: bool = False) -> str:
    """Start the long-lived agent Chrome (headless by default) on the agent profile."""
    return await asyncio.to_thread(serve_chrome.start_chrome, PROFILE, PORT, headful)


@tool
async def browser_stop() -> str:
    """Gracefully close the agent Chrome so rotated cookies are written to the profile."""
    await _drop_connection()
    return await asyncio.to_thread(serve_chrome.stop_chrome, PORT)


@tool
async def login_begin() -> str:
    """Restart the agent Chrome with a visible window on the Google sign-in page.

    A human must type the password and finish 2FA in that window; the agent must not.
    Then call login_finish."""
    await _drop_connection()
    await asyncio.to_thread(serve_chrome.stop_chrome, PORT)
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        # CLI-only server: stay headless; the human signs in through chrome://inspect.
        await asyncio.to_thread(serve_chrome.start_chrome, PROFILE, PORT, False, serve_chrome.LOGIN_URL)
        return ("No display on this server. Ask the user to run `python login_view.py` on the server, "
                "tunnel it with `ssh -N -L 9500:127.0.0.1:9500 user@server`, open http://127.0.0.1:9500, "
                "and sign in there. Then call login_finish.")
    await asyncio.to_thread(serve_chrome.start_chrome, PROFILE, PORT, True)
    return ("A Chrome window with the Google sign-in page is open. Ask the user to sign in there, "
            "then call login_finish.")


@tool
async def login_finish() -> dict:
    """Check the sign-in worked, then restart the agent Chrome headless on the same profile."""
    accounts = await _accounts()
    if not accounts:
        return {"ok": False, "detail": "no Google account signed in yet; the window is still open"}
    await _drop_connection()
    await asyncio.to_thread(serve_chrome.stop_chrome, PORT)
    await asyncio.to_thread(serve_chrome.start_chrome, PROFILE, PORT, False)
    return {"ok": True, "google_accounts": await _accounts(), "headless": True}


@tool
async def navigate(url: str, wait_ms: int = 3000) -> dict:
    """Open a URL in the agent tab. Returns final URL, title and the start of the page text."""
    page = await _page()
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(wait_ms)
    text = await page.evaluate("document.body ? document.body.innerText : ''")
    return {"url": page.url, "title": await page.title(), "text": text[:3000]}


SNAPSHOT_JS = """(max) => {
  const sel = 'a[href],button,input,textarea,select,[role=button],[role=link],[role=tab],[role=menuitem],[contenteditable=true]';
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') continue;
    el.setAttribute('data-agent-ref', String(++n));
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || '').trim().replace(/\\s+/g, ' ');
    out.push(`[${n}] <${el.tagName.toLowerCase()}${el.type ? ' type=' + el.type : ''}> ${label.slice(0, 80)}${el.href ? ' -> ' + el.href.slice(0, 100) : ''}`);
    if (out.length >= max) break;
  }
  return {text: (document.body ? document.body.innerText : '').slice(0, 6000), elements: out};
}"""


@tool
async def page_snapshot(max_elements: int = 150) -> dict:
    """Visible text plus numbered interactive elements of the agent tab, for click/type_text refs."""
    page = await _page()
    snap = await page.evaluate(SNAPSHOT_JS, max_elements)
    return {"url": page.url, "title": await page.title(), **snap}


def _locator(page, ref: int | None, selector: str | None):
    if ref is not None:
        return page.locator(f'[data-agent-ref="{ref}"]')
    if selector:
        return page.locator(selector).first
    raise ValueError("give ref (from page_snapshot) or a Playwright selector")


@tool
async def click(ref: int | None = None, selector: str | None = None) -> dict:
    """Click an element by page_snapshot ref or Playwright selector (css=, text=, role=...)."""
    page = await _page()
    await _locator(page, ref, selector).click(timeout=15000)
    await page.wait_for_timeout(1500)
    return {"url": page.url, "title": await page.title()}


@tool
async def type_text(text: str, ref: int | None = None, selector: str | None = None,
                    submit: bool = False) -> dict:
    """Fill a field by ref or selector; submit=True presses Enter. Never use for passwords."""
    page = await _page()
    loc = _locator(page, ref, selector)
    await loc.fill(text, timeout=15000)
    if submit:
        await loc.press("Enter")
        await page.wait_for_timeout(1500)
    return {"url": page.url}


@tool
async def press_key(key: str) -> str:
    """Press a key in the agent tab, e.g. Enter, Escape, PageDown, Control+A."""
    page = await _page()
    await page.keyboard.press(key)
    return "ok"


@tool
async def screenshot(full_page: bool = False) -> Image:
    """PNG screenshot of the agent tab."""
    page = await _page()
    return Image(data=await page.screenshot(full_page=full_page), format="png")


@tool
async def evaluate(script: str) -> str:
    """Run a JavaScript expression or function in the agent tab; returns JSON of the result."""
    page = await _page()
    return json.dumps(await page.evaluate(script), default=str)[:20000]


@tool
async def looker_export(report_url: str, out_dir: str | None = None, page_size: int = 50000) -> dict:
    """Export every row of each datasource behind a Looker Studio report to CSV.

    Loads the report as the signed-in account, captures its batchedDataV2 call,
    and replays it with all dimensions + Record Count, paging until complete.
    Only datasources used on the report's landing page are seen."""
    args = types.SimpleNamespace(url=report_url, mode="attach", profile=PROFILE, cdp=CDP, cookies=None,
                                 headful=False, page_size=page_size, name=None,
                                 out=str(out_dir or OUT / "looker"))
    try:
        with contextlib.redirect_stdout(sys.stderr):  # stdout is the MCP channel
            summary = await looker_crawl.crawl(args)
    except SystemExit as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "out_dir": args.out, "datasources": summary}


@tool
async def health_check(url: str = healthcheck.DEFAULT_URL) -> dict:
    """Load a page that needs the Google session and report whether it still works.

    For a Looker Studio URL it waits for the report's data call; logs to out/session_health.jsonl."""
    rec = await healthcheck.check(CDP, url)
    with open(OUT / "session_health.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    mcp.run()
