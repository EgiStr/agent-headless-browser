"""Benchmark this stack with the tasks from dev.to "16MB vs 1.2GB: benchmarking 5 AI browser tools".

Generic tasks run on a throwaway profile/port so the logged-in agent Chrome is not
disturbed; --auth-cdp additionally measures the signed-in Google/Looker path.

  python scripts/bench.py --label windows --auth-cdp http://127.0.0.1:9333
"""
import argparse
import asyncio
import json
import os
import pathlib
import shutil
import statistics
import sys
import time

import psutil
from patchright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import looker_crawl  # noqa: E402
import serve_chrome  # noqa: E402
from mcp_server import ACCOUNTS_URL, SNAPSHOT_JS  # noqa: E402

REPORT = os.environ["LOOKER_REPORT_URL"]


def chrome_mem(profile_dir: pathlib.Path) -> dict:
    """Memory of the browser process owning profile_dir plus all its children.

    rss_mb: sum of RSS, the article's metric; it counts shared pages once per
    process, so it overstates Chrome (zygote-forked on Linux).
    uss_mb: sum of memory unique to each process, closer to the RAM actually used."""
    key = str(profile_dir)
    for p in psutil.process_iter(["cmdline"]):
        cmd = " ".join(p.info["cmdline"] or [])
        if key in cmd and "--type=" not in cmd:
            rss = uss = 0
            procs = [p] + p.children(recursive=True)
            for q in procs:
                try:
                    m = q.memory_full_info()
                    rss += m.rss; uss += m.uss
                except psutil.Error:
                    pass
            return {"rss_mb": round(rss / 2**20, 1), "uss_mb": round(uss / 2**20, 1), "procs": len(procs)}
    return {}


async def snapshot_bytes(page) -> int:
    return len(json.dumps(await page.evaluate(SNAPSHOT_JS, 150)).encode())


TRANSIENT = ("ERR_NETWORK_CHANGED", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET")
RETRIES = {"count": 0}


async def goto(page, url, **kw):
    """page.goto with up to 2 retries on transient network errors (counted in the report)."""
    for attempt in range(3):
        try:
            return await page.goto(url, **kw)
        except Exception as e:
            if attempt == 2 or not any(t in str(e) for t in TRANSIENT):
                raise
            RETRIES["count"] += 1
            await asyncio.sleep(1)


async def timed(coro):
    t = time.perf_counter()
    r = await coro
    return round(time.perf_counter() - t, 2), r


async def generic(port: int, profile_dir: pathlib.Path) -> dict:
    res = {}
    if profile_dir.exists():
        shutil.rmtree(profile_dir)
    t = time.perf_counter()
    serve_chrome.start_chrome(str(profile_dir), port, headful=False)
    res["cold_start_s"] = round(time.perf_counter() - t, 2)
    try:
        await asyncio.sleep(2)
        res["idle_mem"] = chrome_mem(profile_dir)
        await _generic_tasks(port, profile_dir, res)
    finally:
        await asyncio.to_thread(serve_chrome.stop_chrome, port)
        shutil.rmtree(profile_dir, ignore_errors=True)
    res["transient_retries"] = RETRIES["count"]
    return res


async def _generic_tasks(port, profile_dir, res):
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]

        async def open_snap_close(url):
            page = await ctx.new_page()
            await goto(page, url, wait_until="load", timeout=60000)
            n = await snapshot_bytes(page)
            await page.close()
            return n

        runs = [(await timed(open_snap_close("https://httpbin.org/html")))[0] for _ in range(3)]
        res["httpbin_open_snapshot_close_s"] = {"runs": runs, "avg": round(statistics.mean(runs), 2)}

        page = await ctx.new_page()
        await goto(page, "https://httpbin.org/html", wait_until="load")
        res["one_page_mem"] = chrome_mem(profile_dir)
        await page.close()

        t = time.perf_counter()
        pages = await asyncio.gather(*[ctx.new_page() for _ in range(3)])
        await asyncio.gather(*[goto(pg, "https://httpbin.org/html", wait_until="load") for pg in pages])
        res["parallel3_s"] = round(time.perf_counter() - t, 2)
        res["parallel3_mem"] = chrome_mem(profile_dir)
        for pg in pages:
            await pg.close()

        page = await ctx.new_page()
        sec, _ = await timed(goto(page, "https://react.dev/", wait_until="load", timeout=60000))
        res["spa_react_dev"] = {"load_s": sec, "snapshot_bytes": await snapshot_bytes(page)}
        await goto(page, "https://github.com/microsoft/playwright", wait_until="load", timeout=60000)
        res["github_snapshot_bytes"] = await snapshot_bytes(page)
        res["github_full_text_bytes"] = len((await page.evaluate("document.body.innerText")).encode())

        errors = {}
        for name, url, timeout in [("dns", "https://no-such-host.invalid/", 15000),
                                   ("http_404", "https://httpbin.org/status/404", 15000),
                                   ("timeout", "https://httpbin.org/delay/10", 3000)]:
            try:
                r = await page.goto(url, timeout=timeout)
                errors[name] = f"HTTP {r.status}" if r else "no response"
            except Exception as e:
                errors[name] = str(e).splitlines()[0][:120]
        res["errors"] = errors
        await page.close()


async def auth(cdp: str, out: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        r = await browser.contexts[0].request.post(ACCOUNTS_URL)
        accounts = [a[3] for a in json.loads(await r.text())[1] if len(a) > 3] if r.status == 200 else []
    args = argparse.Namespace(url=REPORT, mode="attach", profile=None, cdp=cdp, cookies=None, headful=False,
                              page_size=50000, name=None, out=out)
    sec, summary = await timed(looker_crawl.crawl(args))
    return {"google_accounts": accounts, "looker_export_s": sec,
            "looker_rows": sum(d["raw_rows"] for d in summary.values())}


async def main(a):
    report = {"label": a.label, "platform": sys.platform, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
              "cpu_count": psutil.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 2**30, 1)}
    report["generic"] = await generic(a.port, (ROOT / "profiles" / f"bench_{a.label}").resolve())
    if a.auth_cdp:
        report["auth"] = await auth(a.auth_cdp, str(ROOT / "out" / f"bench_{a.label}"))
    path = ROOT / "out" / f"bench_{a.label}.json"
    path.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--port", type=int, default=9555)
    ap.add_argument("--auth-cdp")
    asyncio.run(main(ap.parse_args()))
