"""The example MCP config stays in sync with the settings the server actually reads.

A variable name that the server does not know is ignored without a warning, and
the server falls back to its defaults. A hand-written config with a stale name
therefore looks fine and quietly talks to the wrong profile. These tests fail
when the example and `config.py` drift apart.
"""

import inspect
import json
import re
from pathlib import Path

from agent_headless_browser.config import Settings

EXAMPLE = Path(__file__).resolve().parent.parent / "mcp.example.json"
ENV_LOOKUP = re.compile(r'env\.get\("([A-Z_]+)"')


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _env() -> dict:
    return _example()["mcpServers"]["agent-browser"]["env"]


def test_example_is_a_valid_client_config():
    config = _example()
    server = config["mcpServers"]["agent-browser"]
    assert server["command"]
    assert isinstance(server["args"], list)
    assert isinstance(server["env"], dict)


def test_example_only_uses_variable_names_the_server_reads():
    read_by_server = set(ENV_LOOKUP.findall(inspect.getsource(Settings.from_env)))
    assert read_by_server
    assert set(_env()) <= read_by_server


def test_example_values_match_the_server_defaults():
    defaults = Settings()
    env = _env()
    assert int(env["AHB_PORT"]) == defaults.port
    assert Path(env["AHB_PROFILE_DIR"]).expanduser() == defaults.profile_dir
    assert Path(env["AHB_OUT_DIR"]) == defaults.out_dir
