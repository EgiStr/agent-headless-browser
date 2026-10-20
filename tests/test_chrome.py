import socket
from pathlib import Path

import pytest

from agent_headless_browser import chrome
from agent_headless_browser.config import Settings


def test_user_agent_windows_hides_headless():
    ua = chrome.user_agent("155.0.8059.85", system="Windows")
    assert ua == (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36"
    )
    assert "Headless" not in ua


def test_user_agent_linux():
    assert "(X11; Linux x86_64)" in chrome.user_agent("155.0.8059.84", system="Linux", machine="x86_64")


def _settings(tmp_path: Path, **kw) -> Settings:
    return Settings(port=9999, profile_dir=tmp_path / "profile", **kw)


def test_build_args_headless(tmp_path):
    args = chrome.build_args("chrome", _settings(tmp_path), headful=False, url=None, ua="UA")
    assert args[0] == "chrome"
    assert f"--user-data-dir={tmp_path / 'profile'}" in args
    assert "--remote-debugging-port=9999" in args
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--disable-features=DeviceBoundSessions,EnableBoundSessionCredentials" in args
    assert "--headless=new" in args
    assert "--user-agent=UA" in args
    assert args[-1] == "about:blank"


def test_build_args_headful_opens_sign_in_and_keeps_real_ua(tmp_path):
    args = chrome.build_args("chrome", _settings(tmp_path), headful=True, url=None, ua="UA")
    assert "--headless=new" not in args
    assert not any(a.startswith("--user-agent") for a in args)
    assert args[-1] == chrome.LOGIN_URL


def test_build_args_extra_flags_and_url(tmp_path):
    settings = _settings(tmp_path, chrome_args=("--no-sandbox",))
    args = chrome.build_args("chrome", settings, headful=False, url="https://example.com", ua=None)
    assert "--no-sandbox" in args
    assert args[-1] == "https://example.com"


def test_read_mode(tmp_path):
    assert chrome.read_mode(tmp_path) is None
    (tmp_path / chrome.MODE_FILE).write_text("headless\n")
    assert chrome.read_mode(tmp_path) == "headless"


def test_cdp_version_none_when_nothing_listens():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    assert chrome.cdp_version(free_port) is None


def test_stop_is_noop_when_not_running(monkeypatch):
    monkeypatch.setattr(chrome, "cdp_version", lambda port: None)
    assert chrome.stop(1234) == "nothing listening on port 1234"


def test_start_refuses_when_port_taken(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "cdp_version", lambda port: {"Browser": "Chrome/1"})
    with pytest.raises(chrome.ChromeError, match="already owns port"):
        chrome.start(_settings(tmp_path))


def test_find_chrome_missing(monkeypatch):
    monkeypatch.setattr(chrome, "WINDOWS_CHROME", Path("/nonexistent/chrome.exe"))
    monkeypatch.setattr(chrome.shutil, "which", lambda name: None)
    with pytest.raises(chrome.ChromeError, match="not found"):
        chrome.find_chrome()
