"""One-click installer for agent-headless-browser.

Standard library only, so the shell and PowerShell wrappers can run it with any
Python 3.10+. It installs the package, registers the MCP server with the MCP
clients it finds on the machine, and verifies the result.

    python scripts/install.py              # install and configure
    python scripts/install.py --dry-run    # show what would happen
    python scripts/install.py --yes        # no prompts

The sign-in step is always manual: the installer prints the steps and stops.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "agent-headless-browser"
SERVER_NAME = "agent-browser"
DSH_ENTRY_ID = "mcp-agent-browser"
# The first and the last line of the block the installer writes. They are how
# a later run finds its own entry again, to update it.
DSH_ENTRY_COMMENT = "# agent-headless-browser - headless Chrome that stays signed in to your Google"
DSH_ENTRY_LAST_LINE = "failOnStartupError: false"
DEFAULT_SOURCE = "git+https://github.com/EgiStr/agent-headless-browser"
RAW_BASE = "https://raw.githubusercontent.com/EgiStr/agent-headless-browser/main/scripts"
MIN_PYTHON = (3, 10)
IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"

# Where the runtime looks for Chrome. The installer has to agree with it: a
# browser the runtime cannot launch is of no use to the user. Chromium is not
# in the list because the runtime needs real Chrome.
CHROME_PATHS = {
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
    "Linux": ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"],
}
CHROME_COMMANDS = {
    "Windows": ["chrome.exe"],
    "Darwin": [],
    "Linux": ["google-chrome", "google-chrome-stable"],
}


class InstallerError(RuntimeError):
    """Something the installer cannot do on its own."""


# --- console ---------------------------------------------------------------


class Console:
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def _print(self, prefix: str, message: str) -> None:
        if not self.quiet:
            print(f"{prefix} {message}", flush=True)

    def step(self, message: str) -> None:
        self._print("==>", message)

    def ok(self, message: str) -> None:
        self._print("  ok", message)

    def warn(self, message: str) -> None:
        self._print("  !!", message)

    def fail(self, message: str) -> None:
        self._print("  xx", message)

    def plain(self, message: str = "") -> None:
        if not self.quiet:
            print(message, flush=True)


# --- preflight -------------------------------------------------------------


def parse_version(text: str) -> tuple[int, ...]:
    """First dotted version in ``text``, e.g. 'Python 3.12.1' -> (3, 12, 1)."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part is not None)


def python_is_supported(version: tuple[int, ...]) -> bool:
    return version[:2] >= MIN_PYTHON


def find_chrome() -> Path | None:
    """The installed Google Chrome, or None."""
    system = platform.system()
    for raw in CHROME_PATHS.get(system, []):
        candidate = Path(raw).expanduser()
        if candidate.exists():
            return candidate
    for command in CHROME_COMMANDS.get(system, []):
        found = shutil.which(command)
        if found:
            return Path(found)
    return None


def candidate_bin_dirs() -> list[Path]:
    """Directories an installed console script may land in."""
    dirs: list[Path] = []
    home = Path.home()
    dirs.append(home / ".local" / "bin")
    uv = which("uv")
    if uv:
        result = run([uv, "tool", "dir", "--bin"], timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            dirs.append(Path(result.stdout.strip()))
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "WindowsApps")
        with contextlib.suppress(KeyError, ValueError):
            dirs.append(Path(sysconfig.get_path("scripts", scheme="nt_user")))
    else:
        dirs.append(home / ".local" / "pipx" / "bin")
    return [d for d in dirs if d.is_dir()]


def resolve_executable(name: str) -> Path | None:
    """Absolute path of an installed console script, or None."""
    found = shutil.which(name)
    if found:
        return Path(found)
    for directory in candidate_bin_dirs():
        for candidate in (directory / name, directory / f"{name}.exe"):
            if candidate.exists():
                return candidate
    return None


def run(
    command: list[str],
    timeout: int = 600,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        env=env,
    )


def which(name: str) -> str | None:
    found = shutil.which(name)
    return found


def cdp_is_up(port: int) -> bool:
    """Is something already serving CDP on that port?"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def process_command_lines() -> list[str]:
    """Command lines of the running Chrome processes, best effort."""
    if IS_WINDOWS:
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | ForEach-Object { $_.CommandLine }",
        ]
    else:
        command = ["ps", "-eo", "args="]
    try:
        result = run(command, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


USER_DATA_DIR_PATTERNS = (
    re.compile(r'"--user-data-dir=([^"]+)"'),
    re.compile(r'--user-data-dir="([^"]*)"'),
    re.compile(r"--user-data-dir='([^']*)'"),
    re.compile(r"--user-data-dir=(\S+)"),
)


def user_data_dir(command_line: str) -> str | None:
    """The profile directory in a Chrome command line.

    The directory may contain spaces, and the whole argument is then quoted, so
    splitting the line on whitespace would cut the path in half.
    """
    for pattern in USER_DATA_DIR_PATTERNS:
        match = pattern.search(command_line)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def running_agent_profile(port: int) -> Path | None:
    """The profile of a Chrome that already serves CDP on ``port``.

    An agent Chrome that is already running holds a live Google session. A second
    profile would mean signing in again, so the installer reuses the running one.
    """
    if not cdp_is_up(port):
        return None
    for line in process_command_lines():
        if f"--remote-debugging-port={port}" not in line:
            continue
        value = user_data_dir(line)
        if value:
            return Path(value)
    return None


def choose_profile_dir(explicit: str | None, port: int) -> Path:
    """Where the Google session should live."""
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get("AHB_PROFILE_DIR")
    if from_env:
        return Path(from_env).expanduser()
    running = running_agent_profile(port)
    if running:
        return running
    return Path.home() / ".agent-headless-browser" / "profile"


# --- installation ----------------------------------------------------------


@dataclass
class InstallResult:
    method: str
    executable: Path | None
    detail: str = ""


def install_with_uv(source: str, console: Console, dry_run: bool) -> InstallResult | None:
    uv = which("uv")
    if not uv:
        return None
    console.step(f"installing with uv from {source}")
    if dry_run:
        return InstallResult("uv", None, "dry run")
    result = run([uv, "tool", "install", "--force", source])
    if result.returncode != 0:
        console.warn(f"uv tool install failed: {(result.stderr or result.stdout).strip()[:200]}")
        return None
    return InstallResult("uv", resolve_executable("ahb-mcp"), "uv tool install")


def install_with_pipx(source: str, console: Console, dry_run: bool) -> InstallResult | None:
    pipx = which("pipx")
    if not pipx:
        return None
    console.step(f"installing with pipx from {source}")
    if dry_run:
        return InstallResult("pipx", None, "dry run")
    result = run([pipx, "install", "--force", source])
    if result.returncode != 0:
        console.warn(f"pipx install failed: {(result.stderr or result.stdout).strip()[:200]}")
        return None
    return InstallResult("pipx", resolve_executable("ahb-mcp"), "pipx install")


def install_with_pip(source: str, console: Console, dry_run: bool) -> InstallResult | None:
    console.step(f"installing with pip into the user site-packages from {source}")
    if dry_run:
        return InstallResult("pip", None, "dry run")
    command = [sys.executable, "-m", "pip", "install", "--user", "--upgrade", source]
    result = run(command)
    if result.returncode != 0:
        console.warn(f"pip install failed: {(result.stderr or result.stdout).strip()[:300]}")
        return None
    return InstallResult("pip", resolve_executable("ahb-mcp"), "pip install --user")


def install_package(source: str, console: Console, dry_run: bool) -> InstallResult:
    for attempt in (install_with_uv, install_with_pipx, install_with_pip):
        result = attempt(source, console, dry_run)
        if result is not None:
            return result
    raise InstallerError("could not install the package with uv, pipx or pip")


# --- MCP client configuration ---------------------------------------------


def yaml_scalar(value: object) -> str:
    """A YAML single-quoted scalar.

    A quote inside the value is doubled, which is how YAML escapes it. Without
    that, a profile directory with an apostrophe in its name produces a patch
    file that no longer parses.
    """
    return "'" + str(value).replace("'", "''") + "'"


def dsh_entry_block(
    executable: Path, profile_dir: Path, out_dir: Path, port: int = 9333, indent: int = 4
) -> str:
    """The cordis.patch.yml entry for this server, indented for the insert list."""
    pad = " " * indent
    return (
        f"{pad}{DSH_ENTRY_COMMENT}\n"
        f"{pad}# account. Added by scripts/install.py. Remove this block to uninstall.\n"
        f"{pad}- id: mcp-agent-browser\n"
        f"{pad}  name: '@deepseek-ai/dsh-mcp-client'\n"
        f"{pad}  config:\n"
        f"{pad}    serverName: agent-browser\n"
        f"{pad}    transport: stdio\n"
        f"{pad}    command: {yaml_scalar(executable)}\n"
        f"{pad}    env:\n"
        f"{pad}      AHB_PORT: {yaml_scalar(port)}\n"
        f"{pad}      AHB_PROFILE_DIR: {yaml_scalar(profile_dir)}\n"
        f"{pad}      AHB_OUT_DIR: {yaml_scalar(out_dir)}\n"
        f"{pad}    toolCallTimeoutMs: 300000\n"
        f"{pad}    {DSH_ENTRY_LAST_LINE}\n"
    )


def dsh_entry_present(text: str) -> bool:
    """Is the entry already in the file?

    A comment that mentions the id does not count, or the installer would
    decide the profile is configured when it is not.
    """
    needle = f"- id: {DSH_ENTRY_ID}"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == needle or stripped.startswith(needle + " "):
            return True
    return False


def find_dsh_entry_span(lines: list[str]) -> tuple[int, int] | None:
    """The line range of an entry this installer wrote, if it is there.

    The block starts with the comment the installer writes and ends with the
    last line of the entry, so both ends can be found again.
    """
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if start is None:
            if stripped.startswith(DSH_ENTRY_COMMENT):
                start = index
            continue
        if stripped.startswith(DSH_ENTRY_LAST_LINE):
            return (start, index + 1)
    return None


def count_dsh_entries(lines: list[str]) -> int:
    """How many times the entry appears in the file."""
    needle = f"- id: {DSH_ENTRY_ID}"
    return sum(1 for line in lines if line.strip().startswith(needle))


def dsh_structure(lines: list[str]) -> str:
    """'insert', 'rootmap' or 'toplevel'.

    A DSH patch file is normally a top-level YAML array. Some profiles keep the
    MCP servers in an ``insert:`` list inside the first element, some use a
    top-level mapping with an ``insert:`` key, and others leave the array
    empty. The three need different edits, and guessing wrong breaks the
    profile.
    """
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "- insert:":
            return "insert"
        if stripped == "insert:":
            return "rootmap"
        return "toplevel"
    return "toplevel"


def top_level_entries(lines: list[str]) -> int:
    """How many block sequence items the file has at column zero."""
    return sum(1 for line in lines if line.startswith("- "))


def find_dsh_insert_index(lines: list[str]) -> int:
    """Index right after the last item of the top-level insert list.

    The insert list is the indented part after ``- insert:``. The next
    top-level entry ends it, so the new entry goes after its last item.
    """
    start = next(
        (i for i, line in enumerate(lines) if line.strip() in {"insert:", "- insert:"}),
        None,
    )
    if start is None:
        return len(lines)
    last = start
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        if line[:1].isspace():
            last = index  # an item of the list, or a comment inside it
            continue
        if line.lstrip().startswith("#"):
            last = index  # a comment that still belongs to the list
            continue
        break  # a top-level entry ends the insert list
    return last + 1


def find_empty_array_index(lines: list[str]) -> int | None:
    """Index of an empty top-level flow array, e.g. a file that is just '[]'."""
    for index, line in enumerate(lines):
        if line.strip() == "[]":
            return index
    return None


def dsh_toplevel_block(executable: Path, profile_dir: Path, out_dir: Path, port: int = 9333) -> str:
    """A whole top-level patch element, for a profile with no insert list."""
    return "- insert:\n" + dsh_entry_block(executable, profile_dir, out_dir, port)


def dsh_patch_files() -> list[Path]:
    """cordis.patch.yml of every DSH profile on this machine."""
    roots: list[Path] = []
    home_env = os.environ.get("DSH_HOME")
    if home_env:
        roots.append(Path(home_env))
    roots.append(Path.home() / ".dsh")
    found: list[Path] = []
    for root in roots:
        profiles = root / "profiles"
        if not profiles.is_dir():
            continue
        for profile in sorted(profiles.iterdir()):
            patch = profile / "cordis.patch.yml"
            if patch.is_file():
                found.append(patch)
    return found


def configure_dsh(
    executable: Path,
    profile_dir: Path,
    out_dir: Path,
    console: Console,
    dry_run: bool,
    port: int = 9333,
) -> bool:
    patches = dsh_patch_files()
    if not patches:
        console.warn("no DSH profile found (looked for cordis.patch.yml under profiles/)")
        return False
    written = False
    for patch in patches:
        text = patch.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        structure = dsh_structure(lines)
        present = dsh_entry_present(text)
        # an entry this installer wrote earlier can be updated, so a re-run
        # picks up a new path, port or profile
        span = find_dsh_entry_span(lines) if present else None
        if present and span is None:
            console.ok(f"DSH profile already configured: {patch}")
            written = True
            continue
        if structure in {"insert", "rootmap"}:
            # a list under '- insert:' is indented by four, a list under a
            # top-level 'insert:' key by two
            indent = 4 if structure == "insert" else 2
            block = dsh_entry_block(executable, profile_dir, out_dir, port, indent).splitlines(keepends=True)
            if span is not None:
                start, end = span
                new_lines = [*lines[:start], *block, *lines[end:]]
            else:
                index = find_dsh_insert_index(lines)
                new_lines = [*lines[:index], *block, *lines[index:]]
            expected_growth = 0
        else:
            block = dsh_toplevel_block(executable, profile_dir, out_dir, port).splitlines(keepends=True)
            if span is not None:
                start, end = span
                new_lines = [*lines[:start], *block, *lines[end:]]
            else:
                empty = find_empty_array_index(lines)
                if empty is None:
                    if lines and not lines[-1].endswith("\n"):
                        lines[-1] += "\n"
                    new_lines = [*lines, *block]
                else:
                    # replace the empty array, never append after it: a flow
                    # sequence cannot be followed by a block sequence
                    new_lines = [*lines[:empty], *block, *lines[empty + 1 :]]
            expected_growth = 0 if span is not None else 1
        growth = top_level_entries(new_lines) - top_level_entries(lines)
        if growth != expected_growth:
            console.warn(f"skipping {patch}: its shape is not one this installer recognises")
            continue
        if count_dsh_entries(new_lines) != 1:
            console.warn(f"skipping {patch}: the result would not have exactly one entry")
            continue
        console.step(f"{'updating' if present else 'adding'} the MCP server in {patch}")
        if dry_run:
            written = True
            continue
        backup = patch.with_name(patch.name + ".install.bak")
        backup.write_text(text, encoding="utf-8")
        patch.write_text("".join(new_lines), encoding="utf-8")
        console.ok(f"wrote {patch} (the old one is kept as {backup.name})")
        written = True
    return written


def warn_on_duplicate_scopes(console: Console) -> None:
    """Claude Code keeps one server per scope; the same name in two scopes is a conflict."""
    claude = which("claude")
    if not claude:
        return
    result = run([claude, "mcp", "list"], timeout=90)
    text = result.stdout or ""
    if "defined in multiple scopes" in text:
        console.warn("the same server name is defined in more than one Claude Code scope")
        console.plain("       keep one of them:")
        console.plain("         claude mcp remove agent-browser -s user")
        console.plain("         claude mcp remove agent-browser -s project")


def configure_claude_code(
    executable: Path,
    profile_dir: Path,
    out_dir: Path,
    console: Console,
    dry_run: bool,
    port: int = 9333,
) -> bool:
    """Register the server in Claude Code.

    The environment has to go with it. Without it the server starts on the
    default port and the default profile, which is a different Chrome than the
    one this installer just set up.
    """
    claude = which("claude")
    if not claude:
        console.warn("no 'claude' CLI found; skipping Claude Code")
        return False
    env = [
        "-e",
        f"AHB_PORT={port}",
        f"AHB_PROFILE_DIR={profile_dir}",
        f"AHB_OUT_DIR={out_dir}",
    ]
    add = [claude, "mcp", "add", "-s", "user", SERVER_NAME, *env, "--", str(executable)]
    console.step("registering the MCP server in Claude Code (user scope)")
    if dry_run:
        console.ok(f"would run: claude mcp add -s user {SERVER_NAME} -e ... -- {executable}")
        return True
    result = run(add)
    if result.returncode != 0:
        combined = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
        if "already exists" in combined:
            # update it, so a reinstall with a new path takes effect
            console.step("the MCP server is already in Claude Code; updating it")
            removed = run([claude, "mcp", "remove", "-s", "user", SERVER_NAME])
            if removed.returncode == 0:
                result = run(add)
    if result.returncode != 0:
        console.warn(f"claude mcp add failed: {(result.stderr or result.stdout).strip()[:200]}")
        return False
    console.ok(f"Claude Code now has the '{SERVER_NAME}' MCP server")
    return True


def write_generic_snippet(
    executable: Path, profile_dir: Path, out_dir: Path, console: Console, port: int = 9333
) -> Path:
    """A config file for MCP clients this installer does not know.

    An existing file keeps the servers it already had: this one owns only its
    own entry. The old file is kept next to it.
    """
    target = Path.home() / ".agent-headless-browser" / "mcp.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, object] = {}
    if target.is_file():
        text = target.read_text(encoding="utf-8")
        try:
            existing = json.loads(text)
        except json.JSONDecodeError:
            existing = {}
        if isinstance(existing, dict) and isinstance(existing.get("mcpServers"), dict):
            servers = dict(existing["mcpServers"])
        backup = target.with_name(target.name + ".install.bak")
        backup.write_text(text, encoding="utf-8")
    servers[SERVER_NAME] = {
        "command": str(executable),
        "args": [],
        "env": {
            "AHB_PORT": str(port),
            "AHB_PROFILE_DIR": str(profile_dir),
            "AHB_OUT_DIR": str(out_dir),
        },
    }
    target.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8")
    console.ok(f"wrote a config for other MCP clients: {target}")
    return target


# --- verification ----------------------------------------------------------


@dataclass
class Status:
    running: bool
    accounts: list[str]
    headless: bool | None
    error: str | None = None

    @property
    def signed_in(self) -> bool:
        return bool(self.accounts)


def parse_status(text: str) -> Status:
    """Read the JSON that 'ahb status' prints."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return Status(running=False, accounts=[], headless=None)
    if not isinstance(data, dict):
        return Status(running=False, accounts=[], headless=None)
    accounts = data.get("google_accounts") or []
    return Status(
        running=bool(data.get("running")),
        accounts=[str(a) for a in accounts],
        headless=data.get("headless"),
    )


def verify(
    cli: Path | None,
    console: Console,
    dry_run: bool,
    profile_dir: Path | None = None,
    port: int = 9333,
) -> Status:
    """Check the install with the ahb CLI.

    This has to be the CLI, not ahb-mcp: ahb-mcp is the MCP server and it
    waits on stdin instead of printing the status.
    """
    console.step("verifying")
    if dry_run:
        return Status(running=False, accounts=[], headless=None)
    if cli is None:
        found = which("ahb")
        if not found:
            console.warn("could not find the ahb CLI; skipping the verification")
            return Status(running=False, accounts=[], headless=None)
        cli = Path(found)
    # the CLI reads these from its environment, and it has to look at the
    # Chrome this installer set up, not at some other one
    env = dict(os.environ)
    env["AHB_PORT"] = str(port)
    if profile_dir is not None:
        env["AHB_PROFILE_DIR"] = str(profile_dir)
    result = run([str(cli), "status"], env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:200]
        console.warn(f"'ahb status' failed: {detail}")
        return Status(running=False, accounts=[], headless=None, error=detail)
    status = parse_status(result.stdout)
    if not status.running:
        console.warn("the agent Chrome is not running yet (that is expected before sign-in)")
    elif not status.signed_in:
        console.warn("Chrome is running but no Google account is signed in")
    else:
        console.ok(f"Chrome is running and signed in as {', '.join(status.accounts)}")
    return status


# --- next steps ------------------------------------------------------------


def print_next_steps(console: Console, profile_dir: Path, out_dir: Path, has_display: bool) -> None:
    console.plain()
    console.plain("Next: sign in to Google (one time, you do it yourself)")
    console.plain("---------------------------------------------------")
    if has_display:
        console.plain("  ahb start --headful      # a Chrome window opens on the sign-in page")
        console.plain("  ahb stop                 # close it again, so the session is written to disk")
    else:
        console.plain("  ahb start --login        # headless Chrome on the sign-in page")
        console.plain("  ahb login-view            # leave running, then open http://127.0.0.1:9500")
        console.plain(
            "                            # on your own machine: ssh -N -L 9500:127.0.0.1:9500 user@server"
        )
    console.plain()
    console.plain("Then check it:")
    console.plain("  ahb status               # running: true, google_accounts: [you@...]")
    console.plain()
    console.plain(f"The Google session lives in {profile_dir}. Treat that folder like a")
    console.plain("password: never copy it to another machine, never move it. A copied")
    console.plain("profile makes Google revoke the session.")
    console.plain()
    console.plain(f"Exports and logs go to {out_dir}.")
    default_profile = Path.home() / ".agent-headless-browser" / "profile"
    if profile_dir != default_profile:
        console.plain()
        console.plain("That is not the default profile directory, so tell the CLI where it is:")
        console.plain(f'  Windows:       setx AHB_PROFILE_DIR "{profile_dir}"')
        console.plain(f"  macOS/Linux:   export AHB_PROFILE_DIR='{profile_dir}'")
    console.plain()
    console.plain("Your MCP client needs a restart to pick up the new server.")
    console.plain("The tools it gets: browser_status, browser_start, browser_stop, login_begin,")
    console.plain("login_finish, navigate, page_snapshot, click, type_text, press_key,")
    console.plain("screenshot, evaluate, looker_export, health_check.")


# --- main ------------------------------------------------------------------


def port_number(text: str) -> int:
    """A TCP port number, for argparse."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a port number") from None
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("the port has to be between 1 and 65535")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install.py", description="One-click installer for agent-headless-browser."
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help="where to install from (default: the GitHub repository)",
    )
    parser.add_argument(
        "--profile-dir",
        default=None,
        help="where the Google session lives (default: ~/.agent-headless-browser/profile)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="where exports and logs go (default: ~/.agent-headless-browser/out)",
    )
    parser.add_argument(
        "--port",
        type=port_number,
        default=9333,
        help="the CDP port the agent Chrome uses (default: 9333)",
    )
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--dry-run", action="store_true", help="only show what would happen")
    parser.add_argument("--skip-mcp", action="store_true", help="install without configuring MCP clients")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    profile_dir = choose_profile_dir(args.profile_dir, args.port)
    # the configs are read by an MCP client that starts the server somewhere
    # else, so a relative path would put the exports in the wrong place
    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
    else:
        out_dir = Path.home() / ".agent-headless-browser" / "out"

    console.plain(f"agent-headless-browser installer ({platform.system()} {platform.machine()})")
    console.plain()

    console.step("checking prerequisites")
    version = parse_version(platform.python_version())
    if not python_is_supported(version):
        console.fail(f"Python {'.'.join(map(str, version))} is too old, need 3.10 or newer")
        return 1
    console.ok(f"Python {'.'.join(map(str, version))}")
    chrome = find_chrome()
    if chrome:
        console.ok(f"Google Chrome at {chrome}")
    else:
        console.warn("Google Chrome was not found; install it, the tool needs real Chrome")
    if cdp_is_up(args.port):
        console.ok(f"an agent Chrome is already serving CDP on port {args.port}")
    else:
        console.ok(f"port {args.port} is free")

    if not args.dry_run and not args.yes and sys.stdin.isatty():
        try:
            answer = input("Install and configure now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("", "y", "yes"):
            console.plain("Nothing was changed.")
            return 0

    try:
        result = install_package(args.source, console, args.dry_run)
    except InstallerError as exc:
        console.fail(str(exc))
        return 1
    if args.dry_run:
        console.ok(f"would install with {result.method} ({result.detail})")
    else:
        console.ok(f"installed with {result.method} ({result.detail})")

    executable = result.executable
    if executable is None and not args.dry_run:
        executable = resolve_executable("ahb-mcp")
    if executable is None:
        if args.dry_run:
            # a stand-in, so the dry run still shows what would be configured
            executable = Path("ahb-mcp")
        else:
            console.warn("could not find the ahb-mcp entry point; the MCP clients will not be configured")
    else:
        console.ok(f"MCP entry point: {executable}")

    if not args.skip_mcp and executable is not None:
        configure_claude_code(executable, profile_dir, out_dir, console, args.dry_run, args.port)
        configure_dsh(executable, profile_dir, out_dir, console, args.dry_run, args.port)
        if not args.dry_run:
            write_generic_snippet(executable, profile_dir, out_dir, console, args.port)
            warn_on_duplicate_scopes(console)

    if not args.dry_run:
        profile_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)

    cli = resolve_executable("ahb") if not args.dry_run else None
    verify(cli, console, args.dry_run, profile_dir, args.port)

    has_display = IS_WINDOWS or IS_MACOS or bool(os.environ.get("DISPLAY"))
    print_next_steps(console, profile_dir, out_dir, has_display)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InstallerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
