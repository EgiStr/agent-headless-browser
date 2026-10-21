# Architecture

Two long-lived pieces and one file that must never be copied.

```
  MCP client (Claude Code, DSH, Hermes, Codex, …)
        │  stdio, one JSON-RPC per tool call
  ahb-mcp ──────────────── src/agent_headless_browser/mcp_server.py
        │  CDP over http://127.0.0.1:9333
  Chrome (one process, headless)  ──  src/agent_headless_browser/chrome.py
        │
  AHB_PROFILE_DIR  ← the Google session lives here
```

The agent never launches a browser of its own. It attaches to the one Chrome that owns the profile.

## The three rules the design is built on

1. **One Chrome owns the profile, for its whole life.** A second Chrome on the same profile corrupts it, and Chrome's
   singleton hands the new command line to the old process instead of starting one.
2. **The profile is never copied and its cookies are never exported.** Since Chrome 146 a Google session can be bound to
   the device (DBSC, a TPM-held key). A transplanted cookie lands on the account chooser instead of the report. In testing,
   both copying the folder and injecting the cookies revoked the session.
3. **Chrome is closed gracefully.** `Browser.close` over CDP lets rotated cookies reach the profile directory. A hard kill
   survived once, but that is not a guarantee.

The experiments behind these rules are in [findings.md](findings.md).

## The pieces

| Piece | File | What it owns |
|---|---|---|
| Chrome launcher | `chrome.py` | Finding the real Chrome (never Chromium), the CDP port, the launch flags, graceful stop |
| MCP server | `mcp_server.py` | 14 tools, one shared page per session, errors wrapped as `ToolError` so the model sees the cause |
| Page session | `session.py` | Lazily opens a page and reuses it; reconnects when Chrome restarts |
| Looker export | `looker.py` | Captures one real `batchedDataV2` call and replays it with all dimensions + Record Count |
| Google account check | `google.py` | Reads `accounts.google.com/ListAccounts` to see which account the profile is signed in to |
| Health check | `health.py` | Loads a page that needs the session and appends the result to `session_health.jsonl` |
| Login view | `login_view.py` | Streams the headless tab to a browser over HTTP so a human can sign in on a server with no display |
| CLI | `cli.py` | `ahb start/stop/status/health/login-view/looker-export/mcp` |
| Settings | `config.py` | Reads `AHB_*` once, at startup |
| Installer | `scripts/install.py` | Finds Chrome, installs the package, registers the MCP server with the clients on the machine |

## Files on disk

| Path | Meaning |
|---|---|
| `$AHB_PROFILE_DIR` (default `~/.agent-headless-browser/profile`) | The Chrome profile with the Google session. **A credential.** Back it up like a password; never copy it to run elsewhere. |
| `$AHB_OUT_DIR` (default `out`) | Exports and `session_health.jsonl`. Safe to delete. |
| `~/.agent-headless-browser/mcp.json` | A generic MCP client config the installer writes. Other clients can be pointed at it. |
| `<profile>/agent-mode` | `headless` or `headful`. Written at launch; read back by `browser_status`. |
| `127.0.0.1:9333` | The CDP port. Always loopback: Chrome ignores `--remote-debugging-address=0.0.0.0` since 155. |

## Chrome flags that matter

| Flag | Why |
|---|---|
| `--user-data-dir=$AHB_PROFILE_DIR` | Chrome 136+ refuses `--remote-debugging-port` on the default profile. |
| `--disable-features=DeviceBoundSessions,EnableBoundSessionCredentials` | Unbinds the Google session so it survives in the agent profile. |
| `--headless=new` (or headful) | Set from `browser_start(headful=...)`. |
| `--window-size=1366,900` | The default headless window is 800x600 and sites render their mobile layout. |
| `--remote-debugging-port=9333` | Where agents attach. Loopback only. |
| `--no-sandbox` (opt-in via `AHB_CHROME_ARGS`) | Needed inside a container as root; not on a normal VPS user. |

The user agent is replaced with the headful one of the platform Chrome actually runs on, so a macOS Chrome does not claim
to be Linux.

## The MCP server

One `AgentSession` holds a Patchright context attached over CDP and one page. The first tool call pays the connection cost;
the rest reuse it. If Chrome was restarted underneath, the session reconnects on the next call.

Failures are wrapped: mcp 2.x hides unexpected exception text from the model, so every tool catches `Exception` and re-raises
it as `ToolError(f"{type(exc).__name__}: {exc}")`. That is why a tool error reads like
`ChromeError: Google Chrome not found (checked …)` instead of being swallowed.

The server's own `instructions` (the text an MCP client shows the model) carry the three rules: check `browser_status`
first, never type credentials, never copy the profile.

## The installer

`scripts/install.py` is the only part with a `#!/usr/bin/env python3` shebang and no third-party imports, so the one-line
bootstrap works before anything is installed. It:

1. checks that Python 3.10+ and **real Chrome** are present (`chromium` is rejected: Google and Patchright expect Chrome);
2. installs the package with `uv`, falling back to `pipx`, then `pip`;
3. registers the MCP server with the DSH profiles and the Claude Code configs it finds;
4. prints the sign-in step — it never signs in for you.

Every file it rewrites is backed up first (`*.install.bak`, then `.bak.1`, `.bak.2`, …), and a config it cannot parse is
left alone and written beside as `*.new` rather than overwritten.

## Environment variables

| Variable | Default | Read by |
|---|---|---|
| `AHB_PORT` | `9333` | CDP port |
| `AHB_PROFILE_DIR` | `~/.agent-headless-browser/profile` | The profile |
| `AHB_OUT_DIR` | `out` | Exports and the health log |
| `AHB_CHROME_ARGS` | | Extra Chrome flags, shell-quoted |
| `AHB_HEALTH_URL` | `https://myaccount.google.com/` | Page used by `health_check` / `ahb health` |

They are read once, when the server or CLI starts. Changing them needs a restart of that process.

## Where to add what

| Want to… | Touch |
|---|---|
| Add a tool | `mcp_server.py` (add `@tool` and a docstring), then a row in `docs/mcp-tools.md` and the skill's table |
| Change how Chrome is found | `chrome.py` **and** `scripts/install.py` — they must agree, and a test asserts they do |
| Change the export | `looker.py`; the request body it replays is Google's undocumented API, so read [findings.md](findings.md) first |
| Add a CLI command | `cli.py` (subparser plus `_cmd_*`) |
| Register with a new client | `scripts/install.py` (`configure_*`), and add the config shape to `tests/test_install.py` |
