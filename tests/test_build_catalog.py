import json
import shutil

import build_catalog
import check_catalog
import intake
import polars as pl
import pytest

from whoi_cmip_reorg_code.catalog import cmip6
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA

GOOD = [
    "good_0",
    "good_1",
    "good_2",
    "gap_0",
    "gap_1",
    "overlap_0",
    "overlap_1",
    "v1",
    "v2",
    "fx",
    "mismatch",
]
BAD = ["empty", "truncated", "text"]


@pytest.fixture(scope="module")
def built(messy_store, audit, tmp_path_factory):
    out = tmp_path_factory.mktemp("catalog")
    rc = build_catalog.main(
        [
            "--drs-root",
            str(messy_store.root),
            "--name",
            "test_cat",
            "--out",
            str(out),
            "--ncinfo",
            str(audit / "ncinfo"),
            "--jobs",
            "1",
        ]
    )
    assert rc == 0
    return out


def test_only_good_files(built, messy_store):
    df = pl.read_csv(built / "test_cat.csv", schema=CATALOG_SCHEMA)
    want = sorted(str(messy_store.paths[k]) for k in GOOD)
    assert df["path"].to_list() == want
    inv = pl.read_parquet(built / "test_cat_invalid.parquet")
    reasons = dict(zip(inv["path"], inv["reason"]))
    for k in BAD:
        assert reasons[str(messy_store.paths[k])] == "excluded"
    assert reasons[str(messy_store.paths["flat"])] == "drs_status=wrong_depth"


def test_rows(built, messy_store):
    df = pl.read_csv(built / "test_cat.csv", schema=CATALOG_SCHEMA)
    r = df.filter(pl.col("path") == str(messy_store.paths["good_0"])).row(0, named=True)
    assert r["master_id"] == "CMIP6.CMIP.NCAR.CESM2.historical.r1i1p1f1.Amon.tas.gn"
    assert (r["version"], r["time_range"], r["collection"]) == (
        "v20190308",
        "185001-189912",
        "cmip6",
    )
    assert r["size_bytes"] == messy_store.paths["good_0"].stat().st_size
    assert r["product"] is None
    fx = df.filter(pl.col("variable_id") == "areacella").row(0, named=True)
    assert fx["time_range"] is None


def test_intake_loads_dataset(built):
    cat = intake.open_esm_datastore(str(built / "test_cat.json"))
    key = "CMIP6.CMIP.NCAR.CESM2.historical.r1i1p1f1.Amon.tas.gn.v20190308"
    assert key in cat
    ds = cat.search(variable_id="tas", version="v20190308").to_dataset_dict(
        progressbar=False
    )[key]
    assert ds.sizes["time"] == 165 * 12


def test_subtree_root(messy_store, tmp_path):
    sub = messy_store.root / "CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas"
    df, _ = cmip6.build([str(sub)], str(messy_store.root), jobs=1)
    assert df.height == 3 and set(df["variable_id"]) == {"tas"}
    with pytest.raises(ValueError, match="not under"):
        cmip6.build([str(tmp_path)], str(messy_store.root), jobs=1)


def test_manifest_mode(messy_store, tmp_path):
    m = tmp_path / "catalogs.yaml"
    m.write_text(
        "products:\n"
        f"  - {{name: a, root: {messy_store.root}, builder: cmip6, output: a}}\n"
        f"  - {{name: b, root: {messy_store.root}, builder: cmip6, output: b, "
        "enabled: false}\n"
    )
    assert (
        build_catalog.main(
            ["--manifest", str(m), "--out", str(tmp_path), "--jobs", "1"]
        )
        == 0
    )
    assert (tmp_path / "a.json").exists() and not (tmp_path / "b.json").exists()


def test_manifest_mode_names_files_by_output(messy_store, tmp_path):
    m = tmp_path / "catalogs.yaml"
    m.write_text(
        "products:\n"
        f"  - {{name: prod, root: {messy_store.root}, builder: cmip6, output: whoi_prod}}\n"
    )
    out = tmp_path / "out"
    argv = ["--manifest", str(m), "--out", str(out), "--jobs", "1"]
    assert build_catalog.main([*argv, "--product", "prod"]) == 0
    assert (out / "whoi_prod.json").exists() and (out / "whoi_prod.csv").exists()
    assert not (out / "prod.json").exists()


# --- check_catalog -----------------------------------------------------------


def test_check_passes(built, tmp_path):
    report = tmp_path / "r.json"
    rc = check_catalog.main(
        [
            "--catalog",
            str(built / "test_cat.json"),
            "--previous",
            str(built / "test_cat.json"),
            "--report",
            str(report),
        ]
    )
    res = json.loads(report.read_text())
    assert rc == 0, res
    assert {r["check"] for r in res} >= {
        "columns",
        "types",
        "unique_paths",
        "sample_paths_exist",
        "dataset_count_vs_previous",
        "intake_open",
        "to_dataset_dict",
    }


def _copy(built, tmp_path, edit):
    shutil.copy(built / "test_cat.json", tmp_path / "test_cat.json")
    df = pl.read_csv(built / "test_cat.csv", schema=CATALOG_SCHEMA)
    edit(df).write_csv(tmp_path / "test_cat.csv")
    return tmp_path / "test_cat.json"


def _failed(cat, *extra):
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = check_catalog.main(["--catalog", str(cat), *extra])
    return rc, {
        ln.split()[1].rstrip(":")
        for ln in buf.getvalue().splitlines()
        if ln.startswith("FAIL")
    }


def test_check_fails_on_missing_path(built, tmp_path):
    cat = _copy(
        built,
        tmp_path,
        lambda df: df.with_columns(
            path=pl.when(pl.col("variable_id") == "pr")
            .then(pl.col("path") + ".gone")
            .otherwise("path")
        ),
    )
    rc, failed = _failed(cat)
    assert rc == 1 and "sample_paths_exist" in failed


def test_check_fails_on_unloadable_dataset(built, tmp_path):
    cat = _copy(built, tmp_path, lambda df: df.filter(pl.col("variable_id") == "tos"))
    rc, failed = _failed(cat)
    assert rc == 1 and failed == {"to_dataset_dict"}


def test_check_fails_on_big_drop(built, tmp_path):
    cat = _copy(built, tmp_path, lambda df: df.filter(pl.col("variable_id") == "tas"))
    rc, failed = _failed(cat, "--previous", str(built / "test_cat.json"))
    assert rc == 1 and failed == {"dataset_count_vs_previous"}


def test_check_fails_on_duplicate_paths(built, tmp_path):
    cat = _copy(built, tmp_path, lambda df: pl.concat([df, df.head(1)]))
    rc, failed = _failed(cat)
    assert rc == 1 and "unique_paths" in failed


def test_site_summary_runs_on_built_catalog(built, messy_store, tmp_path):
    import summarize_catalog

    root = str(messy_store.root.parent)
    assert (
        summarize_catalog.main(
            [
                "--catalog",
                str(built / "test_cat.csv"),
                "--out",
                str(tmp_path),
                "--root",
                root,
            ]
        )
        == 0
    )
    summary = json.loads((tmp_path / "catalog_summary.json").read_text())
    # 11 good files: tas, pr, tos, psl (2 versions), areacella, huss
    ids = {d["dataset_id"] for d in summary}
    assert len(ids) == 7
    assert "CMIP6.CMIP.NCAR.CESM2.historical.r1i1p1f1.Amon.tas.gn.v20190308" in ids
    assert all(d["relative_path"].startswith("cmip6/CMIP/") for d in summary)
