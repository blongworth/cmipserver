import du_compare
import polars as pl
import pytest

ROOT = "/data"
CAT = "/data/cmip6/CMIP/NCAR/a.nc"


def _du(tmp_path, rows):
    d = tmp_path / "du"
    d.mkdir()
    for i, r in enumerate(rows, 1):
        (d / f"{i}.tsv").write_text("\t".join(map(str, r)) + "\n")
    return d


def _manifest(tmp_path):
    m = tmp_path / "m.yaml"
    m.write_text(
        "products:\n"
        "  - {name: cmip6, root: /data/cmip6, builder: cmip6, output: whoi_cmip6,"
        " enabled: true, description: x}\n"
        "  - {name: gm, root: /data/cmems/G/monthly, builder: cmip6,"
        " output: whoi_gm, enabled: false, description: x}\n"
        "listed:\n"
        "  - {name: orb, path: other/b, description: x}\n"
    )
    return m


def test_compare(tmp_path, capsys):
    # shard, files, apparent, allocated, nonnc_files, nonnc_bytes, errors
    du = _du(
        tmp_path,
        [
            ("cmip6/CMIP", 3, 3050, 4096, 1, 50, 0),
            ("cmip6/ScenarioMIP/NCAR", 1, 100, 512, 0, 0, 0),
            ("cmems/G", 2, 70, 512, 0, 0, 0),
            ("other/b", 1, 5, 512, 0, 0, 1),
        ],
    )
    cat = tmp_path / "cat.csv"
    cat.write_text(
        f"path,size_bytes,variable_id\n{CAT},2000,tas\n"
        "/data/cmip6/ScenarioMIP/NCAR/b.nc,100,pr\n/elsewhere/c.nc,1,x\n"
    )
    out = tmp_path / "out"
    rc = du_compare.main(
        [
            "--du", str(du), "--catalog", str(cat), "--products",
            str(_manifest(tmp_path)), "--root", ROOT, "--out", str(out),
        ]
    )  # fmt: skip
    assert rc == 0
    shards = pl.read_parquet(out / "du_by_shard.parquet")
    row = shards.filter(pl.col("shard") == "cmip6/CMIP").row(0, named=True)
    assert (row["nc_files"], row["nc_bytes"]) == (2, 3000)
    assert (row["cat_files"], row["cat_bytes"], row["diff_bytes"]) == (1, 2000, 1000)

    prod = {
        r["product"]: r
        for r in pl.read_parquet(out / "du_by_product.parquet").iter_rows(named=True)
    }
    assert prod["cmip6"]["shards"] == 2
    assert prod["cmip6"]["nc_bytes"] == 3100
    assert prod["cmip6"]["cat_bytes"] == 2100
    assert prod["cmip6"]["diff_files"] == 1
    assert prod["gm"]["nc_bytes"] is None
    assert "coarser" in prod["gm"]["note"]
    assert prod["orb"]["kind"] == "listed"
    assert prod["orb"]["errors"] == 1
    assert "not under /data/" in capsys.readouterr().err


def test_overlapping_shards_rejected(tmp_path):
    du = _du(
        tmp_path,
        [("cmip6/CMIP", 1, 1, 1, 0, 0, 0), ("cmip6/CMIP/NCAR", 1, 1, 1, 0, 0, 0)],
    )
    with pytest.raises(SystemExit, match="overlapping"):
        du_compare.read_du([du])
