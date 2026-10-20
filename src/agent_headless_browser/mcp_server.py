"""MCP server exposing the logged-in agent Chrome to AI agents.

Run with ``ahb-mcp`` (stdio). Configure with ``AHB_*`` environment variables,
see :class:`agent_headless_browser.config.Settings`.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import chrome, google, health, looker
from .config import Settings
from .session import AgentSession

P = ParamSpec("P")
R = TypeVar("R")

settings = Settings.from_env()
session = AgentSession(settings)
mcp = MCPServer(
    "agent-browser",
    instructions=(
        "Headless Chrome on a dedicated, already signed-in Google profile. Call browser_status first. "
        "If no account is signed in, call login_begin, let the human sign in, then call login_finish. "
        "Use page_snapshot refs with click and type_text. Never type passwords, never copy the profile "
        "and never export its cookies: that revokes the Google session."
    ),
)


def tool(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Register ``fn`` as a tool whose failures reach the agent as readable errors.

    mcp 2.x hides unexpected exception text from the model; ToolError is shown.
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"{type(exc).__name__}: {str(exc)[:1500]}") from exc

    mcp.tool()(wrapper)
    return wrapper


async def _restart(headful: bool, url: str | None = None) -> str:
    await session.disconnect()
    await asyncio.to_thread(chrome.stop, settings.port)
    return await asyncio.to_thread(chrome.start, settings, headful=headful, url=url)


@tool
async def browser_status() -> dict[str, Any]:
    """Is the agent Chrome running, headless or not, and which Google accounts are signed in."""
    info = chrome.cdp_version(settings.port)
    status: dict[str, Any] = {
        "running": bool(info),
        "endpoint": settings.cdp_url,
        "profile": str(settings.profile_dir),
    }
    if info:
        status |= {
            "browser": info["Browser"],
            "headless": chrome.read_mode(settings.profile_dir) == "headless",
            "google_accounts": await google.signed_in_accounts(await session.context()),
        }
    return status


@tool
async def browser_start(headful: bool = False) -> str:
    """Start the long-lived agent Chrome (headless by default) on the agent profile."""
    return await asyncio.to_thread(chrome.start, settings, headful=headful)


@tool
async def browser_stop() -> str:
    """Gracefully close the agent Chrome so rotated cookies are written to the profile."""
    await session.disconnect()
    return await asyncio.to_thread(chrome.stop, settings.port)


@tool
async def login_begin() -> str:
    """Open the Google sign-in page for a human. The agent must never type credentials.

    With a display: a visible Chrome window. Without one (CLI server): headless, signed in through
    ``ahb login-view``. Call login_finish afterwards."""
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        await _restart(headful=False, url=chrome.LOGIN_URL)
        return (
            "No display on this server. Ask the user to run `ahb login-view` on the server, tunnel it with "
            "`ssh -N -L 9500:127.0.0.1:9500 user@server`, open http://127.0.0.1:9500 and sign in there. "
            "Then call login_finish."
        )
    await _restart(headful=True)
    return "A Chrome window with the Google sign-in page is open. Ask the user to sign in, then call login_finish."


@tool
async def login_finish() -> dict[str, Any]:
    """Verify the sign-in worked, then restart the agent Chrome headless on the same profile."""
    accounts = await google.signed_in_accounts(await session.context())
    if not accounts:
        return {"ok": False, "detail": "no Google account signed in yet"}
    await _restart(headful=False)
    return {
        "ok": True,
        "google_accounts": await google.signed_in_accounts(await session.context()),
        "headless": True,
    }


@tool
async def navigate(url: str, wait_ms: int = 3000) -> dict[str, Any]:
    """Open a URL in the agent tab. Returns the final URL, title and the start of the page text."""
    page = await session.page()
    await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
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
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || '')
      .trim().replace(/\\s+/g, ' ');
    const href = typeof el.href === 'string' && el.href ? ' -> ' + el.href.slice(0, 100) : '';
    out.push(`[${n}] <${el.tagName.toLowerCase()}${el.type ? ' type=' + el.type : ''}> ${label.slice(0, 80)}${href}`);
    if (out.length >= max) break;
  }
  return {text: (document.body ? document.body.innerText : '').slice(0, 6000), elements: out};
}"""


@tool
async def page_snapshot(max_elements: int = 150) -> dict[str, Any]:
    """Visible text plus numbered interactive elements of the agent tab, for click/type_text refs."""
    page = await session.page()
    snap: dict[str, Any] = await page.evaluate(SNAPSHOT_JS, max_elements)
    return {"url": page.url, "title": await page.title(), **snap}


async def _locator(ref: int | None, selector: str | None) -> Any:
    page = await session.page()
    if ref is not None:
        return page.locator(f'[data-agent-ref="{ref}"]')
    if selector:
        return page.locator(selector).first
    raise ToolError("give ref (from page_snapshot) or a Playwright selector")


@tool
async def click(ref: int | None = None, selector: str | None = None) -> dict[str, Any]:
    """Click an element by page_snapshot ref or Playwright selector (css=, text=, role=...)."""
    await (await _locator(ref, selector)).click(timeout=15_000)
    page = await session.page()
    await page.wait_for_timeout(1500)
    return {"url": page.url, "title": await page.title()}


@tool
async def type_text(
    text: str, ref: int | None = None, selector: str | None = None, submit: bool = False
) -> dict[str, Any]:
    """Fill a field by ref or selector; submit=True presses Enter. Never use it for passwords."""
    loc = await _locator(ref, selector)
    await loc.fill(text, timeout=15_000)
    page = await session.page()
    if submit:
        await loc.press("Enter")
        await page.wait_for_timeout(1500)
    return {"url": page.url}


@tool
async def press_key(key: str) -> str:
    """Press a key in the agent tab, e.g. Enter, Escape, PageDown, Control+A."""
    await (await session.page()).keyboard.press(key)
    return "ok"


@tool
async def screenshot(full_page: bool = False) -> Image:
    """PNG screenshot of the agent tab."""
    return Image(data=await (await session.page()).screenshot(full_page=full_page), format="png")


@tool
async def evaluate(script: str) -> str:
    """Run a JavaScript expression or function in the agent tab; returns the result as JSON."""
    return json.dumps(await (await session.page()).evaluate(script), default=str)[:20_000]


@tool
async def looker_export(
    report_url: str, out_dir: str | None = None, page_size: int = 50_000
) -> dict[str, Any]:
    """Export every row of each datasource behind a Looker Studio report to CSV.

    Only datasources used on the opened page are seen; for multi-page reports call it per page URL."""
    target = Path(out_dir) if out_dir else settings.out_dir / "looker"
    results = await looker.export_report(await session.context(), report_url, target, page_size)
    return {"out_dir": str(target), "datasources": [r.to_dict() for r in results]}


@tool
async def health_check(url: str | None = None) -> dict[str, Any]:
    """Load a page that needs the Google session and report whether it still works.

    For a Looker Studio URL it waits for the report's data call. Appends to <out>/session_health.jsonl."""
    record = await health.check(settings.cdp_url, url or settings.health_url)
    health.append_log(record, settings.out_dir / "session_health.jsonl")
    return record


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
