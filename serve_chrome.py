"""Run one long-lived Chrome that owns the agent profile; agents attach over CDP.

  python serve_chrome.py --headful   # first time: sign in by hand, then `--stop`
  python serve_chrome.py             # headless, keeps running
  python serve_chrome.py --stop      # graceful close (flushes cookies to disk)
  python serve_chrome.py --login     # headless sign-in for CLI-only servers (see login_view.py)

Env: AGENT_CHROME_ARGS (extra Chrome flags, e.g. --no-sandbox inside a container).
CDP always binds 127.0.0.1 (Chrome 155 ignores other addresses); reach it over SSH.

Only this process may ever open the profile dir. Copying it or reusing its
cookies elsewhere is what got the session revoked in testing.
"""
import argparse
import json
import os
import pathlib
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

from patchright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent
CHROME_CANDIDATES = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                     "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]
LOGIN_URL = "https://accounts.google.com/ServiceLogin?continue=https://myaccount.google.com/"
EXTRA_ARGS = shlex.split(os.environ.get("AGENT_CHROME_ARGS", ""))


def chrome_path():
    return next((c for c in CHROME_CANDIDATES if pathlib.Path(c).exists()), None) or shutil.which("google-chrome")


def user_agent(chrome: str) -> str | None:
    """The UA headful Chrome would send. Headless says "HeadlessChrome", which
    Google's sign-in page may refuse."""
    if sys.platform == "win32":
        dirs = [d.name for d in pathlib.Path(chrome).parent.iterdir() if re.fullmatch(r"\d+(\.\d+){3}", d.name)]
        ver = max(dirs, key=lambda v: tuple(map(int, v.split("."))), default=None)
        os_part = "Windows NT 10.0; Win64; x64"
    else:
        out = subprocess.run([chrome, "--version"], capture_output=True, text=True).stdout
        ver = (re.search(r"(\d+)\.\d+\.\d+\.\d+", out) or [None])[0]
        os_part = f"X11; Linux {platform.machine()}"
    if not ver:
        return None
    return (f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{ver.split('.')[0]}.0.0.0 Safari/537.36")


def _profile_dir(profile: str) -> pathlib.Path:
    return pathlib.Path(profile) if pathlib.Path(profile).is_absolute() else (ROOT / profile).resolve()


def mode(profile: str) -> str | None:
    """"headless" or "headful" as last started; the UA can't tell since we replace it."""
    f = _profile_dir(profile) / "agent-mode"
    return f.read_text().strip() if f.exists() else None


def version(port: int) -> dict | None:
    try:
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read())
    except OSError:
        return None


def _spawn_detached(args: list[str]):
    """Start Chrome so it outlives the caller.

    MCP clients on Windows put the server in a KILL_ON_JOB_CLOSE Job Object,
    which would hard-kill a child Chrome when the client exits (breakaway did
    not help in testing). WMI creates the process outside our tree and job."""
    io = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform != "win32":
        subprocess.Popen(args, start_new_session=True, **io)
        return
    cmdline = subprocess.list2cmdline(args).replace("'", "''")
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                    f"-Arguments @{{CommandLine='{cmdline}'}}; exit $r.ReturnValue"], check=True, **io)


def start_chrome(profile: str = "profiles/agent", port: int = 9333, headful: bool = False,
                 url: str | None = None) -> str:
    if version(port):
        raise RuntimeError(f"a Chrome already owns port {port}; stop it first")
    profile_dir = _profile_dir(profile)
    chrome = chrome_path()
    args = [chrome, f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
            "--disable-features=DeviceBoundSessions,EnableBoundSessionCredentials",
            "--no-first-run", "--no-default-browser-check", "--window-size=1366,900"]
    args += EXTRA_ARGS
    if not headful:
        args.append("--headless=new")
        if ua := user_agent(chrome):
            args.append(f"--user-agent={ua}")
    args.append(url or (LOGIN_URL if headful else "about:blank"))
    _spawn_detached(args)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "agent-mode").write_text("headful" if headful else "headless")
    for _ in range(50):
        if v := version(port):
            return f"{v['Browser']} {'headful' if headful else 'headless'} on http://127.0.0.1:{port}, profile {profile_dir}"
        time.sleep(0.3)
    raise RuntimeError("chrome did not come up")


def stop_chrome(port: int = 9333) -> str:
    if not version(port):
        return f"nothing listening on port {port}"
    # Playwright's close() only disconnects from a CDP browser; Browser.close
    # actually shuts Chrome down cleanly.
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            browser.new_browser_cdp_session().send("Browser.close")
        except Exception:
            pass  # connection drops as Chrome exits
    for _ in range(40):
        if not version(port):
            time.sleep(1)  # let Chrome release the profile lock
            return "stopped"
        time.sleep(0.5)
    raise RuntimeError("chrome did not stop")


LOGIN_HELP = """Sign in without a display:
  1. On the server: python login_view.py --cdp http://127.0.0.1:{port}
  2. On your PC:    ssh -N -L 9500:127.0.0.1:9500 user@your-vps
  3. Open http://127.0.0.1:9500, click the page and type; the header shows "signed in" when done
  4. Then: python serve_chrome.py --stop && python serve_chrome.py"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="profiles/agent")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--login", action="store_true", help="headless, opened on the Google sign-in page")
    a = ap.parse_args()
    try:
        if a.stop:
            print(stop_chrome(a.port))
        else:
            print(start_chrome(a.profile, a.port, a.headful, LOGIN_URL if a.login else None))
            if a.login:
                print(LOGIN_HELP.format(port=a.port))
    except RuntimeError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
