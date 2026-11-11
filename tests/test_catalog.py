import json
from pathlib import Path

import intake
import make_sample_catalog as msc
import polars as pl
import pytest
import summarize_catalog
import write_site_config
import yaml

from whoi_cmip_reorg_code.catalog_schema import (
    CATALOG_SCHEMA,
    DATASET_KEYS,
    SUMMARY_SCHEMA,
)

FIXTURES = Path(__file__).parent / "fixtures"
SITE = Path(__file__).parent.parent / "site"
REAL_KEY = "CMIP6.CMIP.NCAR.CESM2.historical.r1i1p1f1.Amon.tas.gn.v20190308"
# Real catalog of the 20CRv3 monthly means on VAST (tests/fixtures/README.md).
TCR_CSV = FIXTURES / "whoi_20crv3.csv"
TCR_JSON = FIXTURES / "whoi_20crv3.json"


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    assert msc.main(["--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def summary(sample, tmp_path_factory):
    """Sample + real 20CRv3, as committed in site/data."""
    out = tmp_path_factory.mktemp("summary")
    csv = sample / "sample_catalog.csv"
    argv = ["--catalog", str(csv), "--catalog", str(TCR_CSV), "--out", str(out)]
    assert summarize_catalog.main(argv) == 0
    return out


@pytest.fixture(scope="module")
def tcr_summary(tmp_path_factory):
    out = tmp_path_factory.mktemp("tcr_summary")
    argv = ["--catalog", str(TCR_CSV), "--out", str(out)]
    assert summarize_catalog.main(argv) == 0
    return out


def read_summary(out: Path) -> pl.DataFrame:
    return pl.read_json(out / "catalog_summary.json", schema=SUMMARY_SCHEMA)


def test_sample_deterministic(sample):
    a = msc.make_catalog()
    assert a.equals(msc.make_catalog())
    assert 500 <= a.height <= 2000
    assert set(a["collection"]) == {"cmip6", "reanalysis"}
    assert a["source_id"].drop_nulls().n_unique() >= 3


def test_committed_fixture_is_current(sample):
    # tests/fixtures must match what the generator produces now
    assert (FIXTURES / "sample_catalog.csv").read_text() == (
        sample / "sample_catalog.csv"
    ).read_text()
    assert (FIXTURES / "sample_catalog.json").read_text() == (
        sample / "sample_catalog.json"
    ).read_text()


def test_catalog_columns_match_schema(sample):
    df = pl.read_csv(sample / "sample_catalog.csv", schema_overrides=CATALOG_SCHEMA)
    assert df.columns == list(CATALOG_SCHEMA)
    assert df["path"].is_unique().all()
    assert df["path"].str.starts_with("/proj/cmip6/data/").all()


def test_intake_loads_offline(sample):
    cat = intake.open_esm_datastore(str(sample / "sample_catalog.json"))
    df = pl.read_csv(sample / "sample_catalog.csv", schema_overrides=CATALOG_SCHEMA)
    assert len(cat.df) == df.height
    assert len(cat) == df.select(DATASET_KEYS).unique().height
    assert REAL_KEY in set(cat.keys())
    assert len(cat.search(product="era5").df) > 0


def test_browser_snippet_loads_real_dataset(sample):
    # same pattern as the browser's "Get data" snippet, with the path swap
    cat = intake.open_esm_datastore(str(sample / "sample_catalog.json"))
    cat.df["path"] = cat.df["path"].str.replace(
        "/proj/cmip6/data", str(sample / "sample_data"), regex=False
    )
    ds = cat[REAL_KEY].to_dask()
    assert ds["tas"].attrs["units"] == "K"
    assert ds.sizes["time"] == (2014 - 1850 + 1) * 12


def test_summary_columns_and_counts(sample, summary):
    s = read_summary(summary)
    records = json.loads((summary / "catalog_summary.json").read_text())
    assert all(list(r) == list(SUMMARY_SCHEMA) for r in records)
    cat = pl.concat(
        [
            pl.read_csv(
                sample / "sample_catalog.csv", schema_overrides=CATALOG_SCHEMA
            ).filter(pl.col("product").ne_missing("20crv3")),
            pl.read_csv(TCR_CSV, schema_overrides=CATALOG_SCHEMA),
        ]
    )
    by = cat.group_by(DATASET_KEYS).len()
    assert s.height == by.height
    assert s["n_files"].sum() == cat.height
    assert s["dataset_id"].is_unique().all()
    assert REAL_KEY in s["dataset_id"].to_list()
    stats = json.loads((summary / "catalog_stats.json").read_text())
    assert stats["n_datasets"] == s.height


def test_summary_has_no_absolute_paths(summary):
    text = (summary / "catalog_summary.json").read_text()
    assert "/proj/" not in text and "/vast/" not in text
    s = pl.read_json(summary / "catalog_summary.json", schema=SUMMARY_SCHEMA)
    assert not s["relative_path"].str.starts_with("/").any()


def test_file_lists_cover_summary(summary):
    s = pl.read_json(summary / "catalog_summary.json", schema=SUMMARY_SCHEMA)
    for row in s.iter_rows(named=True):
        lists = json.loads((summary / row["file_list"]).read_text())
        assert len(lists[row["dataset_id"]]) == row["n_files"]


def test_committed_site_summary_is_current(summary):
    for name in ("catalog_summary.json", "catalog_stats.json"):
        assert (SITE / "data" / name).read_text() == (summary / name).read_text()
    committed = sorted(p.name for p in (SITE / "data" / "files").glob("*.json"))
    fresh = sorted(p.name for p in (summary / "files").glob("*.json"))
    assert committed == fresh
    for name in fresh:
        assert (SITE / "data" / "files" / name).read_text() == (
            summary / "files" / name
        ).read_text()


def test_real_20crv3_fixture_matches_schema():
    df = pl.read_csv(TCR_CSV, schema_overrides=CATALOG_SCHEMA)
    assert df.columns == list(CATALOG_SCHEMA)
    assert df.height == 10
    assert df["path"].str.starts_with("/proj/cmip6/data/reanalysis/20crv3/").all()
    assert set(df["product"]) == {"20crv3"}
    assert json.loads(TCR_JSON.read_text())["id"] == "whoi_20crv3"


def test_real_20crv3_intake_loads_offline():
    cat = intake.open_esm_datastore(str(TCR_JSON))
    assert len(cat.df) == 10
    assert len(cat) == 10
    sub = cat.search(product="20crv3", frequency="mon", variable_id="prmsl")
    assert list(sub.keys()) == ["reanalysis.20crv3.mon.msl.prmsl.v3"]


def test_real_20crv3_summary_alone(tcr_summary):
    s = read_summary(tcr_summary)
    assert s.height == 10
    assert s["relative_path"].str.starts_with("reanalysis/20crv3/Monthlies/").all()
    assert s["time_start"].is_null().all() and s["time_end"].is_null().all()
    assert (s["n_files"] == 1).all()
    assert "/proj/" not in (tcr_summary / "catalog_summary.json").read_text()
    stats = json.loads((tcr_summary / "catalog_stats.json").read_text())
    assert stats["catalogs"] == [
        {"name": "whoi_20crv3", "n_datasets": 10, "synthetic": False}
    ]
    assert stats["by_product"] == {"20crv3": 10}


def test_real_20crv3_replaces_sample_20crv3(sample, summary, tcr_summary, tmp_path):
    s = read_summary(summary)
    assert s["dataset_id"].is_unique().all()
    real = read_summary(tcr_summary)
    assert s.filter(product="20crv3").equals(real)
    argv = ["--catalog", str(sample / "sample_catalog.csv"), "--out", str(tmp_path)]
    assert summarize_catalog.main(argv) == 0
    alone = read_summary(tmp_path)
    others = pl.col("product").ne_missing("20crv3")
    assert s.filter(others).equals(alone.filter(others))
    assert alone.filter(product="20crv3").height > 0
    stats = json.loads((summary / "catalog_stats.json").read_text())
    assert stats["catalogs"] == [
        {
            "name": "sample_catalog",
            "n_datasets": alone.filter(others).height,
            "synthetic": True,
        },
        {"name": "whoi_20crv3", "n_datasets": 10, "synthetic": False},
    ]
    shard = json.loads((summary / "files" / "reanalysis.20crv3.json").read_text())
    assert set(shard) == set(real["dataset_id"])


def test_summary_rejects_paths_outside_root(sample, tmp_path):
    with pytest.raises(SystemExit):
        summarize_catalog.main(
            [
                "--catalog",
                str(sample / "sample_catalog.csv"),
                "--out",
                str(tmp_path),
                "--root",
                "/elsewhere",
            ]
        )


def test_site_config_from_variables():
    variables = yaml.safe_load((SITE / "_variables.yml").read_text())
    cfg = write_site_config.site_config(variables)
    assert cfg["vast_prefix"] == "/proj/cmip6/data"
    assert "cmip.whoi.edu" in variables["acknowledgment"]
    with pytest.raises(ValueError):
        write_site_config.site_config({**variables, "audience": "public"})
