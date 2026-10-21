# Exporting a Looker Studio report

`looker_export` pulls **every row of every datasource behind a report** to CSV. It is the one tool in this project that
needs the signed-in session, and the one that most often needs a second look before the numbers are reported.

## The workflow

```text
browser_status          → is Chrome up, is an account signed in?
  └─ not running       → browser_start
  └─ no account        → login_begin → the human signs in → login_finish
looker_export(url)      → writes <datasource>.csv and <datasource>_grouped.csv
verify the numbers      → against a total the report itself shows
report to the user      → with the numbers and where they came from
```

## What you get

Per datasource, two files in the output directory:

| File | Content |
|---|---|
| `<name>.csv` | One row per record, all dimensions, header row first |
| `<name>_grouped.csv` | The same rows as the report grouped them, plus a `record_count` column |

`raw_rows` and `grouped_rows` in the tool's answer are the row counts of those two files. They are equal when nothing was
grouped (a report with no grouping, or a Record Count of 1 everywhere). The grouped file has one column more than the raw
one: `len(columns) + 1`, because of `record_count`.

## Verifying before you report

An export that returns a number is not the same as an export that is correct. Check at least one total against the report
itself:

1. Open the report with `navigate(report_url)` and read a scorecard or a table total from `page_snapshot` or `evaluate`.
2. Compare that total with the sum of the rows you exported.
3. If they differ, say so. Do not report the row count as "the total".

A report with a per-region scorecard is the good case: check every region, not one. The reference export below was
validated that way — 18 per-province counts, all matching.

```python
# sum the grouped file to compare with the report's own numbers
import csv
from collections import Counter
from pathlib import Path

rows = list(csv.DictReader(Path("wfp_food_prices_idn_grouped.csv").open(encoding="utf-8")))
total = sum(int(r["record_count"]) for r in rows)
by_admin1 = Counter()
for r in rows:
    by_admin1[r["admin1"] or "National Average"] += int(r["record_count"])
print(total, by_admin1.most_common(5))
```

## The reference export

The report used while testing, and what it produced — a yardstick to compare against when something looks off:

| | |
|---|---|
| Report | `Test FoodPrice Report`, page `/page/CGBgD` |
| Datasource | `wfp_food_prices_idn.csv` (`f5b265ab-9039-4f9c-a04c-262d89e95e38`), the public WFP HDX food prices CSV |
| Rows | **212,117** (212,116 records + 1 descriptor row), in 14 columns (`date, admin1, admin2, market, latitude, longitude, category, commodity, unit, priceflag, pricetype, currency, price, usdprice`) |
| Files | 30.70 MB raw, 31.10 MB grouped |
| SHA-256 | raw `2D2EA0E9530DEBF8D53492A271DE182E870B4A3126E84465BCE8FFC85F001B04`, grouped `5E5EBAFD72C09DE02264DE784A2F3223E5E852CB5CEBA9C121FBEE6B20FF675A` |
| Cross-check | all 18 per-province Record Counts matched the report UI exactly (Jawa Tengah 24,736 · Jawa Barat 19,334 · Jawa Timur 19,251 · … Bali 4,133); 35 provinces plus 1,139 "National Average" rows; priceflag: 168,344 aggregate / 42,633 forecast / 1,139 actual; 8 categories, 31 commodities, 216 markets, 206 distinct dates (2007-01-15 to 2024-02-15) |

The two hashes are stable: the same report exported twice, hours apart, produced byte-identical files. Use them to tell
"the export changed" from "the data changed".

### The first data row is a descriptor row

Row 1 of the raw CSV is the source file's field-descriptor row:

```
, #adm1+name, #adm2+name, #loc+market+name, #geo+lat, #geo+lon, #item+type,
#item+name, #item+unit, #item+price+flag, #item+price+type, #currency, #value, #value+usd
```

That row is literally in the uploaded WFP CSV — it is how HDX marks the column headers. It is not an export artifact and it
is not something Looker adds. Each cell names the source field and what it holds (`adm1+name` = the name of the `adm1`
field, `geo+lat` = the latitude of the `geo` field, `item+price+flag` = the price flag of the `item` field). The `date`
cell of that row is empty, so the `#` markers have to be looked for in the dimension columns. Skip the row by looking at
`admin1`:

```python
rows = [r for r in csv.DictReader(f) if not r["admin1"].startswith("#")]
```

Other datasets mark their descriptor rows differently, so check the first rows of the file rather than assuming.

The file has `raw_rows + 1` lines: one header the exporter wrote, then the rows. The descriptor row is counted in
`raw_rows`, so the reference export is 212,117 rows = 212,116 records + 1 descriptor row, in 212,118 lines.

## Multi-page reports

Only the datasources **used on the page you open** are captured. A three-page report therefore needs three calls:

```
https://datastudio.google.com/u/0/reporting/<id>            → page 1's datasources
https://datastudio.google.com/u/0/reporting/<id>/page/<p2>  → page 2's datasources
```

Get the page ids from the report's own tabs (`page_snapshot` on the report, or the `page=` parameter in the URL). Give each
page its own `out_dir`, or name the files after the page, so the exports do not overwrite each other.

## How it works, and why it can break

The report tab is the authenticated client. `looker_export` opens the report, captures one real `batchedDataV2` request
(url, headers, body), and replays it **from inside the page** with:

- every dimension of the datasource, plus the Record Count metric;
- the report's filters cleared and no date range;
- a sort on every dimension, so page boundaries stay stable;
- `page_size` rows per page (50,000 by default).

Counts are expanded back into rows and nulls are re-inserted from `nullIndex`.

That endpoint is **undocumented**. Google can change the request shape, the field names or the response format without
notice, and the export stops working. The failure is loud: `column <kind>: N values + M nulls != K rows` or
`no dataset in response`. Expect to fix `looker.py` when that happens; the experiments are in [findings.md](findings.md).

## Cost and limits

| | |
|---|---|
| 212,117 rows | 99 s on a Linux container, 120 s on Windows |
| Memory | The rows are held in the Python process while they are written, so peak RAM scales with the datasource. A million-row CSV is a few hundred MB. |
| Rights | You get what the signed-in account can see. A viewer's export is the viewer's view. |
| Filters | Ignored on purpose: the export is the whole datasource. The report's own filters do not apply. |
| Page size | `page_size` is rows per request. Lower it if the request times out; raise it for fewer round trips. |

## When the export returns nothing

| Symptom | Cause | Fix |
|---|---|---|
| `not signed in: the report redirected to Google sign-in` | The session died | `login_begin`, the human signs in, `login_finish` |
| `the report issued no data requests (no access, or nothing on this page)` | Wrong page id, or the account cannot see the report | Open the report by hand and read its tab ids; check the sharing settings |
| `datasources: []` but the report has charts | The report renders from a cached tile, or the data call had not fired yet | Retry on the exact page URL; check `health_check(report_url)` |
| `HTTP 403` on the replay | The captured headers went stale | Re-run the export so a fresh request is captured |
| Row count much lower than the report's total | The report filters by default | The export ignores filters; compare against an unfiltered total |
