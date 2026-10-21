"""Start and stop the one long-lived Chrome that owns the agent profile.

Agents never launch their own browser; they attach to this one over CDP. Design
constraints, all learned the hard way (see docs/findings.md):

* DBSC is disabled so the Google session is not bound to this machine's TPM.
* Only one process may open the profile. Copying it, or reusing its cookies
  elsewhere, got the session revoked in testing.
* Stop gracefully (CDP ``Browser.close``) so rotated cookies reach disk.
* On Windows, MCP clients run servers in a kill-on-close Job Object; Chrome is
  created through WMI so it outlives the client.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings

LOGIN_URL = "https://accounts.google.com/ServiceLogin?continue=https://myaccount.google.com/"
MODE_FILE = "agent-mode"
CHROME_DOWNLOAD_URL = "https://www.google.com/chrome/"
# The same places scripts/install.py looks, so installer and runtime agree on where Chrome is.
WINDOWS_CHROME = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)
MACOS_CHROME = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
)
WINDOWS_CHROME_COMMANDS = ("chrome.exe",)
LINUX_CHROME = ("google-chrome", "google-chrome-stable")


class ChromeError(RuntimeError):
    """Chrome could not be found, started or stopped."""


def _first_existing(paths: tuple[Path, ...]) -> str | None:
    """First of ``paths`` that exists, with ``~`` expanded to the user's home.

    The home is not always ``/Users/me``: a macOS user may install Chrome in their own
    ``~/Applications``, and their home directory lives somewhere else.
    """
    for path in paths:
        candidate = path.expanduser()
        if candidate.exists():
            return str(candidate)
    return None


def _first_on_path(names: tuple[str, ...]) -> str | None:
    """First of ``names`` that resolves to an executable on PATH."""
    for name in names:
        if found := shutil.which(name):
            return found
    return None


def find_chrome() -> str:
    """Path of the installed Google Chrome (not Chromium: patchright and Google expect real Chrome)."""
    found: str | None
    if sys.platform == "win32":
        # the Program Files paths first, then PATH: an installer that put
        # Chrome somewhere else still put it on PATH
        found = _first_existing(WINDOWS_CHROME) or _first_on_path(WINDOWS_CHROME_COMMANDS)
        where = "Program Files and PATH"
    elif sys.platform == "darwin":
        found = _first_existing(MACOS_CHROME)
        where = "/Applications"
    elif sys.platform.startswith("linux"):
        found = _first_on_path(LINUX_CHROME)
        where = "PATH"
    else:
        raise ChromeError(f"Google Chrome is not supported on {sys.platform}")
    if found is None:
        raise ChromeError(f"Google Chrome not found (checked {where}); install it from {CHROME_DOWNLOAD_URL}")
    return found


def chrome_version(chrome: str) -> str | None:
    """Full version string such as ``155.0.8059.85``."""
    if sys.platform == "win32":
        # chrome.exe --version opens a window on Windows; read the versioned folder instead.
        versions = [d.name for d in Path(chrome).parent.iterdir() if re.fullmatch(r"\d+(\.\d+){3}", d.name)]
        return max(versions, key=lambda v: tuple(map(int, v.split("."))), default=None)
    out = subprocess.run([chrome, "--version"], capture_output=True, text=True, check=False).stdout
    match = re.search(r"\d+\.\d+\.\d+\.\d+", out)
    return match.group(0) if match else None


def user_agent(version: str, system: str | None = None, machine: str | None = None) -> str:
    """The UA headful Chrome sends.

    Headless Chrome says ``HeadlessChrome``, which Google's sign-in page may refuse.
    """
    system = system or platform.system()
    os_part = (
        "Windows NT 10.0; Win64; x64"
        if system == "Windows"
        else f"X11; Linux {machine or platform.machine()}"
    )
    major = version.split(".")[0]
    return (
        f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


def build_args(
    chrome: str,
    settings: Settings,
    *,
    headful: bool,
    url: str | None,
    ua: str | None,
) -> list[str]:
    """Full Chrome command line for the agent profile."""
    args = [
        chrome,
        f"--user-data-dir={settings.profile_dir}",
        f"--remote-debugging-port={settings.port}",
        "--remote-debugging-address=127.0.0.1",
        "--disable-features=DeviceBoundSessions,EnableBoundSessionCredentials",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1366,900",
        *settings.chrome_args,
    ]
    if not headful:
        args.append("--headless=new")
        if ua:
            args.append(f"--user-agent={ua}")
    args.append(url or (LOGIN_URL if headful else "about:blank"))
    return args


def cdp_version(port: int) -> dict[str, Any] | None:
    """``/json/version`` of the Chrome on ``port``, or None if nothing listens."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
            data: dict[str, Any] = json.loads(resp.read())
            return data
    except OSError:
        return None


def read_mode(profile_dir: Path) -> str | None:
    """``headless`` or ``headful`` as last started. The UA cannot tell, since it is replaced."""
    path = profile_dir / MODE_FILE
    return path.read_text().strip() if path.exists() else None


def _spawn_detached(args: list[str]) -> None:
    io: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform != "win32":
        subprocess.Popen(args, start_new_session=True, **io)
        return
    cmdline = subprocess.list2cmdline(args).replace("'", "''")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{CommandLine='{cmdline}'}}; exit $r.ReturnValue",
        ],
        check=True,
        **io,
    )


def start(
    settings: Settings,
    *,
    headful: bool = False,
    url: str | None = None,
    foreground: bool = False,
    timeout: float = 15.0,
) -> str:
    """Start the agent Chrome and wait until CDP answers.

    Args:
        foreground: Replace this process with Chrome (for systemd ``Type=simple``).
            Never returns in that case.
    """
    if cdp_version(settings.port):
        raise ChromeError(f"a Chrome already owns port {settings.port}; stop it first")
    chrome = find_chrome()
    version = chrome_version(chrome)
    args = build_args(chrome, settings, headful=headful, url=url, ua=user_agent(version) if version else None)
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    (settings.profile_dir / MODE_FILE).write_text("headful" if headful else "headless")
    if foreground:
        os.execv(chrome, args)
    _spawn_detached(args)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if info := cdp_version(settings.port):
            mode = "headful" if headful else "headless"
            return f"{info['Browser']} {mode} on {settings.cdp_url}, profile {settings.profile_dir}"
        time.sleep(0.3)
    raise ChromeError("Chrome did not open its CDP port in time")


def stop(port: int, timeout: float = 20.0) -> str:
    """Gracefully close the Chrome on ``port``.

    Playwright's ``browser.close()`` only disconnects from a CDP browser and leaves
    Chrome running; ``Browser.close`` actually shuts it down.
    """
    if not cdp_version(port):
        return f"nothing listening on port {port}"
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        with contextlib.suppress(Exception):  # the connection drops as Chrome exits
            browser.new_browser_cdp_session().send("Browser.close")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not cdp_version(port):
            time.sleep(1)  # let Chrome release the profile lock
            return "stopped"
        time.sleep(0.5)
    raise ChromeError("Chrome did not stop in time")
