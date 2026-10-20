"""A lazily (re)connected CDP session to the agent Chrome, with one working tab."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from patchright.async_api import async_playwright

from . import chrome

if TYPE_CHECKING:
    from patchright.async_api import Browser, BrowserContext, Page, Playwright

    from .config import Settings


class AgentSession:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    async def context(self) -> BrowserContext:
        if self._browser is None or not self._browser.is_connected():
            if not chrome.cdp_version(self.settings.port):
                raise chrome.ChromeError(
                    f"agent Chrome is not running on {self.settings.cdp_url}; start it first"
                )
            self._pw = self._pw or await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(self.settings.cdp_url)
            self._page = None
        return self._browser.contexts[0]

    async def page(self) -> Page:
        """The agent's working tab, reopened if it was closed."""
        ctx = await self.context()
        if self._page is None or self._page.is_closed():
            self._page = await ctx.new_page()
        return self._page

    async def disconnect(self) -> None:
        """Drop the CDP connection; Chrome keeps running."""
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
        self._browser = self._page = None

    async def aclose(self) -> None:
        """Disconnect and stop the Playwright driver."""
        await self.disconnect()
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
