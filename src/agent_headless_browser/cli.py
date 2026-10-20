"""``ahb``: command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from patchright.async_api import async_playwright

from . import __version__, chrome, health, login_view, looker
from .config import Settings

LOGIN_HELP = """Sign in without a display:
  1. On the server: ahb login-view
  2. On your PC:    ssh -N -L 9500:127.0.0.1:9500 user@your-server
  3. Open http://127.0.0.1:9500, click the page and type; the header shows "signed in" when done
  4. Then: ahb stop && ahb start"""


def _cmd_start(args: argparse.Namespace, settings: Settings) -> int:
    url = chrome.LOGIN_URL if args.login else None
    print(chrome.start(settings, headful=args.headful, url=url, foreground=args.foreground))
    if args.login:
        print(LOGIN_HELP)
    return 0


def _cmd_stop(args: argparse.Namespace, settings: Settings) -> int:
    print(chrome.stop(settings.port))
    return 0


def _cmd_status(args: argparse.Namespace, settings: Settings) -> int:
    from . import google

    async def run() -> dict[str, object]:
        info = chrome.cdp_version(settings.port)
        if not info:
            return {"running": False, "endpoint": settings.cdp_url}
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(settings.cdp_url)
            accounts = await google.signed_in_accounts(browser.contexts[0])
        return {
            "running": True,
            "endpoint": settings.cdp_url,
            "browser": info["Browser"],
            "headless": chrome.read_mode(settings.profile_dir) == "headless",
            "google_accounts": accounts,
        }

    print(json.dumps(asyncio.run(run()), indent=1))
    return 0


def _cmd_health(args: argparse.Namespace, settings: Settings) -> int:
    log = Path(args.log) if args.log else settings.out_dir / "session_health.jsonl"

    async def run() -> bool:
        while True:
            record = await health.check(settings.cdp_url, args.url or settings.health_url)
            print(json.dumps(record), flush=True)
            health.append_log(record, log)
            if not args.every:
                return bool(record["ok"])
            await asyncio.sleep(args.every)

    return 0 if asyncio.run(run()) else 1


def _cmd_login_view(args: argparse.Namespace, settings: Settings) -> int:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(login_view.serve(settings.cdp_url, args.host, args.port))
    return 0


def _cmd_looker(args: argparse.Namespace, settings: Settings) -> int:
    out = Path(args.out) if args.out else settings.out_dir / "looker"

    async def run() -> list[looker.DatasourceExport]:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(settings.cdp_url)
            return await looker.export_report(browser.contexts[0], args.url, out, args.page_size)

    try:
        results = asyncio.run(run())
    except looker.LookerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps([r.to_dict() for r in results], indent=1))
    return 0


def _cmd_mcp(args: argparse.Namespace, settings: Settings) -> int:
    from .mcp_server import main as mcp_main

    mcp_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ahb", description="Headless Chrome for AI agents, signed in to your own Google account."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="start the agent Chrome (headless by default)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--headful", action="store_true", help="visible window, on the sign-in page")
    mode.add_argument("--login", action="store_true", help="headless, on the sign-in page (CLI servers)")
    p.add_argument("--foreground", action="store_true", help="exec Chrome in place (systemd Type=simple)")
    p.set_defaults(func=_cmd_start)

    sub.add_parser("stop", help="gracefully stop the agent Chrome").set_defaults(func=_cmd_stop)
    sub.add_parser("status", help="running state and signed-in Google accounts").set_defaults(
        func=_cmd_status
    )

    p = sub.add_parser("health", help="check the session still works; exit 1 if not")
    p.add_argument("--url", help="page that needs the session (default: AHB_HEALTH_URL)")
    p.add_argument("--every", type=int, default=0, metavar="SECONDS", help="repeat forever")
    p.add_argument("--log", help="JSONL log (default: <out>/session_health.jsonl)")
    p.set_defaults(func=_cmd_health)

    p = sub.add_parser("login-view", help="live click-and-type view for signing in headless")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9500)
    p.set_defaults(func=_cmd_login_view)

    p = sub.add_parser("looker-export", help="export every row behind a Looker Studio report to CSV")
    p.add_argument("url", help="https://lookerstudio.google.com/reporting/<report-id>[/page/<page-id>]")
    p.add_argument("--out", help="output directory (default: <out>/looker)")
    p.add_argument("--page-size", type=int, default=50_000)
    p.set_defaults(func=_cmd_looker)

    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(func=_cmd_mcp)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout is reserved for results (and the MCP channel)
    )
    try:
        code: int = args.func(args, Settings.from_env())
    except chrome.ChromeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
