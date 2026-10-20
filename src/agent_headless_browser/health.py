"""Is the Google session still good? One JSON record per check."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from patchright.async_api import Response, async_playwright

LOOKER_HOSTS = ("lookerstudio.google.com", "datastudio.google.com")


def is_looker(url: str) -> bool:
    return any(host in url for host in LOOKER_HOSTS)


def is_healthy(status: object, landed_url: str) -> bool:
    """200 and not bounced to the sign-in page."""
    return status == 200 and "accounts.google.com" not in landed_url


async def check(cdp_url: str, url: str) -> dict[str, Any]:
    """Load ``url`` in a fresh tab of the agent Chrome.

    A Looker Studio URL passes only if the report's data call (``batchedDataV2``)
    returns 200; any other URL passes if it loads without bouncing to sign-in.
    """
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        page = await browser.contexts[0].new_page()
        data_call: asyncio.Future[int] = asyncio.get_running_loop().create_future()

        def on_response(resp: Response) -> None:
            if "batchedDataV2" in resp.url and not data_call.done():
                data_call.set_result(resp.status)

        page.on("response", on_response)
        status: object
        try:
            nav = await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            status = (
                await asyncio.wait_for(data_call, 60) if is_looker(url) else (nav.status if nav else None)
            )
        except Exception as exc:
            status = f"none ({type(exc).__name__})"
        landed = page.url
        await page.close()
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": is_healthy(status, landed),
        "status": status,
        "landed": landed[:120],
    }


def append_log(record: dict[str, Any], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
