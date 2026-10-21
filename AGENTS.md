# AGENTS.md

Work on this repository? Read this first, then the docs it points at.

## What this is

A headless Chrome that stays signed in to the user's own Google account, exposed to AI agents as an MCP server (14 tools).
One of the tools exports every row behind a Looker Studio report to CSV.

## Commands

```bash
pip install -e ".[dev]"
pytest                              # unit tests, no browser, no network
AHB_E2E=1 pytest -m integration     # real Chrome, local pages only, no Google account
ruff check . && ruff format --check . && mypy
python scripts/install.py --dry-run # what the installer would do, changes nothing
```

CI additionally builds the wheel and checks it with `twine`. See [docs/development.md](docs/development.md).

## Invariants

These are not style preferences. Breaking them breaks the Google session or the export.

1. **Never copy the profile directory and never export or inject its cookies.** Since Chrome 146 a Google session can be
   bound to the device (DBSC). A transplanted cookie lands on the account chooser. In testing, both revoked the session.
2. **One Chrome owns the profile.** A second Chrome on the same profile corrupts it; `browser_start` refuses when the CDP
   port is taken, and that refusal is correct.
3. **Never type a password, a 2FA code or recovery information.** The human signs in; the agent verifies with
   `login_finish`.
4. **Close Chrome gracefully** (`browser_stop`, `ahb stop`), so rotated cookies reach the profile.
5. **Real Google Chrome only.** Chromium is rejected: Google and Patchright expect Chrome.
6. **CDP stays on 127.0.0.1.** It is full control of the signed-in account.
7. **The installer uses the standard library only** (`scripts/install.py`), because it runs before anything is installed.

## Where things are

| Path | Contents |
|---|---|
| `src/agent_headless_browser/` | The package: `chrome.py` (launch, find, stop), `mcp_server.py` (the tools), `looker.py` (the export), `session.py`, `google.py`, `health.py`, `login_view.py`, `config.py`, `cli.py` |
| `scripts/` | `install.py` plus the shell/PowerShell/cmd wrappers for the one-click install |
| `tests/` | Unit tests per module, plus `tests/integration/test_e2e.py` |
| `docs/` | The documentation set (see below) |
| `.claude/skills/agent-browser/SKILL.md` | The skill an agent loads to drive this server |

`chrome.py` and `scripts/install.py` must agree on where Chrome lives; a test enforces it.

## Documentation

| Document | Read it when |
|---|---|
| [docs/architecture.md](docs/architecture.md) | You need to know how the pieces fit, what is on disk, or where to add a tool |
| [docs/mcp-tools.md](docs/mcp-tools.md) | You are calling the tools and need exact signatures and error strings |
| [docs/looker-export.md](docs/looker-export.md) | You are exporting a report, or a number looks wrong |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Something failed and you need the symptom → cause → fix |
| [docs/development.md](docs/development.md) | You are changing the code or cutting a release |
| [docs/vps.md](docs/vps.md) | You are running it on a server with no display |
| [docs/findings.md](docs/findings.md) | You want to know *why* the design is like this; it is the experiment log |

## The skill

`.claude/skills/agent-browser/SKILL.md` tells an agent how to drive this server: check `browser_status` first, never type
credentials, never copy the profile, export per page, and verify the numbers before reporting them. If you change the
tools, update that skill and `docs/mcp-tools.md` in the same commit — the skill is what makes the tools usable.

## Conventions

- Imperative commit messages. A CHANGELOG entry for every user-visible change.
- Stage explicit paths, never globs.
- Never commit a profile, cookies, an export, a report id or a real email address.
- `mypy --strict` stays clean; the code is fully annotated.
