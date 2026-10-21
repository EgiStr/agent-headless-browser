# The 14 MCP tools

`ahb-mcp` speaks MCP over stdio. Every tool is a function; every failure comes back as a `ToolError` whose text is
`ExceptionType: message`, so the cause reaches the model instead of being swallowed by mcp 2.x.

Signatures below are exact — they are the code, not a paraphrase.

## At a glance

| Tool | Signature | Returns |
|---|---|---|
| `browser_status` | `()` | `{running, endpoint, profile, browser?, headless?, google_accounts?}` |
| `browser_start` | `(headful: bool = False)` | a message |
| `browser_stop` | `()` | a message |
| `login_begin` | `()` | a message with the steps the human must take |
| `login_finish` | `()` | `{ok, google_accounts, headless}` or `{ok: False, detail}` |
| `navigate` | `(url: str, wait_ms: int = 3000)` | `{url, title, text}` (text capped at 3000 chars) |
| `page_snapshot` | `(max_elements: int = 150)` | `{url, title, text, elements}` |
| `click` | `(ref: int \| None = None, selector: str \| None = None)` | `{url, title}` |
| `type_text` | `(text: str, ref=None, selector=None, submit: bool = False)` | `{url}` |
| `press_key` | `(key: str)` | `"ok"` |
| `screenshot` | `(full_page: bool = False)` | a PNG image |
| `evaluate` | `(script: str)` | the result as JSON text (capped at 20 000 chars) |
| `looker_export` | `(report_url: str, out_dir: str \| None = None, page_size: int = 50000)` | `{out_dir, datasources: [...]}` |
| `health_check` | `(url: str \| None = None)` | `{ts, ok, status, landed}` |

## Lifecycle

### `browser_status()`

The first call of every task. It never fails, and it answers three different questions at once: is Chrome up, is it
headless, and **which Google account is signed in**.

```json
{
  "running": true,
  "endpoint": "http://127.0.0.1:9333",
  "profile": "C:\\Users\\me\\.agent-headless-browser\\profile",
  "browser": "Chrome/155.0.8059.85",
  "headless": true,
  "google_accounts": ["you@example.com"]
}
```

`google_accounts` is empty when the browser is not running, and also when the profile has no live session. The two cases
are told apart by `running`.

### `browser_start(headful=False)`

Starts the one Chrome that owns the profile. It refuses when the CDP port is already taken — that is a feature, not an
error to work around: a second Chrome on the same profile corrupts it. If Chrome is already running, start it once and
reuse it.

### `browser_stop()`

Closes Chrome gracefully through CDP `Browser.close`, so rotated cookies are written to the profile directory. Never kill
the process instead.

### `login_begin()` / `login_finish()`

The agent must never type a password, a 2FA code or recovery information — the tool says so and it is not negotiable.

`login_begin` opens the sign-in page: a visible window when there is a display, and headless with `ahb login-view` steps
when there is none. It restarts Chrome, so the previous session is gone.

`login_finish` verifies that an account is signed in and restarts Chrome headless on the same profile. It returns
`{"ok": false, "detail": "no Google account signed in yet"}` if the human has not finished yet — that is not an error, it
is "ask the user to try again".

## Reading a page

### `navigate(url, wait_ms=3000)`

Opens the URL in the agent tab and returns the **final** URL (after redirects), the title and the first 3000 characters of
visible text. `wait_ms` is the pause after `domcontentloaded`; a Looker report needs a longer wait than a static page.

### `page_snapshot(max_elements=150)`

Returns visible text plus the interactive elements as numbered lines:

```
[1] <button type=button> Export
[2] <a> WFP food prices -> https://datastudio.google.com/reporting/…
```

Each element gets a `data-agent-ref` attribute, which is what `click` and `type_text` take as `ref`.

**Refs are only valid for the snapshot that produced them.** Navigate, click anything that changes the page, or open a
menu, and take a new snapshot.

### `click(ref=None, selector=None)` and `type_text(text, ref=None, selector=None, submit=False)`

`ref` is a number from the last snapshot. `selector` is a Playwright selector: `text=Download`, `css=.toolbar button`,
`role=button[name="Share"]`. Give one, not both. `submit=True` presses Enter afterwards.

`type_text` fills a field. It is for search boxes and forms — never for credentials.

### `press_key(key)`, `screenshot(full_page=False)`, `evaluate(script)`

`press_key` sends `Enter`, `Escape`, `PageDown`, `Control+A`. `screenshot` returns a PNG (use it for charts, canvases and
anything the text snapshot cannot describe). `evaluate` runs JavaScript in the page and returns the result as JSON — it is
the tool for structured extraction (read a table out of the DOM, pull a JSON blob out of a script tag).

## Looker

### `looker_export(report_url, out_dir=None, page_size=50000)`

Exports every row of every datasource behind a Looker Studio report to CSV.

```json
{
  "out_dir": "C:\\Users\\me\\.agent-headless-browser\\out\\looker",
  "datasources": [
    {
      "datasource_id": "f5b265ab-9039-4f9c-a04c-262d89e95e38",
      "name": "wfp_food_prices_idn.csv",
      "csv_path": "…\\wfp_food_prices_idn.csv",
      "grouped_rows": 212117,
      "raw_rows": 212117,
      "columns": ["date", "admin1", "…"]
    }
  ]
}
```

Two files per datasource: `<name>.csv` with the raw rows and `<name>_grouped.csv` with a `record_count` column. The counts
come from the report's own Record Count metric, so grouped and raw differ when the report groups.

Three things decide what you get:

- **Only datasources used on the page you open.** For a multi-page report, call it once per page URL
  (`…/reporting/<id>/page/<pageId>`).
- **Only what the signed-in account can see.** A viewer does not get the hidden rows.
- **Nothing is filtered out.** The replay asks for all dimensions plus Record Count with the filters cleared, so the export
  is the whole datasource, not the report's current filter.

`out_dir` defaults to `<AHB_OUT_DIR>/looker`. Pass an explicit path when the user asked for one — and make it **absolute**: a
relative path is resolved against the MCP server's working directory, which is not the user's session directory. The tool
creates the directory if it does not exist.

### `health_check(url=None)`

Loads a page that needs the session in a fresh tab and reports `{ts, ok, status, landed}`. For a Looker URL it waits for
the report's own `batchedDataV2` call and passes only if that returns 200 — a page that loads but whose data call fails
is **not** healthy. The record is appended to `<AHB_OUT_DIR>/session_health.jsonl`.

## Error strings you will meet

| Message | Meaning | Do |
|---|---|---|
| `agent Chrome is not running on http://127.0.0.1:9333; start it first` | No Chrome on the CDP port | `browser_start` |
| `Google Chrome not found (checked …)` | Chrome is not installed where the code looks | Install Google Chrome (not Chromium) |
| `not signed in: the report redirected to Google sign-in` | The session is gone | `login_begin` → human signs in → `login_finish` |
| `the report issued no data requests (no access, or nothing on this page)` | The page did not query any datasource | Check the page id, and that the account can see the report |
| `ChromeError: already owns port 9333` | A Chrome is already there | Reuse it, do not start a second one |
| `Timeout 15000ms exceeded` | The element was not there | New snapshot; the page may have changed |
