"""Light session check against the long-lived Chrome; appends one JSON line per run.

  python healthcheck.py                         # once, against AGENT_HEALTH_URL
  python healthcheck.py --url <report> --every 1800   # loop forever, every 30 min

A Looker Studio URL passes only if the report's data call (batchedDataV2) returns 200;
any other URL passes if it loads without bouncing to the Google sign-in page.
"""
import argparse
import asyncio
import json
import os
import time

from patchright.async_api import async_playwright

DEFAULT_URL = os.environ.get("AGENT_HEALTH_URL", "https://myaccount.google.com/")


async def check(cdp, url=DEFAULT_URL):
    looker = "lookerstudio.google.com" in url or "datastudio.google.com" in url
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        page = await browser.contexts[0].new_page()
        got = asyncio.get_running_loop().create_future()
        page.on("response", lambda r: "batchedDataV2" in r.url and not got.done() and got.set_result(r.status))
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            status = await asyncio.wait_for(got, 60) if looker else (resp.status if resp else None)
        except Exception as e:
            status = f"none ({type(e).__name__})"
        ok = status == 200 and "accounts.google.com" not in page.url
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": ok,
               "data_status": status, "landed": page.url[:120]}
        await page.close()
        return rec


async def main(a):
    while True:
        rec = await check(a.cdp, a.url)
        print(json.dumps(rec), flush=True)
        with open(a.log, "a") as f:
            f.write(json.dumps(rec) + "\n")
        if not a.every:
            return
        await asyncio.sleep(a.every)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://127.0.0.1:9333")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--every", type=int, default=0)
    ap.add_argument("--log", default="out/session_health.jsonl")
    asyncio.run(main(ap.parse_args()))
