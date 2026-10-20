"""Agent browser: one entry point, two ways to get a logged-in Chrome.

  attach   - attach over CDP to a Chrome that is already running and logged in
             (desktop Chrome with chrome://inspect remote debugging, or a VPS
             Chrome started with --remote-debugging-port under Xvfb).
  headless - launch real Chrome headless on a dedicated persistent profile dir,
             optionally seeded with exported cookies.
"""
import json
import pathlib
from contextlib import asynccontextmanager

from patchright.async_api import async_playwright

DESKTOP_UD = pathlib.Path.home() / "AppData/Local/Google/Chrome/User Data"


def desktop_ws_endpoint() -> str:
    port, path = (DESKTOP_UD / "DevToolsActivePort").read_text().split()
    return f"ws://127.0.0.1:{port}{path}"


@asynccontextmanager
async def open_context(mode: str, profile_dir: str = "profiles/agent", cdp: str | None = None,
                       cookies_file: str | None = None, headless: bool = True):
    async with async_playwright() as p:
        if mode == "attach":
            browser = await p.chromium.connect_over_cdp(cdp or desktop_ws_endpoint(), timeout=60000)
            ctx = browser.contexts[0]
            yield ctx
            return  # never close the user's browser
        ctx = await p.chromium.launch_persistent_context(
            profile_dir, channel="chrome", headless=headless, no_viewport=True,
            args=["--disable-blink-features=AutomationControlled",
                  # Keep Google sessions unbound to this machine's TPM so the
                  # profile/cookies survive headless runs and a move to a VPS.
                  "--disable-features=DeviceBoundSessions,EnableBoundSessionCredentials"])
        if cookies_file:
            await ctx.add_cookies(json.loads(pathlib.Path(cookies_file).read_text()))
        try:
            yield ctx
        finally:
            await ctx.close()


async def export_cookies(ctx, out: str, domain_filter: str = "google"):
    cookies = [c for c in await ctx.cookies() if domain_filter in c["domain"]]
    pathlib.Path(out).write_text(json.dumps(cookies, indent=1))
    return len(cookies)
