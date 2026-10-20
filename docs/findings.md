# Findings: headless agent browser on a logged-in Google profile

> **Research log.** Each experiment is recorded as it ran in Oct 2026, before the code became the
> `agent_headless_browser` package. Script names here refer to that early layout: `serve_chrome.py` became
> `ahb start/stop`, `looker_crawl.py` became `ahb looker-export`, `healthcheck.py` became `ahb health`, and
> `login_view.py` became `ahb login-view`.

Goal: an AI-agent browser that reuses a logged-in Google account, runs headless
24/7 (VPS), and can pull every row behind the Looker Studio report
`<report-id>` (shared only to the tester's Google Workspace account) into CSV.

Status: **data goal reached** (`out/wfp_food_prices_idn.csv`, 212,117 rows, validated).
Headless-profile goal: **works** with one long-lived headless Chrome on its own profile (Exp 4); multi-hour lifetime still being logged.

## 1. Research (state of the art, Oct 2026)

| Option | What it is | Fit |
|---|---|---|
| Playwright / **Patchright** `launch_persistent_context(user_data_dir, channel="chrome")` | Real Chrome with persistent profile; Patchright patches CDP leaks (`Runtime.enable`, `navigator.webdriver`) | Used here. Lightweight, Python, headless or headful |
| Playwright `connect_over_cdp` | Attach to an already running, already logged-in Chrome | Used here (attach mode) |
| chrome-devtools-mcp `--autoConnect`, Claude in Chrome, Open Browser Use | Drive the user's real desktop Chrome (extension or chrome://inspect remote debugging) | Desktop only; not headless |
| Vercel `agent-browser`, browser-use, Stagehand | LLM-agent layers on top of CDP/Playwright | Optional layer on top; same profile problem underneath |
| Camoufox / nodriver | Anti-fingerprint Firefox / CDP-less Chrome driver | Not needed: Google did not block real Chrome here |
| Docker Chrome + Xvfb + noVNC (e.g. `linuxserver/chromium`, Steel browser) | Headful Chrome on a VPS, login once over noVNC, persistent volume | Recommended VPS shape |

Key platform facts that decide the design:

- **Chrome 136+ refuses `--remote-debugging-port` on the default user-data-dir.** An agent needs its own `--user-data-dir`.
- **Chrome 146+ ships DBSC (Device Bound Session Credentials) on Windows, on by default for Google accounts.**
  The session's refresh is signed with a TPM-held key, so Google cookies copied out of a profile stop working elsewhere.
  The desktop profile here has `Default/Network/Device Bound Sessions`, so it is bound.
- The Cookies DB is locked while desktop Chrome runs and is app-bound encrypted (Chrome 127+), so copying the profile folder is not a path.

## 2. Experiments

### Exp 1: attach to the logged-in desktop Chrome over CDP: PASS
`python looker_crawl.py --mode attach --url <report-url>`

- Desktop Chrome already exposed CDP via chrome://inspect remote debugging (`DevToolsActivePort`, port 9222; HTTP `/json` disabled, WebSocket only).
- Report loads as the test account. Network shows `getReport`, `getSchema`, `getClaimedDatasources`, `batchedDataV2`.
- One datasource: `wfp_food_prices_idn.csv` (embedded CSV upload), 14 columns.
- Method: capture one real `batchedDataV2` request (URL + headers + body), then replay it **from inside the page** with all 14 dimensions + Record Count, sorted on every dimension, `rowsCount=50000` pages.
  The server returned 50k rows per page with no cap hit; 5 pages.
- Validation (one-off script, not kept):
  - 212,117 rows = sum of the report's own per-province chart (212,117).
  - All 36 admin1 counts match the chart exactly.
  - 212,117 unique rows, every Record Count = 1 (no rows hidden by GROUP BY).
  - Dates 2007-01-15 .. 2024-02-15. 1,139 "National Average" rows have empty admin1/admin2/lat/lon, same as the source.
  - Row 1 is the HDX hashtag row (`#adm1+name`, ...) which is literally in the uploaded CSV; kept as-is.

### Exp 2: fresh headless profile seeded with exported cookies: FAIL
`python looker_crawl.py --mode headless --cookies secrets/google_cookies.json`

- 143 Google cookies exported over CDP (incl. `SID`, `__Secure-1PSID`, `__Secure-1PSIDTS`, `__Secure-1PSIDRTS`).
- Headless: redirected to `accounts.google.com/.../accountchooser`.
- Same test **headful**: same redirect. So this is not headless detection; it is DBSC.
  Bound cookies (`*PSIDTS`/`*PSIDRTS`) cannot be refreshed without the original profile's device key.
- Conclusion: cookie export/import (the usual "storage_state" trick) is dead for Google on Chrome 146+ Windows profiles.

### Exp 3: dedicated agent profile, one manual login with DBSC disabled, then headless
Agent profile `profiles/agent`, Chrome flags `--disable-features=DeviceBoundSessions,EnableBoundSessionCredentials`,
human signs in once (headful; the script was later folded into `serve_chrome.py --headful` / `login_begin`), then the crawler relaunches the same profile headless.

| Run | Result |
|---|---|
| Login (headful) | OK. Profile has **no** `Network/Device Bound Sessions` file, so the session is unbound |
| 3a: same profile, headless, no cookies | **PASS**: report loads, 212,117 rows, CSV byte-identical (sorted) to Exp 1 |
| relaunch profile to export its 34 cookies | ran |
| 3b: fresh headless profile + those cookies | FAIL (account chooser) |
| 3c: `cp -r` of the profile folder, headless | FAIL (account chooser) |
| 3a rerun: original profile | FAIL. The session was dead by then |
| desktop Chrome (attach) afterwards | still OK |

**Inconclusive:** the original profile was not checked between 3a and 3b, so it is unknown which step killed the session:
the export relaunch, the cookie injection, the folder copy rotating `__Secure-1PSIDTS`, or just a short lifetime.
3b therefore does **not** prove that injection fails without DBSC.
What is proven: headless Chrome on its own logged-in profile works against Looker Studio, with no bot block.

Lessons that changed the design:
- One Chrome process owns a profile, for its whole life. No copies and no cookie transplants.
- Never kill Chrome (`timeout`, kill -9). Close it gracefully so rotated cookies reach disk.
- Agents attach over CDP to that one process instead of launching their own.

### Exp 4: long-lived Chrome + periodic health check (in progress)
`serve_chrome.py` runs Chrome directly (not through Playwright), so there is no `--enable-automation` and every flag is ours.
Login once with `--headful`, `--stop`, restart headless, then `healthcheck.py --every 1800` logs to `out/session_health.jsonl`.

| Step (2026-10-20) | Result |
|---|---|
| 22:55 human login, headful, check | OK, data 200; no `Device Bound Sessions` file |
| `--stop` v1 | **Bug**: Playwright `browser.close()` only *disconnects* from a CDP browser. Chrome kept running headful, and the "headless" relaunch was handed to that process by Chrome's singleton. Fixed: `--stop` now sends CDP `Browser.close`, and start refuses if the port is already owned |
| 22:58 graceful stop -> restart `--headless=new` (process cmdline verified) -> check | OK, data 200. **The session survives a clean restart** |
| full crawl over CDP on headless Chrome | PASS: 212,117 rows, identical to Exp 1 |
| `healthcheck.py --every 1800` | running; see `out/session_health.jsonl` |
| 22:59 and 23:05 checks on headless Chrome | OK, data 200 (5 of 5 checks OK since login) |

Observed headless session lifetime so far: **about 7 minutes after the headless restart (22:58 to 23:05), about 10 minutes since login**.
That shows the session works; it says nothing about 24 hours yet.

Still open: lifetime over many hours or days, behaviour after a crash or kill, and the deliberate transplant test.
Run the transplant test last, because it may revoke the session.

### Exp 5: packaged as an MCP server (`mcp_server.py`) + skill (`.claude/skills/agent-browser`)
All 14 tools exercised over real MCP stdio (`scripts/test_mcp.py`, `scripts/test_mcp_actions.py`):
status shows headless and the test account (read from `accounts.google.com/ListAccounts`); navigate, snapshot,
click, type, key, screenshot and evaluate work; `login_finish` restarted Chrome headless with the session intact;
`looker_export` produced a CSV identical to Exp 1.

Found while testing:
- **The MCP client hard-killed Chrome on exit.** On Windows, mcp's stdio client puts the server in a
  `KILL_ON_JOB_CLOSE` Job Object, so a Chrome started by the server died with it.
  `CREATE_BREAKAWAY_FROM_JOB` did not help. Fix: on Windows, launch Chrome through WMI `Win32_Process.Create`,
  which puts it outside the tree. Verified that Chrome stays alive after the client exits.
- **The session survived that hard kill** (23:15 check OK). This happened once and is not a guarantee; prefer `browser_stop`.
- The default headless window is 800x600, so sites render their mobile layout (Wikipedia hid its search box).
  The launcher now passes `--window-size=1366,900`.
- mcp 2.x hides exception text from the model; tools wrap errors as `ToolError` so the agent sees the cause.

### Exp 6: Linux CLI "VPS" simulation (Docker, Debian bookworm, Chrome 155, no X server, no DISPLAY)
Image `vps/Dockerfile`; code mounted at /src; profile on a Linux-native volume.
- `serve_chrome.py --login` starts **headless** Chrome on the Google sign-in page.
  The page renders normally (identifier step, no "browser not secure" block), with UA `X11; Linux x86_64 ... Chrome/155.0.0.0`
  and `navigator.webdriver=false`. The launcher replaces the `HeadlessChrome` UA.
- Login path without any display: tunnel CDP to the PC, then chrome://inspect -> inspect the sign-in tab.
  DevTools shows a live screencast that takes mouse and keyboard input.
- Chrome 155 **ignores `--remote-debugging-address=0.0.0.0`** and always binds 127.0.0.1.
  On a VPS that is what you want (SSH tunnel only). The container uses `vps/tcp_forward.py` as a stand-in for `ssh -L`.
- Inside Docker Chrome needs `--no-sandbox` (`AGENT_CHROME_ARGS`). A real VPS running as a non-root user does not.
- The chrome://inspect route proved too fiddly for the user, so I added `login_view.py`: a live JPEG view of the headless tab
  at http://127.0.0.1:9500 that you click and type into. Keystrokes are forwarded to Chrome and never stored.
  On a VPS: `ssh -N -L 9500:127.0.0.1:9500 user@vps`.

| Step (Linux CLI container) | Result |
|---|---|
| user signs in through `login_view.py` (headless, no X) | OK: `signed in: <account>` |
| health check on the Looker report | OK, data 200 |
| graceful `--stop`, restart headless (not login mode), check | OK, data 200; no `*bound*` file anywhere in the profile |
| `looker_crawl.py --mode attach` | **PASS**: 212,117 rows in 99 s; sorted md5 identical to the Windows CSV (`a1d71e7e...`) |

**Answer: yes, a Linux CLI VPS can sign in to Google headless and run the full export, with no X server or desktop.**
Still untested: a real datacenter IP (Google may ask for extra verification on a new device or location) and a multi-day lifetime.

## Benchmark

Moved to [benchmark.md](benchmark.md).

## 3. VPS setup

Moved to [vps.md](vps.md).

## 4. Looker Studio extraction notes
- Endpoints: `POST /u/0/getSchema` (fields), `POST /u/0/batchedDataV2` (data), XSSI prefix `)]}'`.
- Data is column-oriented with `nullIndex`; the decoder re-inserts nulls and asserts lengths.
- Asking for every dimension + Record Count gives row-level data; rows are expanded by count so duplicates are not lost.
- Replay runs inside the report tab (`fetch` with the captured headers), so auth and XSRF come from the real session.
- The viewer only gets what the report's datasource exposes. Here that is the whole embedded CSV.
