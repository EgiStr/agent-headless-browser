---
name: agent-browser
description: Use when a task needs the user's own signed-in Google session — a Looker Studio / Data Studio report, Google Drive, Sheets, Gmail, or any page shared only with that account — or when the agent Chrome must be started, stopped, re-logged-in or health-checked. Also use when a client cannot see the agent-browser tools, or a report returns fewer rows than it shows.
---

# agent-browser (logged-in headless Chrome)

One long-lived Chrome owns the agent profile and listens on CDP `127.0.0.1:9333`. Every tool attaches to that process, so
the Google session survives from task to task. The profile (`AHB_PROFILE_DIR`, default
`~/.agent-headless-browser/profile`) holds the sign-in and is treated as a credential.

**Core principle: check the session, use the one browser, report only what you read.**

## Start every task with

1. `browser_status` — read `running`, `headless` and `google_accounts`.
2. `running: false` → `browser_start(headful=False)`, then `browser_status` again.
3. `google_accounts: []` → the session is gone. Re-login:
   - `login_begin` opens the sign-in page: a visible window on a desktop, or `ahb login-view` steps on a server with no
     display.
   - **The human signs in.** Never type a password, a 2FA code or recovery information.
   - When the user says it is done → `login_finish`. It verifies the account and restarts Chrome headless.

Do not skip `browser_status`. Every other tool fails in a less obvious way when the session is gone.

Both `login_begin` and `login_finish` restart Chrome, so any open tab is gone afterwards and you have to navigate again.

## If there is no `browser_status` in your tool list

The server is not registered with your client, or the client started before the registration happened. MCP tool lists are
snapshotted when the client connects, so **restart the client** after registering. If it still shows nothing, run the
server by hand (`ahb-mcp`) and read its stderr: a missing package, a wrong path, or an environment the client did not pass
is the usual cause. On Windows, point `command` at the absolute path of `ahb-mcp.exe`, or at `python` with args
`-m agent_headless_browser.mcp_server` — a client that spawns the server with its own environment cannot find a bare
`ahb-mcp` on `PATH`.

## The tools

| Tool | Signature | Use |
|---|---|---|
| `browser_status` | `()` | Chrome running? headless? which Google accounts? |
| `browser_start` | `(headful=False)` | Start the one Chrome that owns the profile |
| `browser_stop` | `()` | Close gracefully, so rotated cookies reach the profile |
| `login_begin` / `login_finish` | `()` / `()` | The human signs in; you verify and go back to headless |
| `navigate` | `(url, wait_ms=3000)` | Open a page; returns the **final** url, title, first 3000 chars |
| `page_snapshot` | `(max_elements=150)` | Page text plus numbered elements `[1] <button> Export` |
| `click` | `(ref=None, selector=None)` | By snapshot ref, or a Playwright selector (`text=…`, `css=…`, `role=button[name="…"]`) |
| `type_text` | `(text, ref=None, selector=None, submit=False)` | Fill a field. Never for credentials |
| `press_key` | `(key)` | `Enter`, `Escape`, `PageDown`, `Control+A` |
| `screenshot` | `(full_page=False)` | PNG, when the text snapshot cannot describe the page |
| `evaluate` | `(script)` | JS in the page; for structured extraction (a table, a JSON blob) |
| `looker_export` | `(report_url, out_dir=None, page_size=50000)` | Every row of every datasource behind a report, to CSV |
| `health_check` | `(url=None)` | Is the session still good? Appended to `session_health.jsonl` |

Exact return shapes and error strings: [docs/mcp-tools.md](../../../docs/mcp-tools.md).

**Refs are per snapshot.** After a navigation, a click that changes the page, or an open menu, take a new snapshot.

## Which tool for which job

| You want | Use |
|---|---|
| The page's text | `navigate` (first 3000 chars) |
| To click or fill something | `page_snapshot` → `click`/`type_text` by ref |
| A button with a known label | `click(selector="text=Export")` — no snapshot needed |
| A number that lives in the DOM | `evaluate` |
| A chart, a canvas, anything visual | `screenshot` |
| Every row behind a report | `looker_export` |
| Proof the session works | `health_check` (for a report URL it waits for the report's own data call) |

## Looker Studio export

Short version — the full workflow is in the **`looker-report-export`** skill and in
[docs/looker-export.md](../../../docs/looker-export.md):

```text
looker_export(report_url, out_dir)   → per datasource: name, csv_path, raw_rows, grouped_rows, columns
```

- Only the datasources **used on the page you open** are captured. A multi-page report needs one call per page URL
  (`…/reporting/<id>/page/<pageId>`), and **one `out_dir` per page** — same-named datasources would overwrite each other.
- Two files per datasource: `<name>.csv` (raw rows) and `<name>_grouped.csv` (plus a `record_count` column).
- The export **ignores the report's filters**: it is the whole datasource, not the filtered view.
- Give `out_dir` an **absolute** path: a relative one resolves against the MCP server's working directory.

## Rules that keep the session alive

- **Never copy the profile directory and never export or inject its cookies.** Since Chrome 146 a Google session can be
  bound to the device (DBSC); a transplanted cookie lands on the account chooser. In testing, both revoked the session.
- **Never start a second Chrome on the profile.** `browser_start` refuses when the port is taken; that refusal is correct.
- **Prefer `browser_stop` over killing Chrome.** A hard kill survived once in testing, but rotated cookies can be lost.
- **Keep the port on 127.0.0.1.** CDP is full control of the signed-in account.
- **Stay inside the user's own account and data.** Do not solve CAPTCHAs; hand a challenge to the user via `login_begin`.
- **Leave Chrome running at the end of a task.** It is a 24/7 browser; the next task should reuse it. Stop it only when the
  user asks, or when the machine is going down.

## Common mistakes

| Mistake | Why it is wrong | Do instead |
|---|---|---|
| Typing a password into the sign-in form | The session is bound to a device; a scripted login can lock the account | `login_begin`, let the human sign in, then `login_finish` |
| Copying the profile to "keep a backup" and running Chrome from the copy | A copy is a second Chrome on the same account | Back it up as a file, never run from it |
| Guessing DOM selectors such as `[data-page-id]` | They do not exist; the calls waste time and mislead | Read the page text with `page_snapshot`; get page ids from the report's own tabs |
| Reporting a row count that no tool returned | It is a guess dressed as a measurement | Only state numbers you read from a tool's answer or from the exported file |
| Summing the columns of several tables into one "total columns" | Meaningless: the tables have different columns | Report rows and columns **per datasource**, and a total row count |
| Stopping Chrome after every task | The next task has to start it again; the browser is meant to stay up | Leave it running |
| Treating "the report has charts" as "the export found the datasource" | A cached tile can render without a data call | Compare the tool's `datasources` with the report's own charts, and `health_check` the report URL |

## Red flags — stop and think

- You are about to type into a field on `accounts.google.com`.
- You are about to copy, move or archive the profile directory.
- You are about to start a Chrome yourself instead of calling `browser_start`.
- You are about to report a number you have not seen in a tool's answer or in the exported file.
- You are about to claim the export is complete without comparing it with the report's own total.

## Without MCP (the CLI)

```bash
ahb status                              # running, headless, signed-in accounts
ahb start [--headful | --login]         # ahb stop closes gracefully
ahb looker-export <report-url> --out <dir>
ahb health --url <url> [--every 1800]   # exit code 1 when the session is broken
ahb login-view                          # sign in on a server with no display
```

## Server without a display

Tested in a display-less Linux container: headless sign-in works, and the export returned the same 212,117 rows.

1. `ahb start --login` — headless Chrome on the sign-in page.
2. `ahb login-view` on the server; on your PC `ssh -N -L 9500:127.0.0.1:9500 user@server`, then open
   http://127.0.0.1:9500 and sign in there. The header shows `signed in: …` when it worked.
3. `ahb stop && ahb start`, or use the systemd units in `deploy/systemd`.

Never move a profile between machines; sign in on the server itself. Size by USS: about 250–350 MB per Chrome. See
[docs/vps.md](../../../docs/vps.md) and [docs/benchmark.md](../../../docs/benchmark.md).
