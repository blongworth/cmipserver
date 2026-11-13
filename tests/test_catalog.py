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


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    assert msc.main(["--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def summary(sample, tmp_path_factory):
    out = tmp_path_factory.mktemp("summary")
    csv = sample / "sample_catalog.csv"
    assert summarize_catalog.main(["--catalog", str(csv), "--out", str(out)]) == 0
    return out


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
    assert df["path"].str.starts_with("/proj/cmip6/").all()


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
        "/proj/cmip6", str(sample / "sample_data"), regex=False
    )
    ds = cat[REAL_KEY].to_dask()
    assert ds["tas"].attrs["units"] == "K"
    assert ds.sizes["time"] == (2014 - 1850 + 1) * 12


def test_summary_columns_and_counts(sample, summary):
    s = pl.read_json(summary / "catalog_summary.json", schema=SUMMARY_SCHEMA)
    records = json.loads((summary / "catalog_summary.json").read_text())
    assert all(list(r) == list(SUMMARY_SCHEMA) for r in records)
    cat = pl.read_csv(sample / "sample_catalog.csv", schema_overrides=CATALOG_SCHEMA)
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
    assert cfg["vast_prefix"] == "/proj/cmip6"
    assert "cmip.whoi.edu" in variables["acknowledgment"]
    with pytest.raises(ValueError):
        write_site_config.site_config({**variables, "audience": "public"})
