"""Store vocabulary: source readers, variable attributes, merge, scripts."""

from __future__ import annotations

import json

import build_vocab
import netCDF4
import numpy as np
import polars as pl
import pytest
import scan_var_attrs
import summarize_catalog
import xarray as xr

from whoi_cmip_reorg_code import vocab
from whoi_cmip_reorg_code import vocab_sources as vs
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA, VOCAB_SCHEMA
from whoi_cmip_reorg_code.ncinfo import var_attrs

ROOT = "/proj/cmip6/data"

CMOR_TABLE = {
    "Header": {"table_id": "Table Amon"},
    "variable_entry": {
        "tas": {
            "long_name": "Near-Surface Air Temperature",
            "standard_name": "air_temperature",
            "units": "K",
            "comment": "near-surface (usually, 2 meter) air temperature",
            "cell_methods": "area: time: mean",
            "dimensions": "longitude latitude time height2m",
            "modeling_realm": "atmos",
            "frequency": "mon",
            "out_name": "tas",
        },
        "pr": {
            "long_name": "Precipitation",
            "standard_name": "precipitation_flux",
            "units": "kg m-2 s-1",
            "comment": "",
            "out_name": "pr",
        },
        "ta27": {"long_name": "Air Temperature (27 levels)", "out_name": "ta"},
        "ta": {"long_name": "Air Temperature", "units": "K", "out_name": "ta"},
    },
}
ECMWF_PARAMS = [
    {
        "id": 167,
        "name": "2 metre temperature",
        "shortname": "2t",
        "unit_id": 2,
        "description": "Air temperature at 2m.<br/><br/>See <a href='x'>here</a>.",
    },
    {"id": 300128, "name": "2 metre temperature", "shortname": "tp2m", "unit_id": 2},
    {"id": 34, "name": "Sea surface temperature", "shortname": "sst", "unit_id": 2},
    {"id": 235, "name": "Skin temperature", "shortname": "skt", "unit_id": 2},
    {
        "id": 162071,
        "name": "Total column vertically-integrated eastward water vapour flux",
        "shortname": "viwve",
        "unit_id": 2,
    },
    {"id": 999, "name": "Old thing", "shortname": "old", "unit_id": 2, "retired": True},
]
CF_XML = """<?xml version="1.0"?>
<standard_name_table>
  <version_number>95</version_number>
  <entry id="air_temperature">
    <canonical_units>K</canonical_units>
    <description>Air temperature is the bulk temperature of the air.</description>
  </entry>
  <entry id="sea_water_salinity">
    <canonical_units>1e-3</canonical_units>
    <description>Salt content of sea water.</description>
  </entry>
  <entry id="sea_water_potential_temperature">
    <canonical_units>K</canonical_units>
    <description>Potential temperature of sea water.</description>
  </entry>
  <alias id="sea_water_temperature_old">
    <entry_id>sea_water_potential_temperature</entry_id>
  </alias>
</standard_name_table>
"""


@pytest.fixture
def sources():
    cmor = vs.frame(vs.cmor_rows(CMOR_TABLE), vs.CMOR_SCHEMA)
    ecmwf = vs.frame(vs.ecmwf_rows(ECMWF_PARAMS, {2: "K"}), vs.ECMWF_SCHEMA)
    cf_rows, _ = vs.cf_rows(CF_XML)
    return {
        "cmip6-cmor-tables": cmor,
        "ecmwf-param-db": ecmwf,
        "cf-standard-names": vs.frame(cf_rows, vs.CF_SCHEMA),
    }


META = {
    "cmip6-cmor-tables": {"version": "6.9.33"},
    "ecmwf-param-db": {"version": "2026-11-23"},
}


def test_cmor_rows():
    rows = {r["entry"]: r for r in vs.cmor_rows(CMOR_TABLE)}
    assert rows["tas"]["table_id"] == "Amon"
    assert rows["tas"]["dimensions"] == "longitude latitude time height2m"
    assert rows["pr"]["comment"] is None  # blank -> null
    assert rows["ta27"]["out_name"] == "ta"
    assert vs.cmor_rows({"Header": {"table_id": "Table CV"}}) == []


def test_ecmwf_rows_strip_html_and_skip_retired():
    rows = vs.ecmwf_rows(ECMWF_PARAMS, {2: "K"})
    assert [r["param_id"] for r in rows] == [167, 300128, 34, 235, 162071]
    assert rows[0]["description"] == "Air temperature at 2m. See here."
    assert rows[0]["units"] == "K"


def test_cf_rows_with_alias():
    rows, version = vs.cf_rows(CF_XML)
    assert version == "95"
    by = {r["standard_name"]: r for r in rows}
    assert by["air_temperature"]["canonical_units"] == "K"
    assert by["sea_water_temperature_old"]["alias_of"] == (
        "sea_water_potential_temperature"
    )


def test_read_sources_roundtrip(tmp_path, sources):
    for name, (fname, _) in vs.SOURCE_FILES.items():
        sources[name].write_csv(tmp_path / fname)
    (tmp_path / vs.SOURCES_YAML).write_text("cf-standard-names: {version: '95'}\n")
    frames, meta = vs.read_sources(tmp_path)
    assert frames["cmip6-cmor-tables"].equals(sources["cmip6-cmor-tables"])
    assert meta["cf-standard-names"]["version"] == "95"
    empty, _ = vs.read_sources(tmp_path / "none")
    assert empty["ecmwf-param-db"].height == 0


# --- synthetic store ------------------------------------------------------


def _nc(path, data_vars: dict, gattrs: dict | None = None):
    """data_vars: name -> attrs; each (time, lat, lon) float32."""
    path.parent.mkdir(parents=True, exist_ok=True)
    shape = (2, 2, 2)
    ds = xr.Dataset(
        {
            n: (("time", "lat", "lon"), np.zeros(shape, "float32"), a)
            for n, a in data_vars.items()
        },
        coords={
            "time": ("time", [0.0, 1.0], {"units": "days since 2000-01-01"}),
            "lat": ("lat", [0.0, 1.0], {"units": "degrees_north"}),
            "lon": ("lon", [0.0, 1.0], {"units": "degrees_east"}),
        },
        attrs=gattrs or {},
    )
    ds.to_netcdf(path)
    return path


def _row(path: str, **facets) -> dict:
    return {c: None for c in CATALOG_SCHEMA} | {"path": path, "version": "v1"} | facets


@pytest.fixture
def store(tmp_path):
    """Files under tmp_path/data, catalogs with ROOT paths."""
    data = tmp_path / "data"
    files = {
        "cmip6/tas_Amon_X_historical_r1i1p1f1_gn_185001-185012.nc": {
            "tas": {"long_name": "Near-Surface Air Temperature", "units": "K"}
        },
        "cmip6/pr_Amon_X_historical_r1i1p1f1_gn_185001-185012.nc": {
            "pr": {"long_name": "Precipitation", "units": "mm/day"}
        },
        "era5/2m_temperature/2000-01-01_2m_temperature.nc": {
            "t2m": {"long_name": "2 metre temperature", "units": "K"}
        },
        "era5/sea_surface_temperature/2000-01-01_sea_surface_temperature.nc": {
            "sst": {"long_name": "SST", "units": "K", "GRIB_paramId": np.int64(34)}
        },
        "reanalysis/20crv3/air.2m.mon.mean.nc": {
            "air": {
                "long_name": "Monthly Air Temperature at 2 m",
                "units": "degK",
                "var_desc": "Air temperature",
                "standard_name": "air_temperature",
            }
        },
        "ocean/glorys/1993/mercatorglorys12v1_19930101.nc": {
            "thetao": {
                "long_name": "Temperature",
                "standard_name": "sea_water_temperature_old",
                "units": "degrees_C",
            },
            "so": {
                "long_name": "Salinity",
                "standard_name": "not_a_cf_name",
                "units": "1e-3",
            },
        },
        # no GRIB_paramId, long_name not in the database: found by short name
        "era5/skin_temperature/2000-01-01_skin_temperature.nc": {
            "skt": {"long_name": "Skin temp", "units": "K"}
        },
        # GRIB1 parameter with no short name: p<param>.<table>, old long name
        "era5/vert-int/2000-01-01_vert-int.nc": {
            "p71.162": {
                "long_name": "Vertical integral of eastward water vapour flux",
                "units": "kg m**-1 s**-1",
            }
        },
    }
    for rel, dv in files.items():
        _nc(data / rel, dv)

    rels = list(files)

    def p(rel):
        return f"{ROOT}/{rel}"

    cmip_f = {
        "collection": "cmip6",
        "activity_id": "CMIP",
        "institution_id": "I",
        "source_id": "X",
        "experiment_id": "historical",
        "member_id": "r1i1p1f1",
        "table_id": "Amon",
        "grid_label": "gn",
    }
    cmip6 = [
        _row(p(r), variable_id=v, **cmip_f)
        for r, v in [(rels[0], "tas"), (rels[1], "pr")]
    ] + [
        # in the catalog, never scanned (file missing): official, not_checked
        _row(p("cmip6/ta_Amon_X.nc"), variable_id="ta", **cmip_f)
    ]
    era5 = [
        _row(
            p(rels[i]),
            collection="reanalysis",
            product="era5",
            frequency="1hr",
            level="sfc",
            variable_id=v,
        )
        for i, v in [
            (2, "2m_temperature"),
            (3, "sea_surface_temperature"),
            (6, "skin_temperature"),
            (7, "vert-int"),
        ]
    ]
    c20 = [
        _row(
            p(rels[4]),
            collection="reanalysis",
            product="20crv3",
            frequency="mon",
            level="2m",
            variable_id="air",
        )
    ]
    gl = [
        _row(
            p(rels[5]),
            collection="ocean_reanalysis",
            product="glorys12v1",
            frequency="day",
            variable_id="so,thetao",
        )
    ]
    cats = {}
    for name, rows in [
        ("whoi_cmip6", cmip6),
        ("whoi_era5", era5),
        ("whoi_20crv3", c20),
        ("whoi_glorys12v1", gl),
    ]:
        df = pl.DataFrame(rows, schema=CATALOG_SCHEMA, orient="row")
        df.write_csv(tmp_path / f"{name}.csv")
        cats[name] = df
    return {"tmp": tmp_path, "data": data, "cats": cats}


def test_var_attrs_multi_variable(store):
    path = store["data"] / "ocean/glorys/1993/mercatorglorys12v1_19930101.nc"
    with netCDF4.Dataset(path) as ds:
        got = {r["nc_name"]: r for r in var_attrs(ds)}
    assert set(got) == {"thetao", "so"}  # coordinates are not data variables
    assert got["so"]["units"] == "1e-3"
    assert got["so"]["dimensions"] == "time lat lon"
    assert got["so"]["grib_param_id"] is None


def _scan_all(store):
    rp = (ROOT, str(store["data"]))
    frames = [
        vocab.scan_samples(vocab.sample_files(n, df), rp)
        for n, df in store["cats"].items()
    ]
    keys = pl.concat(vocab.catalog_keys(n, df) for n, df in store["cats"].items())
    return keys, pl.concat(frames)


def test_build_vocabulary(store, sources):
    keys, attrs = _scan_all(store)
    voc, rep = vocab.build_vocabulary(keys, attrs, sources, META, ROOT)
    assert voc.schema == pl.Schema(VOCAB_SCHEMA)
    by = {(r["catalog"], r["variable_id"], r["nc_name"]): r for r in voc.to_dicts()}

    tas = by["whoi_cmip6", "tas", "tas"]
    assert tas["source"] == "cmip6-cmor-tables"
    assert tas["source_version"] == "6.9.33"
    assert tas["realm"] == "atmos"
    assert tas["nc_check"] == "match"
    assert tas["cf_description"].startswith("Air temperature is")
    assert tas["sample_path"].startswith("cmip6/tas_")

    assert by["whoi_cmip6", "pr", "pr"]["nc_check"] == "units_differ"
    ta = by["whoi_cmip6", "ta", "ta"]
    assert ta["long_name"] == "Air Temperature"  # entry == out_name preferred
    assert ta["nc_check"] == "not_checked"
    assert rep["cmor_ambiguous"] == [
        {"table_id": "Amon", "variable_id": "ta", "used": "ta"}
    ]

    t2m = by["whoi_era5", "2m_temperature", "t2m"]
    assert t2m["source"] == "ecmwf-param-db"
    assert t2m["description"].startswith("Air temperature at 2m")  # id 167
    assert t2m["nc_check"] == "match"
    sst = by["whoi_era5", "sea_surface_temperature", "sst"]
    assert sst["long_name"] == "Sea surface temperature"  # by GRIB_paramId
    skt = by["whoi_era5", "skin_temperature", "skt"]
    assert skt["long_name"] == "Skin temperature"
    assert rep["ecmwf_matched_by"] == {
        "long_name": 1,
        "GRIB_paramId": 1,
        "short name": 1,
        "p<param>.<table> name": 1,
    }
    viwve = by["whoi_era5", "vert-int", "p71.162"]
    assert viwve["long_name"].startswith("Total column vertically-integrated")
    assert rep["ecmwf_unmatched"] == []

    air = by["whoi_20crv3", "air", "air"]
    assert air["source"] == "netcdf"
    assert air["description"] == "Air temperature"  # var_desc
    assert air["nc_check"] is None

    thetao = by["whoi_glorys12v1", "so,thetao", "thetao"]
    assert thetao["cf_description"] == "Potential temperature of sea water."
    assert ("whoi_glorys12v1", "so,thetao", "so") in by
    unknown = {r["standard_name"] for r in rep["unknown_standard_names"]}
    assert unknown == {"not_a_cf_name", "precipitation_flux"}  # small fixture table

    assert [r["variable_id"] for r in rep["not_scanned"]] == []
    assert [r["variable_id"] for r in rep["scan_errors"]] == ["ta"]
    assert rep["undefined"] == []
    assert vocab.missing_keys(keys, voc).height == 0


def test_build_without_scan_uses_official_only(store, sources):
    keys = pl.concat(vocab.catalog_keys(n, df) for n, df in store["cats"].items())
    attrs = pl.DataFrame(schema=vocab.VAR_ATTRS_SCHEMA)
    voc, rep = vocab.build_vocabulary(keys, attrs, sources, META, ROOT)
    gl = voc.filter(pl.col("catalog") == "whoi_glorys12v1")
    assert sorted(gl["nc_name"]) == ["so", "thetao"]  # split from variable_id
    assert gl["source"].is_null().all()
    assert len(rep["not_scanned"]) == keys.height
    assert voc.filter(pl.col("variable_id") == "tas")["nc_check"].item() == (
        "not_checked"
    )
    assert vocab.missing_keys(keys, voc).height == 0


def test_missing_keys(store):
    keys = vocab.catalog_keys("whoi_cmip6", store["cats"]["whoi_cmip6"])
    voc = pl.DataFrame(
        [{c: None for c in VOCAB_SCHEMA} | keys.row(0, named=True)],
        schema=VOCAB_SCHEMA,
        orient="row",
    )
    assert vocab.missing_keys(keys, voc).height == keys.height - 1


def test_scripts_end_to_end(store, sources, capsys):
    tmp = store["tmp"]
    src = tmp / "sources"
    src.mkdir()
    for name, (fname, _) in vs.SOURCE_FILES.items():
        sources[name].write_csv(src / fname)
    out = tmp / "vocab"
    cats = [str(tmp / f"{n}.csv") for n in store["cats"]]
    rp = ["--replace-prefix", ROOT, str(store["data"])]
    for c in cats:
        assert scan_var_attrs.main(["--catalog", c, "--out", str(out), *rp]) == 0
    first = pl.read_parquet(out / "var_attrs_whoi_era5.parquet")
    capsys.readouterr()
    assert scan_var_attrs.main(["--catalog", cats[1], "--out", str(out), *rp]) == 0
    assert "0 to open" in capsys.readouterr().err  # cached
    assert pl.read_parquet(out / "var_attrs_whoi_era5.parquet").equals(first)

    args = [a for c in cats for a in ("--catalog", c)]
    rc = build_vocab.main(
        [*args, "--var-attrs", str(out), "--sources", str(src), "--out", str(out)]
    )
    assert rc == 0
    voc = pl.read_csv(out / "whoi_cmip_vocabulary.csv", schema=VOCAB_SCHEMA)
    assert voc.height == 10
    rep = json.loads((out / "whoi_cmip_vocabulary_report.json").read_text())
    assert rep["counts"]["rows"] == 10
    assert rep["catalogs"] == list(store["cats"])

    # site: only the variables of the summarized catalogs
    site = tmp / "site"
    assert (
        summarize_catalog.main(
            [
                "--catalog",
                cats[2],
                "--out",
                str(site),
                "--vocab",
                str(out / "whoi_cmip_vocabulary.csv"),
            ]
        )
        == 0
    )
    sv = json.loads((site / "variables.json").read_text())
    assert [(r["variable_id"], r["long_name"]) for r in sv] == [
        ("air", "Monthly Air Temperature at 2 m")
    ]
    assert set(sv[0]) == set(summarize_catalog.SITE_VOCAB_COLS)
