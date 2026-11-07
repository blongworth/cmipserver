import check_esgf
import polars as pl
import pytest
from conftest import ESGF_URL, FakeIndex, esgf_docs

from whoi_cmip_reorg_code.esgf import (
    CachedFetcher,
    compare,
    file_records,
    local_datasets,
    query_datasets,
    search_all,
)


@pytest.fixture(scope="module")
def esgf_out(messy_scan, fake_esgf, tmp_path_factory):
    out = tmp_path_factory.mktemp("esgf")
    rc = check_esgf.main(
        [
            "--scan",
            str(messy_scan),
            "--out",
            str(out),
            "--index-url",
            ESGF_URL,
            "--min-interval",
            "0",
        ],
        fetcher=fake_esgf,
    )
    assert rc == 0
    return out


def _status(df, key, store):
    return df.filter(rel_path=store.rel(key)).row(0, named=True)


def test_compare_statuses(esgf_out, messy_store):
    df = pl.read_parquet(esgf_out / "esgf_compare.parquet")
    s = messy_store
    assert _status(df, "good_0", s)["esgf_status"] == "match"
    assert _status(df, "empty", s)["esgf_status"] == "size_mismatch"
    assert _status(df, "truncated", s)["esgf_status"] == "size_mismatch"
    for k in ("fx", "text", "mismatch"):
        assert _status(df, k, s)["esgf_status"] == "dataset_not_on_esgf"
    v1 = _status(df, "v1", s)
    assert (v1["esgf_status"], v1["superseded"], v1["latest_version"]) == (
        "match",
        True,
        "v20200101",
    )
    assert _status(df, "v2", s)["superseded"] is False
    assert _status(df, "overlap_0", s)["retracted"] is True
    flat = _status(df, "flat", s)
    assert (flat["esgf_status"], flat["esgf_version"], flat["version"]) == (
        "match",
        "v20190726",
        None,
    )
    assert flat["esgf_url"].startswith("https://data.example.invalid/")
    assert flat["esgf_checksum_type"] == "SHA256"


def test_missing_local(esgf_out):
    m = pl.read_parquet(esgf_out / "esgf_missing_local.parquet")
    assert m["name"].to_list() == [
        "pr_Amon_CESM2_historical_r1i1p1f1_gn_190001-194912.nc"
    ]


def test_cache_reused(messy_scan, fake_esgf, esgf_out):
    n = len(fake_esgf.calls)
    check_esgf.main(
        ["--scan", str(messy_scan), "--out", str(esgf_out), "--index-url", ESGF_URL],
        fetcher=fake_esgf,
    )
    assert len(fake_esgf.calls) == n  # everything came from the cache


def test_compare_only_needs_no_network(messy_scan, esgf_out):
    assert (
        check_esgf.main(
            ["--scan", str(messy_scan), "--out", str(esgf_out), "--compare-only"]
        )
        == 0
    )


def test_not_queried(messy_scan, fake_esgf, tmp_path):
    check_esgf.main(
        [
            "--scan",
            str(messy_scan),
            "--out",
            str(tmp_path),
            "--index-url",
            ESGF_URL,
            "--source-id",
            "GFDL-ESM4",
            "--min-interval",
            "0",
        ],
        fetcher=fake_esgf,
    )
    df = pl.read_parquet(tmp_path / "esgf_compare.parquet")
    assert set(df.filter(source_id="CESM2")["esgf_status"]) == {"not_queried"}
    assert df.filter(source_id="GFDL-ESM4")["esgf_status"].to_list() == ["match"]


def test_query_errors_recorded(messy_scan):
    import requests

    class Down:
        def get(self, url, params):
            raise requests.ConnectionError("no route")

    lf = pl.scan_parquet(messy_scan / "holdings_all.parquet")
    todo = local_datasets(lf).head(2)
    ds, fs, errs = query_datasets(Down(), ESGF_URL, todo.iter_rows(named=True))
    assert (ds.height, fs.height, errs.height) == (0, 0, 2)
    assert "no route" in errs["error"][0]


def test_paging():
    files = [(f"f{i}.nc", i, "x") for i in range(1200)]
    idx = FakeIndex(esgf_docs("S", "Amon", "tas", "v1", files))
    docs = search_all(
        idx,
        ESGF_URL,
        {
            "type": "File",
            "source_id": "S",
            "experiment_id": "historical",
            "member_id": "r1i1p1f1",
            "table_id": "Amon",
            "variable_id": "tas",
            "grid_label": "gn",
        },
    )
    assert len(docs) == 1200
    assert len(idx.calls) == 3


def test_replicas_collapse():
    docs = esgf_docs("S", "Amon", "tas", "v1", [("a.nc", 1, "x")])
    rep = {**docs[1], "replica": True, "data_node": "mirror", "url": []}
    facets = {
        k: docs[0][k][0]
        for k in (
            "source_id",
            "experiment_id",
            "member_id",
            "table_id",
            "variable_id",
            "grid_label",
        )
    }
    (r,) = file_records(facets, [rep, docs[1]])
    assert r["n_replicas"] == 2
    assert r["data_node"] == "data.example.invalid"  # the original wins
    assert r["url"].startswith("https://")


def test_rate_limit():
    t = [0.0]
    slept = []

    class Inner:
        def get(self, url, params):
            return {"n": params["i"]}

    cf = CachedFetcher(
        Inner(),
        None,
        min_interval=2.0,
        clock=lambda: t[0],
        sleep=lambda s: slept.append(s),
    )
    cf.get("u", {"i": 1})
    t[0] = 0.5
    cf.get("u", {"i": 2})
    cf.get("u", {"i": 1})  # cached: no wait, no call
    assert slept == [1.5]
    assert cf.n_live == 2


def test_compare_size_match_preferred_over_latest():
    holdings = pl.LazyFrame(
        {
            "path": ["/x/a.nc"],
            "rel_path": ["x/a.nc"],
            "file_type": ["file"],
            "facet_parse_ok": [True],
            "source_id": ["S"],
            "experiment_id": ["historical"],
            "member_id": ["r1i1p1f1"],
            "table_id": ["Amon"],
            "variable_id": ["tas"],
            "grid_label": ["gn"],
            "version": [None],
            "name": ["a.nc"],
            "size_bytes": [10],
        },
        schema_overrides={"version": pl.String},
    )
    from whoi_cmip_reorg_code.esgf import dataset_records

    facets = {
        "source_id": "S",
        "experiment_id": "historical",
        "member_id": "r1i1p1f1",
        "table_id": "Amon",
        "variable_id": "tas",
        "grid_label": "gn",
    }
    old = esgf_docs("S", "Amon", "tas", "v1", [("a.nc", 10, "old")], latest=False)
    new = esgf_docs("S", "Amon", "tas", "v2", [("a.nc", 11, "new")])
    ds = pl.DataFrame(dataset_records(facets, [old[0], new[0]]))
    fs = pl.DataFrame(file_records(facets, [old[1], new[1]]))
    local, _ = compare(holdings, ds, fs)
    r = local.row(0, named=True)
    assert (r["esgf_status"], r["esgf_version"], r["latest_version"]) == (
        "match",
        "v1",
        "v2",
    )
