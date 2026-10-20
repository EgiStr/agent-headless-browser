"""Real Chrome, no network, no Google account: launcher, CDP attach, snapshot refs, graceful stop.

Run with ``AHB_E2E=1 pytest -m integration``.
"""

import asyncio
import os
import socket
from urllib.parse import quote

import pytest

from agent_headless_browser import chrome
from agent_headless_browser.config import Settings
from agent_headless_browser.session import AgentSession

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("AHB_E2E") != "1", reason="set AHB_E2E=1 to run against real Chrome"),
]

PAGE = "data:text/html," + quote(
    "<title>e2e</title><input id=q placeholder=Search><button onclick=\"document.title='clicked:'+q.value\">Go</button>"
)


@pytest.fixture
def settings(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    extra = tuple(os.environ.get("AHB_CHROME_ARGS", "").split())
    cfg = Settings(port=port, profile_dir=tmp_path / "profile", chrome_args=extra)
    yield cfg
    chrome.stop(cfg.port)


async def test_start_attach_snapshot_click_stop(settings, monkeypatch):
    from agent_headless_browser import mcp_server

    banner = chrome.start(settings)
    assert "headless" in banner
    assert chrome.read_mode(settings.profile_dir) == "headless"

    monkeypatch.setattr(mcp_server, "settings", settings)
    monkeypatch.setattr(mcp_server, "session", AgentSession(settings))
    page = await mcp_server.session.page()
    assert "HeadlessChrome" not in await page.evaluate("navigator.userAgent")
    assert await page.evaluate("navigator.webdriver") is False

    await page.goto(PAGE)
    snap = await mcp_server.page_snapshot()
    refs = {line.split("]")[0][1:]: line for line in snap["elements"]}
    input_ref = next(int(r) for r, line in refs.items() if "<input" in line)
    button_ref = next(int(r) for r, line in refs.items() if "<button" in line)

    await mcp_server.type_text("hello", ref=input_ref)
    result = await mcp_server.click(ref=button_ref)
    assert result["title"] == "clicked:hello"

    await mcp_server.session.aclose()
    assert await asyncio.to_thread(chrome.stop, settings.port) == "stopped"
    assert chrome.cdp_version(settings.port) is None
