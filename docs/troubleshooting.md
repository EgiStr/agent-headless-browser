# Troubleshooting

Every symptom below was hit while building this project. The fix is in the right column; the "why" is what makes it stick.

## The MCP server

| Symptom | Cause | Fix |
|---|---|---|
| The client shows no `browser_*` tools | The server was registered after the client started; MCP servers mount at client boot | Restart the client (DSH: restart the app) |
| The client cannot start the server: `command not found` / "not recognized" | On Windows, `CreateProcess` searches the **parent's** PATH, not the PATH inside the `env` the client passes. A bare `ahb-mcp` then cannot be found | Point `command` at the absolute path of `ahb-mcp.exe`, or at `python` with args `-m agent_headless_browser.mcp_server` |
| The server starts but every tool fails with `agent Chrome is not running on http://127.0.0.1:9333` | Chrome is not up | `browser_start`, or `ahb start` |
| The tools are there but the session is empty (`google_accounts: []`) | The Google session died or was never created | The re-login flow below |
| `ahb-mcp` works from a terminal but not from the client | The client spawns the server with its own environment | Absolute path, as above |
| A variable in the config seems ignored | The server only reads the names it knows (`AHB_PORT`, `AHB_PROFILE_DIR`, `AHB_OUT_DIR`, `AHB_CHROME_ARGS`, `AHB_HEALTH_URL`); a typo is ignored silently | Use the exact names |
| The DSH registration disappears after a DSH update | The profile's `cordis.patch.yml` was rewritten | Re-run the installer; it re-adds the entry |

## Chrome

| Symptom | Cause | Fix |
|---|---|---|
| `Google Chrome not found (checked …)` | Chrome is not where the code looks, or only Chromium is installed | Install **Google Chrome**. Chromium is rejected on purpose: Google and Patchright expect Chrome. |
| Chrome is installed but not found (macOS) | It is in a non-standard place | The launcher checks `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` and the same path under `~/Applications`. It does **not** search `PATH` on macOS, so Chrome has to be in one of those two |
| Chrome is installed but not found (Windows, consumer install) | The installer put it in the user's own AppData | `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe` is checked as well |
| Chrome is installed but not found (Linux) | It is not on `PATH` | `/usr/bin/google-chrome`, `/usr/bin/google-chrome-stable` and `/opt/google/chrome/chrome` are checked |
| `already owns port 9333` | Chrome is already running | Reuse it. A second Chrome on the same profile corrupts it. |
| Chrome starts and immediately dies in a container | The sandbox needs root or `--no-sandbox` | `AHB_CHROME_ARGS=--no-sandbox` |
| `Failed to move to new namespace` / sandbox errors on a VPS | Chrome's sandbox is on and the user cannot use it | Run as a normal user, not root; keep the sandbox on |
| The report renders the mobile layout | The default headless window is 800x600 | The launcher passes `--window-size=1366,900` |

## The Google session

| Symptom | Cause | Fix |
|---|---|---|
| The report redirects to `accounts.google.com` | The session is gone | Re-login (below) |
| A copied profile or injected cookies land on the account chooser | DBSC: the session is bound to the device | Do not copy the profile, do not export cookies. Sign in once inside the agent profile. |
| The session dies after a reboot | The profile was not closed gracefully | `browser_stop` / `ahb stop` before the machine goes down |
| `login_finish` returns `{"ok": false}` | The human has not finished signing in | Ask them to finish, then call it again |
| `login_begin` says "no display on this server" | Headless machine | `ahb login-view` on the server, tunnel port 9500, sign in there |

### The re-login flow

```text
login_begin                 → a window opens (desktop) or the login-view steps are returned (server)
the human signs in          → never type the password yourself
login_finish                → verifies the account, restarts Chrome headless
browser_status              → google_accounts must contain the account
```

## The export

See [looker-export.md](looker-export.md) for the full table. The short version:

| Symptom | Fix |
|---|---|
| `not signed in: the report redirected to Google sign-in` | Re-login |
| `the report issued no data requests` | Check the page id and the account's access to the report |
| `datasources: []` on a report with charts | The page rendered from a cached tile and issued no data request, **or** a `/getSchema` response was fetched but could not be parsed — `looker.py` swallows that error, so the list comes back empty instead of raising | Export the exact page URL, then `health_check(report_url)`. For the parse-failure branch, run `ahb -v looker-export <url> --out <dir>`: the warnings only reach stderr, stdout is the JSON result |
| `column ...: N values + M nulls != K rows` | Google changed the response format | Fix the decoder in `looker.py`; the format is documented in [findings.md](findings.md) |
| Fewer rows than the report shows | Not the filters — the export ignores them, so its count is at least the filtered one. Look at the page and the datasource instead | Export every page of the report, then compare with an unfiltered total |
| First data row is `#adm1+name,...` | That is the source file's descriptor row. Skip it. |

## The installer and the DSH registration

| Symptom | Cause | Fix |
|---|---|---|
| `cordis.patch.yml` will not parse after an install | The entry was appended to the file in the wrong shape | The installer matches the file's shape line by line — a `- insert:` list, a `[]` array, or a mapping with `insert:` — and **skips the file with a warning** when the result would not have exactly one entry. It does not parse the YAML, so an exotic layout is left untouched. Backups are at `*.install.bak`, `.bak.1`, `.bak.2`. |
| The DSH entry is there but the tools never appear | DSH mounts MCP servers at boot | Restart DSH |
| The MCP server env is missing a variable | DSH scrubs environment variables whose names match `/KEY\|PASSWORD\|SECRET\|TOKEN/i` | Do not put secrets in the MCP env; the profile directory is the secret |
| `mcp.json` was not written | The existing file is not a config this installer understands: invalid JSON, **or** valid JSON whose `mcpServers` is not an object | The installer leaves it alone, keeps it as `mcp.json.install.bak`, and writes `mcp.json.new` beside it; merge it yourself |
| The installer changed a file it should not have | Older versions overwrote backups | The current installer never overwrites a backup; restore from `*.install.bak*` if needed |
| `python3: not found` (macOS/Linux) / `py: not found` (Windows) | The bootstrap needs Python 3.10+ | The installer bootstraps `uv` and lets it provide Python when needed |

## Health and monitoring

```bash
ahb health --url https://datastudio.google.com/reporting/<id>   # exits 1 when the session is broken
ahb health --every 1800                                         # repeat, appending to session_health.jsonl
```

For a Looker URL, `health_check` passes only when the report's own `batchedDataV2` call returns 200 — a page that loads
but whose data fails is not healthy. The systemd units in `deploy/systemd/` run this on a timer; see [vps.md](vps.md).
