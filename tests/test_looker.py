import json

import pytest

from agent_headless_browser.looker import (
    LookerError,
    build_request,
    decode_table,
    expand_counts,
    safe_name,
    strip_xssi,
)

TEMPLATE = {
    "dataRequest": [
        {
            "requestContext": {"reportContext": {"reportId": "r", "pageId": "p", "componentId": "c"}},
            "datasetSpec": {
                "dataset": [{"datasourceId": "old", "revisionNumber": 3}],
                "queryFields": [],
                "sortData": [],
                "paginateInfo": {"startRow": 1, "rowsCount": 100},
                "filters": [{"x": 1}],
                "dateRanges": [{"y": 2}],
                "timezone": "Asia/Jakarta",
            },
        }
    ]
}


def test_strip_xssi_removes_prefix():
    assert strip_xssi(')]}\'\n\n{"a": 1}') == {"a": 1}


def test_strip_xssi_plain_json():
    assert strip_xssi('{"a": 1}') == {"a": 1}


def test_decode_table_values_in_place():
    table = {
        "size": 3,
        "column": [
            {"stringColumn": {"values": ["a", "b", "c"]}, "nullIndex": [1]},
            {"longColumn": {"values": ["1", "2", "3"]}, "nullIndex": []},
        ],
    }
    assert decode_table(table) == [["a", "1"], [None, "2"], ["c", "3"]]


def test_decode_table_nulls_omitted_from_values():
    # Nulls missing from values must be re-inserted, or every later value shifts up a row.
    table = {
        "size": 4,
        "column": [
            {"stringColumn": {"values": ["a", "d"]}, "nullIndex": [1, 2]},
            {"doubleColumn": {"values": [1.0, 2.0, 3.0, 4.0]}},
        ],
    }
    assert decode_table(table) == [["a", 1.0], [None, 2.0], [None, 3.0], ["d", 4.0]]


def test_decode_table_rejects_inconsistent_lengths():
    table = {"size": 3, "column": [{"stringColumn": {"values": ["a"]}, "nullIndex": [0]}]}
    with pytest.raises(LookerError, match="1 values"):
        decode_table(table)


def test_decode_table_empty():
    assert decode_table({"size": 0, "column": []}) == []


def test_expand_counts_restores_duplicates():
    rows = [["x", "1"], ["y", "3"]]
    assert expand_counts(rows) == [["x"], ["y"], ["y"], ["y"]]


def test_build_request_asks_for_every_dimension_sorted_and_paged():
    body = build_request(TEMPLATE, "ds-1", ["_a_", "_b_"], "count_metric", start=50_001, count=50_000)
    spec = body["dataRequest"][0]["datasetSpec"]

    assert spec["dataset"] == [{"datasourceId": "ds-1", "revisionNumber": 0, "parameterOverrides": []}]
    sources = [f["dataTransformation"]["sourceFieldName"] for f in spec["queryFields"]]
    assert sources == ["_a_", "_b_", "count_metric"]
    assert [s["sortColumn"]["dataTransformation"]["sourceFieldName"] for s in spec["sortData"]] == [
        "_a_",
        "_b_",
    ]
    assert spec["paginateInfo"] == {"startRow": 50_001, "rowsCount": 50_000}
    assert spec["filters"] == []
    assert spec["dateRanges"] == []
    # The report context the server may validate is kept.
    assert body["dataRequest"][0]["requestContext"] == TEMPLATE["dataRequest"][0]["requestContext"]
    assert spec["timezone"] == "Asia/Jakarta"


def test_build_request_does_not_mutate_template():
    before = json.dumps(TEMPLATE, sort_keys=True)
    build_request(TEMPLATE, "ds", ["_a_"], "m", 1, 10)
    assert json.dumps(TEMPLATE, sort_keys=True) == before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("wfp_food_prices_idn.csv", "wfp_food_prices_idn"),
        ("Sales / 2024 (EU)", "Sales_2024_EU_"),
        ("", "datasource"),
    ],
)
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected
