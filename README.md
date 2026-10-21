# agent-headless-browser

[![CI](https://github.com/EgiStr/agent-headless-browser/actions/workflows/ci.yml/badge.svg)](https://github.com/EgiStr/agent-headless-browser/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A headless Chrome for AI agents that stays signed in to **your own** Google account and runs 24/7 on a laptop or
a CLI-only Linux server. It is exposed as an [MCP](https://modelcontextprotocol.io) server and ships with a Claude Code skill.
One of its tools exports every row behind a Looker Studio report to CSV.

```
 AI agent (Claude Code or any MCP client)
        │ stdio
   ahb-mcp ──── 14 tools ────┐
        │ CDP, 127.0.0.1 only │
   ahb start ─► one long-lived Chrome ─► profile dir (your Google session)
                     ▲
   ahb login-view ───┘ live click-and-type view for the one-time sign-in; no X server needed
```

## Why it works this way

The [research log](docs/findings.md) has the details. In short:

- **Copied profiles and exported cookies no longer work for Google.** Since Chrome 146, Google sessions are
  bound to the device (DBSC, a TPM-held key). Transplanted cookies land on the account chooser, headless or headful.
- **The fix is to sign in once inside a dedicated agent profile with DBSC disabled.** The agent then reuses that
  profile, headless.
- **One Chrome process owns the profile for its whole life, and agents attach over CDP.** In testing, copying the
  profile or reusing its cookies elsewhere got the session revoked.
- **A server without a display can complete the sign-in.** `ahb login-view` streams the headless tab to your
  browser through an SSH tunnel. This was tested in a display-less Debian container: sign-in, restart, and a 212k-row export.

## Install

Needs Google Chrome (not Chromium) and Python 3.10+.

```bash
pipx install git+https://github.com/EgiStr/agent-headless-browser
# or, from a clone:  pip install -e ".[dev]"
```

## Usage

### 1. Sign in once

```bash
ahb start --headful        # desktop: sign in in the window that opens
ahb stop
```

On a CLI-only server:

```bash
ahb start --login          # headless Chrome on the Google sign-in page
ahb login-view             # live view on 127.0.0.1:9500
# on your PC:  ssh -N -L 9500:127.0.0.1:9500 user@server   then open http://127.0.0.1:9500
ahb stop
```

### 2. Run headless

```bash
ahb start                  # CDP on 127.0.0.1:9333
ahb status                 # {"running": true, "headless": true, "google_accounts": ["you@..."]}
ahb health --every 1800    # exits 1 and logs "ok": false if the session dies
```

### 3. Use it

```bash
ahb looker-export https://lookerstudio.google.com/reporting/<report-id> --out exports/
```

To run it as a service, see [docs/vps.md](docs/vps.md). It covers systemd units and sizing.

## MCP server

```bash
claude mcp add -s user agent-browser -- ahb-mcp
```

Clients that read a JSON config can copy [`mcp.example.json`](mcp.example.json) and adjust the paths. The variable
names in it are the ones the server actually reads: a name the server does not know is ignored without a warning, so
a hand-written config with a stale name quietly falls back to the defaults.

`ahb-mcp` has to be on the client's `PATH`. A client that spawns the server with an environment of its own, instead of
inheriting the parent's, may not find it on Windows: point `command` at the absolute path of `ahb-mcp.exe`, or at
`python` with the args `-m agent_headless_browser.mcp_server`.

| Tool | What it does |
|---|---|
| `browser_status` | Whether Chrome is running and headless, and which Google accounts are signed in |
| `browser_start` / `browser_stop` | Start or stop Chrome; stop is graceful, so cookies reach disk |
| `login_begin` / `login_finish` | Re-login: opens a window on desktops, or gives `login-view` steps on servers; finish verifies the account and goes back to headless |
| `navigate`, `page_snapshot`, `click`, `type_text`, `press_key`, `screenshot`, `evaluate` | General browsing; `page_snapshot` returns numbered refs for `click` and `type_text` |
| `looker_export` | Every row of each datasource behind a Looker Studio report, written to CSV |
| `health_check` | Checks the session still works and appends to `session_health.jsonl` |

The skill in [`.claude/skills/agent-browser`](.claude/skills/agent-browser/SKILL.md) tells agents to check status first,
never to type credentials, and never to copy the profile.

### Configuration

| Variable | Default | |
|---|---|---|
| `AHB_PORT` | `9333` | CDP port; Chrome always binds 127.0.0.1 |
| `AHB_PROFILE_DIR` | `~/.agent-headless-browser/profile` | The live login. Back it up like a password, never copy it to run elsewhere |
| `AHB_OUT_DIR` | `out` | Exports and the health log |
| `AHB_CHROME_ARGS` | | Extra Chrome flags, e.g. `--no-sandbox` inside a container |
| `AHB_HEALTH_URL` | `https://myaccount.google.com/` | Page used by `ahb health`; a Looker URL also waits for its data call |

## How `looker_export` works

It opens the report as you and captures one real `batchedDataV2` request. It then replays that request from inside the
page with **all dimensions plus Record Count**, sorted on every dimension, 50k rows per page. Counts are expanded back
into raw rows, and nulls are re-inserted from `nullIndex`. It sees only what you can see as a viewer, and only the
datasources used on the page you open. It depends on undocumented endpoints, so expect breakage when Google changes them.

## Benchmark

Same tasks as [dev.to "16MB vs 1.2GB"](https://dev.to/atani/16mb-vs-12gb-benchmarking-5-ai-browser-automation-tools-34pm).
Full table and method: [docs/benchmark.md](docs/benchmark.md).

| | Linux CLI | Article range (5 tools) |
|---|---|---|
| httpbin open + snapshot + close | 0.76 s | 0.45 – 4.91 s |
| 1 page, unique RAM (USS) | 245 MB | not reported (RSS 16 – 1,202 MB) |
| 3 parallel tabs | 1.01 s | 4.9 – 10.6 s |
| Signed-in Looker export, 212k rows | 99 s | not tested by any of them |

## Development

```bash
pip install -e ".[dev]" && pre-commit install
pytest                              # unit tests, no browser
AHB_E2E=1 pytest -m integration     # real Chrome, no network or Google account needed
ruff check . && ruff format --check . && mypy
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security and responsible use

CDP gives full control of the signed-in Google account. Keep port 9333 on 127.0.0.1 and reach it only through SSH, and
treat the profile directory as a credential. This tool is for automating **your own** account and data you are
allowed to see. Respect Google's Terms of Service and the sharing settings of any report you export. To report a
vulnerability, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
