import polars as pl
import verify_checksums
from conftest import sha256

from whoi_cmip_reorg_code.checksums import sha256_file, shard


def test_sha256_file(messy_store):
    p = messy_store.paths["good_0"]
    assert sha256_file(p, bufsize=7) == sha256(p)


def test_select_and_hash(messy_store, messy_scan, tmp_path):
    from whoi_cmip_reorg_code.ncinfo import check_files, nc_candidates

    holdings = pl.scan_parquet(messy_scan / "holdings_all.parquet")
    nci = tmp_path / "ncinfo_all.parquet"
    check_files(nc_candidates(holdings).collect(), nci)
    esgf = pl.DataFrame(
        {
            "path": [
                str(messy_store.paths["good_0"]),
                str(messy_store.paths["good_1"]),
            ],
            "esgf_status": ["match", "size_mismatch"],
            "esgf_checksum": [sha256(messy_store.paths["good_0"]), "0" * 64],
            "esgf_checksum_type": ["SHA256", "SHA256"],
        }
    )
    esgf_p = tmp_path / "esgf_compare.parquet"
    esgf.write_parquet(esgf_p)

    cand = tmp_path / "c.parquet"
    verify_checksums.main(
        [
            "select",
            "--scan",
            str(messy_scan),
            "--ncinfo",
            str(tmp_path),
            "--esgf",
            str(esgf_p),
            "--sample-per-source",
            "1",
            "--out",
            str(cand),
        ]
    )
    c = pl.read_parquet(cand)
    why = dict(zip(c["rel_path"], c["reason"]))
    assert why[messy_store.rel("empty")] == "nc_status:empty"
    assert why[messy_store.rel("truncated")] == "nc_status:truncated"
    assert "esgf_size_mismatch" in why[messy_store.rel("good_1")]
    # one sample per source_id (CESM2, GFDL-ESM4)
    assert sum("sample" in r for r in c["reason"]) == 2

    for i in range(2):
        verify_checksums.main(
            [
                "hash",
                "--candidates",
                str(cand),
                "--out",
                str(tmp_path / "ck"),
                "--n-shards",
                "2",
                "--shard-index",
                str(i),
            ]
        )
    ck = pl.read_parquet(sorted((tmp_path / "ck").glob("checksums_*.parquet")))
    assert ck.height == c.height
    row = ck.filter(rel_path=messy_store.rel("good_1")).row(0, named=True)
    assert row["checksum_ok"] is False
    assert row["sha256"] == sha256(messy_store.paths["good_1"])
    empty = ck.filter(rel_path=messy_store.rel("empty")).row(0, named=True)
    assert empty["sha256"] == sha256(messy_store.paths["empty"])
    assert empty["checksum_ok"] is None


def test_unreadable_is_recorded(tmp_path):
    from whoi_cmip_reorg_code.checksums import CANDIDATES_SCHEMA, verify

    c = pl.DataFrame(
        [
            {
                "path": str(tmp_path / "gone.nc"),
                "rel_path": "gone.nc",
                "source_id": "S",
                "size_bytes": 1,
                "reason": "sample",
                "expected_sha256": None,
            }
        ],
        schema=CANDIDATES_SCHEMA,
    )
    res = verify(c, tmp_path / "o.parquet")
    assert res["n_error"] == 1
    assert pl.read_parquet(tmp_path / "o.parquet")["error"][0].startswith(
        "FileNotFoundError"
    )


def test_shard_partition():
    df = pl.DataFrame({"x": range(10)})
    parts = [shard(df, i, 3)["x"].to_list() for i in range(3)]
    assert sorted(x for p in parts for x in p) == list(range(10))
