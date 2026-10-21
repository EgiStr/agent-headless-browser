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


def _no_chrome_anywhere(monkeypatch, platform_name: str) -> None:
    """Make the given platform branch look for Chrome in places that do not exist."""
    monkeypatch.setattr(chrome.sys, "platform", platform_name)
    monkeypatch.setattr(chrome, "WINDOWS_CHROME", (Path("/nonexistent/chrome.exe"),))
    monkeypatch.setattr(chrome, "MACOS_CHROME", (Path("/nonexistent/Google Chrome"),))
    monkeypatch.setattr(chrome.shutil, "which", lambda name: None)


def test_windows_chrome_paths_match_the_installer():
    paths = {str(path) for path in chrome.WINDOWS_CHROME}
    assert r"C:\Program Files\Google\Chrome\Application\chrome.exe" in paths
    assert r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" in paths


def test_windows_chrome_also_looks_in_the_users_own_appdata(monkeypatch, tmp_path):
    """The consumer Chrome installer puts it there, not in Program Files."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    per_user = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    assert per_user in chrome._windows_chrome_paths()


def test_find_chrome_windows_uses_the_users_own_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    per_user = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    per_user.parent.mkdir(parents=True)
    per_user.touch()
    monkeypatch.setattr(chrome, "WINDOWS_CHROME", (tmp_path / "missing" / "chrome.exe", per_user))
    assert chrome.find_chrome() == str(per_user)


def test_linux_chrome_paths_match_the_installer():
    assert tuple(chrome.LINUX_CHROME_PATHS) == (
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/opt/google/chrome/chrome"),
    )


def test_user_agent_names_the_platform_it_runs_on():
    assert "Windows NT" in chrome.user_agent("155.0.0.0", system="Windows")
    assert "Macintosh" in chrome.user_agent("155.0.0.0", system="Darwin")
    assert "X11; Linux" in chrome.user_agent("155.0.0.0", system="Linux")


def test_macos_chrome_paths_match_the_installer():
    expected = (
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    )
    assert tuple(chrome.MACOS_CHROME) == expected


def test_find_chrome_windows_program_files(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    chrome_path = tmp_path / "Program Files" / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome_path.parent.mkdir(parents=True)
    chrome_path.touch()
    monkeypatch.setattr(chrome, "WINDOWS_CHROME", (chrome_path,))
    assert chrome.find_chrome() == str(chrome_path)


def test_find_chrome_windows_falls_back_to_x86(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    x86 = tmp_path / "Program Files (x86)" / "Google" / "Chrome" / "Application" / "chrome.exe"
    x86.parent.mkdir(parents=True)
    x86.touch()
    monkeypatch.setattr(chrome, "WINDOWS_CHROME", (tmp_path / "missing" / "chrome.exe", x86))
    assert chrome.find_chrome() == str(x86)


def test_find_chrome_windows_falls_back_to_the_path(monkeypatch):
    """Chrome installed outside Program Files is still on PATH."""
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "WINDOWS_CHROME", (Path("/nonexistent/chrome.exe"),))
    monkeypatch.setattr(
        chrome.shutil,
        "which",
        lambda name: r"C:\Tools\chrome.exe" if name == "chrome.exe" else None,
    )
    assert chrome.find_chrome() == r"C:\Tools\chrome.exe"


def test_find_chrome_macos_app(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome.sys, "platform", "darwin")
    chrome_path = tmp_path / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    chrome_path.parent.mkdir(parents=True)
    chrome_path.touch()
    monkeypatch.setattr(chrome, "MACOS_CHROME", (chrome_path,))
    assert chrome.find_chrome() == str(chrome_path)


def test_find_chrome_macos_expands_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome.sys, "platform", "darwin")
    home = tmp_path / "home"
    chrome_path = home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    chrome_path.parent.mkdir(parents=True)
    chrome_path.touch()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(
        chrome,
        "MACOS_CHROME",
        (Path("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),),
    )
    assert chrome.find_chrome() == str(chrome_path)


def test_find_chrome_linux_google_chrome(monkeypatch):
    monkeypatch.setattr(chrome.sys, "platform", "linux")
    monkeypatch.setattr(
        chrome.shutil,
        "which",
        lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None,
    )
    assert chrome.find_chrome() == "/usr/bin/google-chrome"


def test_find_chrome_linux_google_chrome_stable(monkeypatch):
    monkeypatch.setattr(chrome.sys, "platform", "linux")
    monkeypatch.setattr(
        chrome.shutil,
        "which",
        lambda name: "/usr/bin/google-chrome-stable" if name == "google-chrome-stable" else None,
    )
    assert chrome.find_chrome() == "/usr/bin/google-chrome-stable"


def test_find_chrome_never_returns_chromium(monkeypatch):
    monkeypatch.setattr(chrome.sys, "platform", "linux")
    monkeypatch.setattr(
        chrome.shutil,
        "which",
        lambda name: "/usr/bin/chromium" if "chromium" in name else None,
    )
    with pytest.raises(chrome.ChromeError, match="not found"):
        chrome.find_chrome()


@pytest.mark.parametrize("platform_name", ["win32", "darwin", "linux"])
def test_find_chrome_missing(monkeypatch, platform_name):
    _no_chrome_anywhere(monkeypatch, platform_name)
    with pytest.raises(chrome.ChromeError, match="not found"):
        chrome.find_chrome()


def test_find_chrome_missing_names_the_download_page(monkeypatch):
    _no_chrome_anywhere(monkeypatch, "darwin")
    with pytest.raises(chrome.ChromeError, match=r"google\.com/chrome"):
        chrome.find_chrome()
