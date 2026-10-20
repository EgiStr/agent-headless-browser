import json
from pathlib import Path

import pytest

from agent_headless_browser import health
from agent_headless_browser.config import Settings
from agent_headless_browser.google import parse_list_accounts


def test_settings_from_env(tmp_path):
    env = {
        "AHB_PORT": "9444",
        "AHB_PROFILE_DIR": str(tmp_path / "p"),
        "AHB_OUT_DIR": str(tmp_path / "o"),
        "AHB_CHROME_ARGS": "--no-sandbox --lang=en",
        "AHB_HEALTH_URL": "https://example.com/",
    }
    s = Settings.from_env(env)
    assert s.port == 9444
    assert s.profile_dir == (tmp_path / "p").resolve()
    assert s.out_dir == tmp_path / "o"
    assert s.chrome_args == ("--no-sandbox", "--lang=en")
    assert s.health_url == "https://example.com/"
    assert s.cdp_url == "http://127.0.0.1:9444"


def test_settings_defaults():
    s = Settings.from_env({})
    assert s.port == 9333
    assert s.chrome_args == ()


def test_parse_list_accounts():
    body = json.dumps(
        [
            "gaia.l.a.r",
            [
                ["gaia.l.a", 1, "Ada", "ada@example.com", "photo", 1],
                ["gaia.l.a", 1, "Bob", "bob@example.org"],
            ],
        ]
    )
    assert parse_list_accounts(body) == ["ada@example.com", "bob@example.org"]


@pytest.mark.parametrize("body", ["", "<html>error</html>", "[]", '["x"]', '["x", [["short"]]]'])
def test_parse_list_accounts_bad_input(body):
    assert parse_list_accounts(body) == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://lookerstudio.google.com/reporting/abc", True),
        ("https://datastudio.google.com/u/0/reporting/abc", True),
        ("https://myaccount.google.com/", False),
    ],
)
def test_is_looker(url, expected):
    assert health.is_looker(url) is expected


@pytest.mark.parametrize(
    ("status", "landed", "ok"),
    [
        (200, "https://myaccount.google.com/", True),
        (200, "https://accounts.google.com/v3/signin/accountchooser?x", False),
        (403, "https://myaccount.google.com/", False),
        ("none (TimeoutError)", "about:blank", False),
    ],
)
def test_is_healthy(status, landed, ok):
    assert health.is_healthy(status, landed) is ok


def test_append_log(tmp_path: Path):
    log = tmp_path / "nested" / "h.jsonl"
    health.append_log({"ok": True}, log)
    health.append_log({"ok": False}, log)
    assert [json.loads(line)["ok"] for line in log.read_text().splitlines()] == [True, False]
