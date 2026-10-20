"""Which Google accounts the agent profile is signed in to."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchright.async_api import BrowserContext

LIST_ACCOUNTS_URL = "https://accounts.google.com/ListAccounts?gpsia=1&source=ChromiumBrowser&json=standard"


def parse_list_accounts(body: str) -> list[str]:
    """Emails from a ``ListAccounts`` response: ``["gaia.l.a.r", [["gaia.l.a", 1, name, email, ...], ...]]``."""
    try:
        data = json.loads(body)
        return [row[3] for row in data[1] if len(row) > 3 and isinstance(row[3], str) and row[3]]
    except (ValueError, IndexError, TypeError):
        return []


async def signed_in_accounts(context: BrowserContext) -> list[str]:
    """Signed-in emails, using the profile's own cookies."""
    resp = await context.request.post(LIST_ACCOUNTS_URL)
    return parse_list_accounts(await resp.text()) if resp.ok else []
