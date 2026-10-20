# agent-headless-browser

A headless Chrome for AI agents that stays signed in to **your own** Google account. It runs 24/7 on a
laptop or a Linux CLI VPS and is exposed as an **MCP server** plus a **Claude Code skill**.
It ships a tool that exports every row behind a Looker Studio report to CSV.

```
 AI agent (Claude Code, any MCP client)
        │  stdio
 mcp_server.py ── 14 tools ──┐
        │  CDP (127.0.0.1 only)
 serve_chrome.py ─► one long-lived Chrome ─► profiles/agent  (your Google session lives here)
                         ▲
 login_view.py ──────────┘  live click-and-type view for the one-time sign-in (no X server needed)
```

## Why it is built this way

These findings come from experiments; the details are in [FINDINGS.md](FINDINGS.md).

- **Copying a Chrome profile or its cookies does not work for Google any more.** Chrome 146+ binds Google sessions to the
  device (DBSC, a TPM-held key). Exported cookies bounce to the account chooser, headless or not.
- **Sign in once inside a dedicated agent profile** with DBSC disabled, then reuse that profile headless.
- **One Chrome process owns the profile for its whole life.** Agents attach over CDP. Copying the profile or
  transplanting its cookies got the session revoked in testing.
- **A CLI-only VPS can do the sign-in headless.** `login_view.py` streams the headless tab to your browser over an
  SSH tunnel. This was tested in a display-less Debian container: login, restart, and a full 212k-row export.

## Quickstart

Requirements: Google Chrome (not Chromium), Python 3.11+.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt          # Windows: .venv\Scripts\pip
```

**1. Sign in once**

On a desktop with a screen:
```bash
python serve_chrome.py --headful      # sign in in the window that opens
python serve_chrome.py --stop
```

On a CLI-only server:
```bash
python serve_chrome.py --login        # headless Chrome on the Google sign-in page
python login_view.py                  # live view on 127.0.0.1:9500
# on your PC:  ssh -N -L 9500:127.0.0.1:9500 user@your-vps   -> open http://127.0.0.1:9500, sign in
python serve_chrome.py --stop
```

**2. Run headless**
```bash
python serve_chrome.py                # keeps running; CDP on 127.0.0.1:9333
python healthcheck.py --url https://myaccount.google.com/
```

**3. Use it**
```bash
python looker_crawl.py --mode attach --cdp http://127.0.0.1:9333 \
  --url https://lookerstudio.google.com/reporting/<report-id> --out out
```

## MCP server

```bash
claude mcp add -s user agent-browser -- /path/to/.venv/bin/python /path/to/mcp_server.py
```
Env: `AGENT_BROWSER_PORT` (9333), `AGENT_BROWSER_PROFILE` (`profiles/agent`), `AGENT_BROWSER_OUT` (`out`),
`AGENT_CHROME_ARGS` (extra Chrome flags), `AGENT_HEALTH_URL`.

| Tool | What it does |
|---|---|
| `browser_status` | running? headless? which Google accounts are signed in |
| `browser_start` / `browser_stop` | lifecycle; stop is graceful, so cookies reach disk |
| `login_begin` / `login_finish` | re-login: a window on desktops, `login_view.py` steps on CLI servers; finish verifies the account and returns to headless |
| `navigate`, `page_snapshot`, `click`, `type_text`, `press_key`, `screenshot`, `evaluate` | general browsing; the snapshot gives numbered refs for click/type |
| `looker_export(report_url)` | every row of each datasource behind a Looker Studio report, to CSV |
| `health_check(url)` | is the session still good; appends to `out/session_health.jsonl` |

The skill at [.claude/skills/agent-browser/SKILL.md](.claude/skills/agent-browser/SKILL.md) tells the agent how to use these
tools safely: check status first, never type passwords, and never copy the profile.

## How `looker_export` works

It loads the report as you, captures one real `batchedDataV2` request, and replays it from inside the page with
**all dimensions + Record Count**, sorted on every dimension and paged by 50k rows. It then expands the counts back into
raw rows and re-inserts nulls from `nullIndex`. It sees only what the viewer can see, and only datasources used on the
page you open.

## Benchmark

Same tasks as [dev.to: "16MB vs 1.2GB"](https://dev.to/atani/16mb-vs-12gb-benchmarking-5-ai-browser-automation-tools-34pm),
run with `scripts/bench.py`. The article used macOS on Apple Silicon, so read these as orders of magnitude.

| | Windows laptop | Linux CLI (Docker) | article range |
|---|---|---|---|
| httpbin open + snapshot + close | 0.58 s | 0.76 s | 0.45 – 4.91 s |
| 1 page, RSS sum (article metric) | 821 MB | 1,532 MB | 16 – 1,202 MB |
| 1 page, USS (real unique RAM) | 325 MB | **245 MB** | not reported |
| 3 parallel tabs, time / USS | 1.08 s / 358 MB | 1.01 s / 273 MB | 4.9 – 10.6 s |
| react.dev load | 2.51 s | 1.44 s | 1.45 – 12.77 s |
| Google-auth Looker export, 212k rows | 120 s | 99 s | not tested |

RSS double-counts Chrome's shared pages on Linux, so size a VPS by USS: about 2 GB is comfortable.

## Limitations

- Tested on Windows 11 and a Debian container on a home connection. **A real datacenter VPS is untested**: Google may ask
  to verify a new device or location, and you finish that in `login_view.py`.
- Multi-day session lifetime has not been measured yet. Run `healthcheck.py --every 1800` and alert on `"ok": false`.
- Looker export depends on undocumented internal endpoints and can break when Google changes them.
- In Docker, Chrome needs `AGENT_CHROME_ARGS=--no-sandbox`. On a normal VPS, run it as a non-root user without that flag.

## Responsible use

This is for automating **your own** account and data you are allowed to see. CDP on port 9333 gives full control of the
signed-in Google account: keep it on 127.0.0.1 and reach it only through SSH. Respect Google's Terms of Service and the
sharing settings of any report you export. Never commit `profiles/`, because it is a live login.

## License

MIT
