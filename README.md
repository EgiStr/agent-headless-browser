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
- **One Chrome process owns the profile for its whole life, and agents attach over CDP.** In testing, a copied
  profile folder and injected cookies both failed; which step killed the session was never isolated, so both are
  off limits.
- **A server without a display can complete the sign-in.** `ahb login-view` streams the headless tab to your
  browser through an SSH tunnel. This was tested in a display-less Debian container: sign-in, restart, and a 212k-row export.

## Install

Needs Google Chrome (not Chromium). Python 3.10+ is used when the machine has
one; the installer can also bring its own.

### One click

macOS or Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/EgiStr/agent-headless-browser/main/scripts/install.sh | sh
```

Windows, in PowerShell:

```powershell
iwr -useb https://raw.githubusercontent.com/EgiStr/agent-headless-browser/main/scripts/install.ps1 | iex
```

Or clone the repo and double-click `scripts\install.cmd`.

The installer checks the prerequisites, installs the package with `uv` (or
`pipx`, or `pip`), registers the MCP server with the Claude Code and DSH
profiles it finds on the machine, and prints the sign-in steps. It never signs
in for you: that step is yours.

To see what it would do without changing anything:

```bash
python3 scripts/install.py --dry-run    # macOS or Linux
py scripts/install.py --dry-run         # Windows
```

It is safe to run again after an update, and it reuses an agent Chrome that is
already running instead of starting a second one.

### Pinning the bootstrap

The one-liners above fetch `main`, which is a moving target: whatever sits on
`main` at that moment runs with your privileges. If you want a fixed copy,
point the installer at a commit instead:

```bash
curl -fsSL https://raw.githubusercontent.com/EgiStr/agent-headless-browser/<commit>/scripts/install.sh | sh
```

The same works for the PowerShell one-liner. The script is short, and the whole
install lives in [`scripts/install.py`](scripts/install.py), so it is worth a
read before you run it.

### By hand

```bash
pipx install git+https://github.com/EgiStr/agent-headless-browser
# or, from a clone:  pip install -e ".[dev]"
```

Then point an MCP client at it, for example
`claude mcp add -s user agent-browser -- ahb-mcp`. A ready-to-copy config is in
[`mcp.example.json`](mcp.example.json).

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

## Documentation

| Document | Read it when |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How the pieces fit, what lives on disk, where to add a tool |
| [docs/mcp-tools.md](docs/mcp-tools.md) | You are calling the tools: exact signatures, return shapes, error strings |
| [docs/looker-export.md](docs/looker-export.md) | You are exporting a report, or a number looks wrong |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Something failed: symptom → cause → fix |
| [docs/development.md](docs/development.md) | You are changing the code or cutting a release |
| [docs/vps.md](docs/vps.md) | You are running it on a server with no display |
| [docs/findings.md](docs/findings.md) | You want to know *why* the design is like this; it is the experiment log |
| [AGENTS.md](AGENTS.md) | You are an agent working in this repository |

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
