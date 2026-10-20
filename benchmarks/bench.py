"""Benchmark with the tasks from dev.to "16MB vs 1.2GB: benchmarking 5 AI browser automation tools".

Generic tasks use a throwaway profile and port, so a running, signed-in agent Chrome
is not disturbed. ``--auth-report`` also times a signed-in Looker Studio export
against the agent Chrome given by ``AHB_PORT``.

    pip install -e ".[bench]"
    python benchmarks/bench.py --label linux
    python benchmarks/bench.py --label linux --auth-report https://lookerstudio.google.com/reporting/<id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psutil
from patchright.async_api import BrowserContext, Page, async_playwright

from agent_headless_browser import chrome, google, looker
from agent_headless_browser.config import Settings
from agent_headless_browser.mcp_server import SNAPSHOT_JS

TRANSIENT = ("ERR_NETWORK_CHANGED", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET")
retries = 0


def chrome_memory(profile_dir: Path) -> dict[str, Any]:
    """RSS (the article's metric) and USS of the browser owning ``profile_dir`` plus children.

    Summed RSS counts shared pages once per process, so it overstates Chrome, which
    forks from a zygote on Linux. USS is the memory unique to each process.
    """
    for proc in psutil.process_iter(["cmdline"]):
        cmd = " ".join(proc.info["cmdline"] or [])
        if str(profile_dir) in cmd and "--type=" not in cmd:
            procs = [proc, *proc.children(recursive=True)]
            rss = uss = 0
            for q in procs:
                try:
                    mem = q.memory_full_info()
                except psutil.Error:
                    continue
                rss, uss = rss + mem.rss, uss + mem.uss
            return {"rss_mb": round(rss / 2**20, 1), "uss_mb": round(uss / 2**20, 1), "procs": len(procs)}
    return {}


async def goto(page: Page, url: str, **kw: Any) -> Any:
    """page.goto with up to 2 retries on transient network errors (counted in the report)."""
    global retries
    for attempt in range(3):
        try:
            return await page.goto(url, **kw)
        except Exception as exc:
            if attempt == 2 or not any(t in str(exc) for t in TRANSIENT):
                raise
            retries += 1
            await asyncio.sleep(1)
    return None


async def snapshot_bytes(page: Page) -> int:
    return len(json.dumps(await page.evaluate(SNAPSHOT_JS, 150)).encode())


async def tasks(ctx: BrowserContext, profile_dir: Path) -> dict[str, Any]:
    res: dict[str, Any] = {}

    async def open_snapshot_close() -> float:
        t = time.perf_counter()
        page = await ctx.new_page()
        await goto(page, "https://httpbin.org/html", wait_until="load", timeout=60_000)
        await snapshot_bytes(page)
        await page.close()
        return round(time.perf_counter() - t, 2)

    runs = [await open_snapshot_close() for _ in range(3)]
    res["httpbin_open_snapshot_close_s"] = {"runs": runs, "avg": round(statistics.mean(runs), 2)}

    page = await ctx.new_page()
    await goto(page, "https://httpbin.org/html", wait_until="load")
    res["one_page_mem"] = chrome_memory(profile_dir)
    await page.close()

    t = time.perf_counter()
    pages = await asyncio.gather(*[ctx.new_page() for _ in range(3)])
    await asyncio.gather(*[goto(pg, "https://httpbin.org/html", wait_until="load") for pg in pages])
    res["parallel3_s"] = round(time.perf_counter() - t, 2)
    res["parallel3_mem"] = chrome_memory(profile_dir)
    for pg in pages:
        await pg.close()

    page = await ctx.new_page()
    t = time.perf_counter()
    await goto(page, "https://react.dev/", wait_until="load", timeout=60_000)
    res["spa_react_dev"] = {
        "load_s": round(time.perf_counter() - t, 2),
        "snapshot_bytes": await snapshot_bytes(page),
    }
    await goto(page, "https://github.com/microsoft/playwright", wait_until="load", timeout=60_000)
    res["github_snapshot_bytes"] = await snapshot_bytes(page)

    errors = {}
    for name, url, timeout in [
        ("dns", "https://no-such-host.invalid/", 15_000),
        ("http_404", "https://httpbin.org/status/404", 15_000),
        ("timeout", "https://httpbin.org/delay/10", 3_000),
    ]:
        try:
            resp = await page.goto(url, timeout=timeout)
            errors[name] = f"HTTP {resp.status}" if resp else "no response"
        except Exception as exc:
            errors[name] = str(exc).splitlines()[0][:120]
    res["errors"] = errors
    await page.close()
    return res


async def generic(port: int) -> dict[str, Any]:
    profile = Path(tempfile.mkdtemp(prefix="ahb-bench-")).resolve()
    settings = Settings(port=port, profile_dir=profile, chrome_args=Settings.from_env().chrome_args)
    t = time.perf_counter()
    chrome.start(settings)
    res: dict[str, Any] = {"cold_start_s": round(time.perf_counter() - t, 2)}
    try:
        await asyncio.sleep(2)
        res["idle_mem"] = chrome_memory(profile)
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(settings.cdp_url)
            res |= await tasks(browser.contexts[0], profile)
    finally:
        await asyncio.to_thread(chrome.stop, port)
        shutil.rmtree(profile, ignore_errors=True)
    res["transient_retries"] = retries
    return res


async def auth(report_url: str) -> dict[str, Any]:
    settings = Settings.from_env()
    async with async_playwright() as p:
        ctx = (await p.chromium.connect_over_cdp(settings.cdp_url)).contexts[0]
        accounts = await google.signed_in_accounts(ctx)
        t = time.perf_counter()
        out = Path(tempfile.mkdtemp(prefix="ahb-bench-looker-"))
        results = await looker.export_report(ctx, report_url, out)
    return {
        "signed_in": bool(accounts),
        "looker_export_s": round(time.perf_counter() - t, 2),
        "looker_rows": sum(r.raw_rows for r in results),
    }


async def main(args: argparse.Namespace) -> None:
    report: dict[str, Any] = {
        "label": args.label,
        "platform": sys.platform,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cpu_count": psutil.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "generic": await generic(args.port),
    }
    if args.auth_report:
        report["auth"] = await auth(args.auth_report)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--port", type=int, default=9555, help="throwaway port for the generic run")
    parser.add_argument("--auth-report", help="Looker Studio report URL to export as the signed-in user")
    asyncio.run(main(parser.parse_args()))
