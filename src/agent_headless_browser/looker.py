"""Export every row of each datasource behind a Looker Studio report.

The report tab itself is the authenticated client. We capture one real
``batchedDataV2`` request, then replay it from inside the page asking for all
dimensions plus Record Count, sorted on every dimension and paged, so the
server returns row-level data. Counts are expanded back into raw rows.

These are undocumented internal endpoints; expect breakage when Google changes them.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from patchright.async_api import BrowserContext, Page, Request, Response

log = logging.getLogger(__name__)

RECORD_COUNT_PREFIX = "datastudio_record_count_system_field_id"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DROP_HEADERS = {
    "cookie",
    "content-length",
    "host",
    "origin",
    "referer",
    "user-agent",
    "accept-encoding",
    "connection",
}


class LookerError(RuntimeError):
    """The report could not be read (not signed in, no access, or the API changed)."""


@dataclass
class DatasourceExport:
    datasource_id: str
    name: str
    csv_path: str
    grouped_rows: int
    raw_rows: int
    columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_xssi(text: str) -> Any:
    """Parse a Google JSON body that starts with the ``)]}'`` anti-XSSI prefix."""
    return json.loads(text[text.index("\n") :] if text.startswith(")]}'") else text)


def decode_table(table: dict[str, Any]) -> list[list[Any]]:
    """Column-oriented ``tableDataset`` into rows, re-inserting nulls.

    A column either has ``size`` values (nulls kept in place) or omits the nulls
    listed in ``nullIndex``; anything else means the format changed.
    """
    size = table.get("size", 0)
    columns: list[list[Any]] = []
    for col in table.get("column", []):
        kind = next(k for k in col if k.endswith("Column"))
        values = col[kind].get("values", [])
        nulls = set(col.get("nullIndex", []))
        if len(values) == size:
            columns.append([None if i in nulls else v for i, v in enumerate(values)])
        elif len(values) + len(nulls) == size:
            it = iter(values)
            columns.append([None if i in nulls else next(it) for i in range(size)])
        else:
            raise LookerError(f"column {kind}: {len(values)} values + {len(nulls)} nulls != {size} rows")
    return [list(row) for row in zip(*columns, strict=True)]


def expand_counts(rows: list[list[Any]]) -> list[list[Any]]:
    """Rows whose last cell is Record Count, repeated that many times without the count."""
    out: list[list[Any]] = []
    for row in rows:
        out.extend([row[:-1]] * int(row[-1]))
    return out


def build_request(
    template: dict[str, Any], datasource_id: str, dimensions: list[str], metric: str, start: int, count: int
) -> dict[str, Any]:
    """A ``batchedDataV2`` body for one page of all dimensions + ``metric``."""
    req = copy.deepcopy(template["dataRequest"][0])
    spec = req["datasetSpec"]
    spec["dataset"] = [{"datasourceId": datasource_id, "revisionNumber": 0, "parameterOverrides": []}]
    fields = [
        {
            "name": f"qf_{i}",
            "datasetNs": "d0",
            "tableNs": "t0",
            "dataTransformation": {"sourceFieldName": dim},
        }
        for i, dim in enumerate(dimensions)
    ]
    fields.append(
        {
            "name": "qf_count",
            "datasetNs": "d0",
            "tableNs": "t0",
            "dataTransformation": {"sourceFieldName": metric},
        }
    )
    spec["queryFields"] = fields
    # Sorting on every dimension keeps page boundaries stable.
    spec["sortData"] = [{"sortColumn": f, "sortDir": 0} for f in fields[:-1]]
    spec["paginateInfo"] = {"startRow": start, "rowsCount": count}
    spec["filters"] = []
    spec["dateRanges"] = []
    req["retryHints"] = {
        "useClientControlledRetry": True,
        "isLastRetry": False,
        "retryCount": 0,
        "originalRequestId": f"ahb_{start}",
    }
    return {"dataRequest": [req]}


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name).removesuffix(".csv") or "datasource"


async def _post_from_page(
    page: Page, url: str, headers: dict[str, str], body: dict[str, Any]
) -> tuple[int, str]:
    status, text = await page.evaluate(
        """async ([url, headers, body]) => {
            const r = await fetch(url, {method: 'POST', headers, body, credentials: 'include'});
            return [r.status, await r.text()];
        }""",
        [url, headers, json.dumps(body)],
    )
    return int(status), str(text)


async def export_report(
    context: BrowserContext, report_url: str, out_dir: Path, page_size: int = 50_000
) -> list[DatasourceExport]:
    """Write ``<datasource>.csv`` and ``<datasource>_grouped.csv`` for each datasource on the page."""
    page = await context.new_page()
    template: dict[str, Any] = {}
    schemas: dict[str, Any] = {}
    names: dict[str, str] = {}

    async def on_request(req: Request) -> None:
        if "batchedDataV2" in req.url and "body" not in template and req.post_data:
            template.update(url=req.url, headers=await req.all_headers(), body=json.loads(req.post_data))

    async def on_response(resp: Response) -> None:
        try:
            if "/getSchema" in resp.url:
                match = UUID.search(resp.request.post_data or "")
                schemas[match.group(0) if match else resp.url] = strip_xssi(await resp.text())["schema"]
            elif "/getClaimedDatasources" in resp.url:
                for ds in strip_xssi(await resp.text()).get("claimedDatasources", []):
                    info = ds["basicEntityInfo"]
                    names[info["id"]] = info.get("name", info["id"])
        except Exception:
            log.warning("could not parse %s", resp.url, exc_info=True)

    page.on("request", on_request)
    page.on("response", on_response)
    try:
        await page.goto(report_url, wait_until="domcontentloaded", timeout=90_000)
        for _ in range(90):
            if "body" in template and schemas:
                break
            await asyncio.sleep(1)
        if "accounts.google.com" in page.url:
            raise LookerError("not signed in: the report redirected to Google sign-in")
        if "body" not in template:
            raise LookerError("the report issued no data requests (no access, or nothing on this page)")

        headers = {
            k: v
            for k, v in template["headers"].items()
            if k.lower() not in DROP_HEADERS and not k.startswith(":") and not k.lower().startswith("sec-")
        }
        results = []
        for ds_id, schema in schemas.items():
            results.append(
                await _export_datasource(
                    page, template, headers, ds_id, schema, names.get(ds_id, ds_id), out_dir, page_size
                )
            )
        return results
    finally:
        await page.close()


async def _export_datasource(
    page: Page,
    template: dict[str, Any],
    headers: dict[str, str],
    ds_id: str,
    schema: dict[str, Any],
    name: str,
    out_dir: Path,
    page_size: int,
) -> DatasourceExport:
    dims = schema["dimensions"]
    metric = next(m["name"] for m in schema["metrics"] if m["name"].startswith(RECORD_COUNT_PREFIX))
    header = [d["displayName"] for d in dims]
    rows: list[list[Any]] = []
    start, total = 1, 0
    while True:
        body = build_request(template["body"], ds_id, [d["name"] for d in dims], metric, start, page_size)
        status, text = await _post_from_page(page, template["url"], headers, body)
        if status != 200:
            raise LookerError(f"batchedDataV2 HTTP {status}: {text[:300]}")
        subset = strip_xssi(text)["dataResponse"][0]["dataSubset"][0]
        if "dataset" not in subset:
            raise LookerError(f"no dataset in response: {json.dumps(subset)[:300]}")
        table = subset["dataset"]["tableDataset"]
        total = int(table.get("totalCount", total))
        batch = decode_table(table)
        rows += batch
        log.info("%s: rows %d..%d of %d", name, start, start + len(batch) - 1, total)
        if not batch or len(rows) >= total:
            break
        start += len(batch)

    raw = expand_counts(rows)
    # Hundreds of thousands of rows: write off the event loop so the MCP server stays responsive.
    csv_path = await asyncio.to_thread(write_csvs, out_dir, safe_name(name), header, rows, raw)
    return DatasourceExport(ds_id, name, str(csv_path), len(rows), len(raw), header)


def write_csvs(
    out_dir: Path, base: str, header: list[str], grouped: list[list[Any]], raw: list[list[Any]]
) -> Path:
    """``<base>.csv`` with raw rows and ``<base>_grouped.csv`` with a record_count column."""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{base}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(raw)
    with (out_dir / f"{base}_grouped.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([*header, "record_count"])
        writer.writerows(grouped)
    return csv_path
