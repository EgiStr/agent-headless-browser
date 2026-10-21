---
name: looker-report-export
description: Use when exporting the rows behind a Looker Studio / Data Studio report to CSV, or when the exported row count, column count or file has to be checked against what the report shows before the numbers are handed to the user. Also use when an export comes back with no datasources, with fewer rows than the report displays, or with a CSV whose first data row looks like a header.
---

# Exporting a Looker Studio report

`looker_export` pulls every row of every datasource behind a report to CSV. It sees what the signed-in account can see, and
only the datasources used on the page you open.

**Core principle: export, then check the number against the report before you report it.**

## The workflow

1. `browser_status` — Chrome up, an account signed in. If not, `browser_start`, or `login_begin` → the human signs in →
   `login_finish`. (Full flow in the `agent-browser` skill.)
2. `looker_export(report_url, out_dir)` where `out_dir` is what the user asked for. The tool opens the report itself; you do
   not have to navigate first. **Give it an absolute path**: a relative one is resolved against the MCP server's working
   directory, not the user's session directory, so the files land somewhere unexpected. The tool creates the directory.
3. Read the answer: one entry per datasource, with `raw_rows`, `grouped_rows` and `columns`.
4. **Verify** (below): compare a total from the export with a total the report itself shows.
5. Report the numbers, per datasource, with where each came from.

## What the tool returns

| Field | Meaning |
|---|---|
| `name` | The datasource name, used for the file name |
| `csv_path` | The raw CSV it wrote |
| `columns` | The column names, in order — this is the column count |
| `raw_rows` | Rows in `<name>.csv`: one per record, all dimensions |
| `grouped_rows` | Rows in `<name>_grouped.csv`, which also has a `record_count` column (so `len(columns) + 1` columns) |

`raw_rows` is the datasource's full row count, taken from the server's own total — not a page of it. `raw_rows` and
`grouped_rows` differ only when the report groups rows; otherwise they are equal.

## The numbers you may state

| You may say | Where it comes from |
|---|---|
| rows of a table | `raw_rows` from the tool, or the rows counted in the CSV |
| columns of a table | `columns` from the tool, or the header row of the CSV |
| total rows | the sum of the rows of the tables you exported |
| "this is the whole datasource" | only after comparing with a total the report shows |

**Never add the columns of two tables together.** "43 columns" across three tables with different columns means nothing.
Report rows and columns per datasource, plus a total row count.

**Never state a number you did not read.** If you could not verify something, say which number it is and that it comes from
the export tool only.

## Verifying

**With a shell or a file tool**, count the rows yourself and compare:

```python
import csv
from pathlib import Path

path = Path("wfp_food_prices_idn.csv")
with path.open(encoding="utf-8", newline="") as fh:
    reader = csv.reader(fh)
    header = next(reader)
    rows = sum(1 for _ in reader)
print(len(header), "columns,", rows, "rows")
```

Use a CSV parser, not `split(",")`: a quoted field may contain a comma or a newline. Skip the descriptor row first (below).

**Compare with the report.** Open the report with `navigate(report_url)` and read a scorecard or table total from
`page_snapshot` or `evaluate`. A report with a per-region scorecard is the good case: check every region, not one.

**Without a shell**, you cannot count the file yourself. Say so: report the tool's `raw_rows` and `columns`, and mark them as
coming from the export tool rather than from an independent count. That is honest; a guessed number is not.

## More than one page

Only the datasources **used on the opened page** are captured. For a multi-page report, export each page:

```
https://datastudio.google.com/u/0/reporting/<id>            → page 1
https://datastudio.google.com/u/0/reporting/<id>/page/<p2>  → page 2
```

Find the page ids from the report's own tabs (`page_snapshot` on the report, or the `page=` parameter in the URL). Give each
page its own `out_dir`, or its files overwrite each other. When you report, say which pages were exported.

## The descriptor row

The first data row of a WFP HDX CSV is the source file's field descriptor:

```
, #adm1+name, #adm2+name, #loc+market+name, #geo+lat, #geo+lon, #item+type, …
```

It is literally in the uploaded file — it is how HDX marks the column headers. Looker Studio does not add it, and it is not
an export artifact. Each cell names the source field and what it holds (`adm1+name` = the name of the `adm1` field,
`geo+lat` = the latitude of the `geo` field). The `date` cell of that row is empty, so the `#` markers have to be looked
for in the dimension columns. Skip the row by looking at `admin1`:

```python
rows = [r for r in csv.DictReader(f) if not r["admin1"].startswith("#")]
```

Other datasets mark their descriptor rows differently: check the first rows of the file instead of assuming.

**The arithmetic:** the exported file has `raw_rows + 1` lines — one header the exporter wrote, then the rows. The
descriptor row is one of those rows and is counted in `raw_rows`, so the number of actual records is `raw_rows - 1`. The
reference export is 212,117 rows = 212,116 records + 1 descriptor row, in 212,118 lines.

## The reference export

The report used while testing, as a yardstick for "does this look right":

| | |
|---|---|
| Report | `Test FoodPrice Report`, page `/page/CGBgD` |
| Datasource | `wfp_food_prices_idn.csv` — the public WFP HDX food prices CSV |
| Rows | 212,117 (212,116 records + 1 descriptor row), in 14 columns |
| Files | 30.70 MB raw, 31.10 MB grouped |
| SHA-256 | raw `2D2EA0E9530DEBF8D53492A271DE182E870B4A3126E84465BCE8FFC85F001B04`, grouped `5E5EBAFD72C09DE02264DE784A2F3223E5E852CB5CEBA9C121FBEE6B20FF675A` |
| Cross-check | all 18 per-province Record Counts matched the report UI exactly (Jawa Tengah 24,736 · Jawa Barat 19,334 · Jawa Timur 19,251 · … Bali 4,133); 35 provinces plus 1,139 "National Average" rows; priceflag 168,344 aggregate / 42,633 forecast / 1,139 actual; 8 categories, 31 commodities, 216 markets, 206 distinct dates (2007-01-15 to 2024-02-15) |

The two hashes are stable: the same report exported twice produced byte-identical files. Use them to tell "the export
changed" from "the data changed".

## When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `not signed in: the report redirected to Google sign-in` | The session died | Re-login, then export again |
| `the report issued no data requests` | Wrong page id, or no access to the report | Open the report by hand, read its tab ids, check sharing |
| `datasources: []` although the report has charts | A cached tile rendered without a data call, or a `/getSchema` response was fetched but could not be parsed — `looker.py` swallows that error, so the list comes back empty instead of raising | Export the exact page URL; `health_check(report_url)` is the discriminator. For the parse branch run `ahb -v looker-export <url> --out <dir>`: the warnings only reach stderr |
| `column …: N values + M nulls != K rows` | Google changed the response format | The decoder in `looker.py` needs updating; the format is in [docs/findings.md](../../../docs/findings.md) |
| Fewer rows than the report shows | The report filters by default | The export ignores filters; compare with an unfiltered total |
| An empty file | The page had not finished loading | `navigate` the page with a longer wait, then export again |

## Red flags — stop and think

- You are about to report a row count you did not read from the tool or the file.
- You are about to add the columns of several tables together.
- You are about to claim the export is complete without comparing it with the report's own total.
- You are about to re-export with a bigger `page_size` because a row count "looks like a page limit". `raw_rows` is the
  server's total; `page_size` only decides how many rows come back per request.
- You are about to export several pages into one directory.
