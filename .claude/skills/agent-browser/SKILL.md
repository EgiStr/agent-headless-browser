---
name: agent-browser
description: Drive a headless Chrome that is already signed in to the user's Google account through the agent-browser MCP tools, for 24/7 automation without logging in each time. Use for anything behind a Google login, such as Looker Studio / Data Studio reports, Google Drive, Sheets or Gmail pages, crawling pages shared only with the user's email, and exporting a Looker Studio report's source data to CSV. Also use when the session needs a re-login, a health check, or a VPS setup.
---

# Agent browser (logged-in headless Chrome)

One long-lived Chrome process owns the agent profile `profiles/agent` and listens on
CDP `127.0.0.1:9333`. Every MCP tool attaches to that process. The Google session lives
in that profile, created once by a human login with DBSC (device-bound sessions) turned off.

## Start every task with
1. `browser_status`. Check `running`, `headless`, and that `google_accounts` contains the needed account.
2. If it is not running: `browser_start` (headless). Then call `browser_status` again.
3. If `google_accounts` is empty, the session is gone. Re-login:
   - `login_begin` opens a visible Chrome window on the Google sign-in page.
   - Tell the user to sign in there themselves. Never type passwords, 2FA codes or recovery info.
   - When the user says it is done: `login_finish`. It verifies the account and restarts Chrome headless.

## Tools
| Tool | Use |
|---|---|
| `navigate(url, wait_ms)` | open a page; returns url, title, first 3k chars of text |
| `page_snapshot(max_elements)` | page text + numbered interactive elements `[n] <tag> label -> href` |
| `click(ref \| selector)` | ref from the latest snapshot, or a Playwright selector (`text=Download`, `css=...`, `role=button[name="Share"]`) |
| `type_text(text, ref \| selector, submit)` | fill a field; never for credentials |
| `press_key(key)` | `Enter`, `Escape`, `PageDown`, `Control+A` ... |
| `screenshot(full_page)` | PNG, when text is not enough (charts, canvas) |
| `evaluate(script)` | JS in the page; use it for structured extraction (tables, JSON in the DOM) |
| `looker_export(report_url, out_dir)` | every row of each datasource behind a Looker Studio report, to CSV |
| `health_check(url)` | loads a page that needs the session; logs to `out/session_health.jsonl` |
| `browser_start` / `browser_stop` | lifecycle; stop is graceful, so cookies get written to disk |

Refs are reassigned on every `page_snapshot`; take a new snapshot after navigation or a click
that changes the page.

## Looker Studio export
- `looker_export` returns, per datasource: `name`, `csv` path, `raw_rows`, `columns`. A `_grouped.csv` with `record_count` is written next to it.
- It works for any report the signed-in account can view. It asks the report's own backend
  (`batchedDataV2`) for all dimensions + Record Count, pages by 50k, and expands counts back into rows.
- Verify before reporting: compare `raw_rows` with a total shown in the report (scorecard or table total).
  Example from testing: a report on the public HDX `wfp_food_prices_idn.csv` gave 212,117 rows, 14 columns.
- Only datasources used on the landing page are captured. For a multi-page report, `navigate` to each page
  URL (`.../reporting/<id>/page/<pageId>`) and call `looker_export` with that URL.
- `ok: false` with "NOT LOGGED IN": run the re-login flow above.

## Rules that keep the session alive
- Never copy `profiles/agent` and never export or inject its cookies. In testing, that revoked the Google session.
- Never start a second Chrome on the same profile. `browser_start` refuses if port 9333 is taken.
- Prefer `browser_stop` over killing Chrome. A hard kill survived once in testing, but rotated cookies can be lost.
- Keep port 9333 on 127.0.0.1. CDP gives full control of the Google account.
- Stay within the user's own account and data. Do not solve CAPTCHAs. If Google shows a challenge, hand it to the user via `login_begin`.

## Without MCP (CLI)
```
.venv/Scripts/python serve_chrome.py [--headful | --stop]
.venv/Scripts/python looker_crawl.py --mode attach --cdp http://127.0.0.1:9333 --url <report-url>
.venv/Scripts/python healthcheck.py --url <report-url> --every 1800
```

## VPS (Linux CLI, no display)
Tested in a Linux CLI container: headless login works and `looker_export` gives the same 212,117 rows.
1. `python serve_chrome.py --login`: headless Chrome opens on the Google sign-in page.
2. `python login_view.py`; the user runs `ssh -N -L 9500:127.0.0.1:9500 user@vps` and opens http://127.0.0.1:9500.
   They click and type there, and the header shows `signed in: ...` when done. `login_begin` returns these steps when there is no DISPLAY.
3. `python serve_chrome.py --stop`, then `python serve_chrome.py` (headless), ideally under systemd.
Never move a Windows profile to Linux; sign in on the VPS itself. Size the VPS by USS: about 250-350 MB per Chrome.
See FINDINGS.md §3 and the benchmark table.
