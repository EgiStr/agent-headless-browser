"""Dump every row of each datasource behind a Looker Studio report to CSV.

The report page itself is used as the authenticated client: we capture one real
batchedDataV2 request, then replay it from inside the page with all dimensions
of the datasource plus Record Count, so the server returns row-level data.
"""
import argparse
import asyncio
import copy
import csv
import json
import pathlib
import re
import time

from agent_browser import open_context

DROP_HEADERS = {"cookie", "content-length", "host", "origin", "referer", "user-agent",
                "accept-encoding", "connection"}
RECORD_COUNT_PREFIX = "datastudio_record_count_system_field_id"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def strip_xssi(text: str):
    return json.loads(text[text.index("\n"):] if text.startswith(")]}'") else text)


def decode_table(table: dict) -> list[list]:
    """Column-oriented tableDataset -> list of rows, re-inserting nulls."""
    size = table.get("size", 0)
    cols = []
    for col in table.get("column", []):
        kind = next(k for k in col if k.endswith("Column"))
        values = col[kind].get("values", [])
        nulls = set(col.get("nullIndex", []))
        if len(values) == size:
            full = [None if i in nulls else v for i, v in enumerate(values)]
        else:
            assert len(values) + len(nulls) == size, (kind, len(values), len(nulls), size)
            it = iter(values)
            full = [None if i in nulls else next(it) for i in range(size)]
        cols.append(full)
    return [list(r) for r in zip(*cols)]


async def page_post(page, url, headers, body):
    return await page.evaluate(
        """async ([url, headers, body]) => {
            const r = await fetch(url, {method: 'POST', headers, body, credentials: 'include'});
            return [r.status, await r.text()];
        }""", [url, headers, json.dumps(body)])


def build_request(template: dict, ds_id: str, dims: list[dict], metric: str,
                  start: int, count: int, filters: list | None = None) -> dict:
    req = copy.deepcopy(template["dataRequest"][0])
    spec = req["datasetSpec"]
    spec["dataset"] = [{"datasourceId": ds_id, "revisionNumber": 0, "parameterOverrides": []}]
    fields = [{"name": f"qf_{i}", "datasetNs": "d0", "tableNs": "t0",
               "dataTransformation": {"sourceFieldName": d["name"]}} for i, d in enumerate(dims)]
    fields.append({"name": "qf_count", "datasetNs": "d0", "tableNs": "t0",
                   "dataTransformation": {"sourceFieldName": metric}})
    spec["queryFields"] = fields
    # Stable order across pages: sort on every dimension.
    spec["sortData"] = [{"sortColumn": {k: f[k] for k in ("name", "datasetNs", "tableNs", "dataTransformation")},
                         "sortDir": 0} for f in fields[:-1]]
    spec["paginateInfo"] = {"startRow": start, "rowsCount": count}
    spec["filters"] = filters or []
    spec["dateRanges"] = []
    req["retryHints"] = {"useClientControlledRetry": True, "isLastRetry": False, "retryCount": 0,
                         "originalRequestId": f"agent_{start}"}
    return {"dataRequest": [req]}


async def crawl(args):
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    async with open_context(args.mode, profile_dir=args.profile, cdp=args.cdp,
                            cookies_file=args.cookies, headless=not args.headful) as ctx:
        page = await ctx.new_page()
        captured, schemas, ds_names = {}, {}, {}

        async def on_request(req):
            if "batchedDataV2" in req.url and "tpl" not in captured:
                captured["tpl"] = (req.url, await req.all_headers(), json.loads(req.post_data))

        async def on_response(resp):
            try:
                if "/getSchema" in resp.url:
                    m = UUID.search(resp.request.post_data or "")
                    schemas[m.group(0) if m else resp.request.post_data] = strip_xssi(await resp.text())
                elif "/getClaimedDatasources" in resp.url:
                    for d in strip_xssi(await resp.text()).get("claimedDatasources", []):
                        info = d["basicEntityInfo"]
                        ds_names[info["id"]] = info.get("name", info["id"])
            except Exception as e:
                print("schema parse failed", e)

        page.on("request", on_request)
        page.on("response", on_response)
        for attempt in range(3):
            try:
                await page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
                break
            except Exception as e:
                print("goto retry", attempt, str(e)[:100]); await page.wait_for_timeout(3000)
        for _ in range(90):
            if "tpl" in captured and schemas:
                break
            await page.wait_for_timeout(1000)
        print("landed on:", page.url, "|", await page.title())
        await page.screenshot(path=str(out / f"screen_{args.mode}.png"))
        if "accounts.google.com" in page.url or "tpl" not in captured:
            raise SystemExit("NOT LOGGED IN or report did not issue data requests")

        url, headers, tpl = captured["tpl"]
        headers = {k: v for k, v in headers.items() if k.lower() not in DROP_HEADERS
                   and not k.startswith(":") and not k.lower().startswith("sec-")}

        summary = {}
        for ds_id, schema in schemas.items():
            s = schema["schema"]
            dims = s["dimensions"]
            metric = next(m["name"] for m in s["metrics"] if m["name"].startswith(RECORD_COUNT_PREFIX))
            header = [d["displayName"] for d in dims]
            rows, start, page_size, total = [], 1, args.page_size, None
            while True:
                status, text = await page_post(page, url, headers,
                                               build_request(tpl, ds_id, dims, metric, start, page_size))
                if status != 200:
                    raise SystemExit(f"HTTP {status}: {text[:500]}")
                resp = strip_xssi(text)
                sub = resp["dataResponse"][0]["dataSubset"][0]
                if "dataset" not in sub:
                    raise SystemExit(f"no dataset: {json.dumps(sub)[:800]}")
                table = sub["dataset"]["tableDataset"]
                total = table.get("totalCount", total)
                batch = decode_table(table)
                rows += batch
                print(f"  rows {start}..{start + len(batch) - 1} / grouped total {total}")
                if not batch or len(rows) >= int(total or 0):
                    break
                start += len(batch)
            # Expand GROUP BY back to raw rows using Record Count.
            raw = []
            for r in rows:
                raw += [r[:-1]] * int(r[-1])
            name = (args.name if len(schemas) == 1 and args.name
                    else re.sub(r"[^\w.-]+", "_", ds_names.get(ds_id, ds_id)).removesuffix(".csv"))
            with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(header); w.writerows(raw)
            with open(out / f"{name}_grouped.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(header + ["record_count"]); w.writerows(rows)
            summary[ds_id] = {"name": ds_names.get(ds_id), "csv": str(out / f"{name}.csv"),
                              "grouped_rows": len(rows), "raw_rows": len(raw), "columns": header}
        await page.close()
        (out / f"summary_{args.mode}.json").write_text(json.dumps(
            {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "url": args.url, "datasources": summary}, indent=1))
        print(json.dumps(summary, indent=1))
        return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="https://lookerstudio.google.com/reporting/<report-id>")
    ap.add_argument("--mode", choices=["attach", "headless"], default="attach")
    ap.add_argument("--profile", default="profiles/agent")
    ap.add_argument("--cdp", help="ws:// or http:// CDP endpoint (default: desktop Chrome DevToolsActivePort)")
    ap.add_argument("--cookies", help="cookie JSON to seed a headless profile")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--page-size", type=int, default=50000)
    ap.add_argument("--name", help="CSV base name (single-datasource reports); default: datasource name")
    ap.add_argument("--out", default="out")
    asyncio.run(crawl(ap.parse_args()))
