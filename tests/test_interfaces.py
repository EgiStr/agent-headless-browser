"""CLI parsing and the MCP tool surface, without a browser."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_headless_browser import chrome, cli
from agent_headless_browser import mcp_server as server

EXPECTED_TOOLS = {
    "browser_status",
    "browser_start",
    "browser_stop",
    "login_begin",
    "login_finish",
    "navigate",
    "page_snapshot",
    "click",
    "type_text",
    "press_key",
    "screenshot",
    "evaluate",
    "looker_export",
    "health_check",
}


def test_cli_looker_export_requires_url():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["looker-export"])


def test_cli_start_modes_are_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["start", "--headful", "--login"])


def test_cli_parses_health():
    args = cli.build_parser().parse_args(["health", "--url", "https://x", "--every", "60"])
    assert (args.url, args.every) == ("https://x", 60)


def test_cli_status_when_not_running(monkeypatch, capsys):
    monkeypatch.setattr(chrome, "cdp_version", lambda port: None)
    assert cli.main(["status"]) == 0
    assert '"running": false' in capsys.readouterr().out


def test_cli_reports_chrome_errors(monkeypatch, capsys):
    def boom(*a, **kw):
        raise chrome.ChromeError("nope")

    monkeypatch.setattr(chrome, "start", boom)
    assert cli.main(["start"]) == 1
    assert "error: nope" in capsys.readouterr().err


async def test_mcp_exposes_all_tools():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert set(tools) == EXPECTED_TOOLS
    schema = tools["type_text"].input_schema
    assert set(schema["properties"]) == {"text", "ref", "selector", "submit"}
    assert schema["required"] == ["text"]


async def test_mcp_status_when_not_running(monkeypatch):
    monkeypatch.setattr(chrome, "cdp_version", lambda port: None)
    status = await server.browser_status()
    assert status["running"] is False


async def test_mcp_errors_are_readable(monkeypatch):
    monkeypatch.setattr(chrome, "cdp_version", lambda port: None)
    await server.session.disconnect()
    with pytest.raises(ToolError, match="ChromeError: agent Chrome is not running"):
        await server.navigate("https://example.com")
