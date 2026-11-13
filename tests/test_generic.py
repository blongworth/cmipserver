"""Non-CMIP6 holdings: header metadata, header gaps, generic and dated
(ERA5-style) catalogs, and the per-product inventory.

``other_store`` is a small synthetic tree shaped like the real non-CMIP6
trees: ERA5 old (``time``) and new CDS (``valid_time``, ``number``,
``expver``) files, date-token file names, short-name dirs, multi-variable
daily ocean files in year dirs with a missing day, BRAN-style ``ocean_time``,
a PRISM-style file with no time coordinate, an empty file, a ``.bil.zip`` and
a hard-linked duplicate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import build_catalog
import check_catalog
import numpy as np
import polars as pl
import pytest
import summarize_catalog
import summarize_other
import yaml
from netCDF4 import Dataset

from whoi_cmip_reorg_code.catalog import generic, reanalysis
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA
from whoi_cmip_reorg_code.gaps import dataset_dir_expr, header_gaps
from whoi_cmip_reorg_code.manifest import (
    CatalogsManifest,
    ProductManifest,
    load,
)
from whoi_cmip_reorg_code.ncinfo import check_file, check_files
from whoi_cmip_reorg_code.scan import scan


def write(
    path: Path,
    data_vars: dict[str, int],
    times: list[float] | None,
    units: str = "days since 1993-01-01",
    calendar: str | None = "gregorian",
    tname: str = "time",
    cdsnew: bool = False,
    prism: bool = False,
) -> Path:
    """Tiny NetCDF4 file. ``data_vars``: name -> ndim (2: lat/lon, 3: +time,
    4: +depth). ``cdsnew`` adds ERA5 CDS ``number``/``expver``; ``prism``
    writes a ``t`` dim with ``year``/``month`` and no time coordinate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as ds:
        ds.title = f"SYNTHETIC {path.name}"
        ds.institution = "test"
        ds.history = "x" * 2000
        ds.createDimension("lat", 2)
        ds.createDimension("lon", 2)
        ds.createDimension("depth", 2)
        ds.createVariable("lat", "f4", ("lat",))[:] = [0, 1]
        ds.createVariable("lon", "f4", ("lon",))[:] = [0, 1]
        ds.createVariable("depth", "f4", ("depth",))[:] = [0, 10]
        tdim = None
        if prism:
            tdim = "t"
            ds.createDimension("t", len(times))
            ds.createVariable("year", "i4", ("t",))[:] = 1895
            ds.createVariable("month", "i4", ("t",))[:] = np.arange(len(times)) + 1
        elif times is not None:
            tdim = tname
            ds.createDimension(tname, len(times))
            t = ds.createVariable(tname, "f8", (tname,))
            t.units = units
            if calendar:
                t.calendar = calendar
            t[:] = times
        if cdsnew:
            ds.createVariable("number", "i8", ())[...] = 0
            ex = ds.createVariable("expver", str, (tdim,))
            for i in range(len(times)):
                ex[i] = "0001"
        for name, nd in data_vars.items():
            dims = {2: ("lat", "lon"), 3: (tdim, "lat", "lon")}.get(
                nd, (tdim, "depth", "lat", "lon")
            )
            v = ds.createVariable(name, "f4", dims)
            v.units = "1"
            if cdsnew:
                v.coordinates = f"number {tdim} lat lon expver"
            v[:] = 1.0
    return path


def hours(n: int, start: float = 0.0) -> list[float]:
    return [start + i for i in range(n)]


@dataclass
class OtherStore:
    root: Path
    p: dict[str, Path] = field(default_factory=dict)


def build_other_store(root: Path) -> OtherStore:
    s = OtherStore(root)
    p = s.p
    era = root / "era5/reanalysis/single-levels"
    mm = era / "monthly-means/2m_temperature"
    p["era_1979"] = write(
        mm / "1979_2m_temperature.nc",
        {"t2m": 3},
        [
            (d - 1) * 24.0
            for d in (1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335)
        ],
        units="hours since 1979-01-01",
    )
    p["era_1980"] = write(
        mm / "1980_2m_temperature.nc",
        {"t2m": 3},
        [
            86400.0 * (3652 + d)
            for d in (0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335)
        ],
        units="seconds since 1970-01-01",
        calendar="proleptic_gregorian",
        tname="valid_time",
        cdsnew=True,
    )
    p["era_stray"] = write(  # partial-year leftover, like era5 evaporation/x.nc
        mm / "x.nc", {"t2m": 3}, [0.0, 744.0], units="hours since 1980-01-01"
    )
    (mm / "1981_2m_temperature.nc.lck").write_text("lock\n")
    h1 = era / "1hr/2m_temperature"
    for i, day in enumerate(("1979-01-01", "1979-01-02")):
        p[f"era_hr_{i}"] = write(
            h1 / f"{day}_2m_temperature.nc",
            {"t2m": 3},
            hours(24, 24.0 * i),
            units="hours since 1979-01-01",
        )
    p["era_hr_year"] = write(
        h1 / "1980_2m_temperature.nc",
        {"t2m": 3},
        hours(24, 0.0),
        units="hours since 1980-01-01",
    )
    p["era_3hr"] = write(
        era / "3hr/T2m/T2m_1979-01.nc",
        {"t2m": 3},
        [3.0 * i for i in range(8)],
        units="hours since 1979-01-01",
    )
    p["era_uv"] = write(
        era / "3hr/uv10m/uv10m_1979-01.nc",
        {"u10": 3, "v10": 3},
        [3.0 * i for i in range(8)],
        units="hours since 1979-01-01",
    )

    ocean = root / "ocean_reanalysis"
    gl = ocean / "glorys12v1"
    for day in ("19930101", "19930102", "19930104"):  # 0103 missing
        p[f"gl_{day}"] = write(
            gl / "1993" / f"mercatorglorys12v1_gl12_mean_{day}.nc",
            {"thetao": 4, "so": 4, "zos": 3},
            [float(int(day[-2:]) - 1) * 24 + 12],
            units="hours since 1993-01-01",
        )
    p["gl_1994"] = write(
        gl / "1994/mercatorglorys12v1_gl12_mean_19940101.nc",
        {"thetao": 4, "so": 4, "zos": 3},
        [365 * 24.0 + 12],
        units="hours since 1993-01-01",
    )
    p["gl_empty"] = gl / "1994/mercatorglorys12v1_gl12_mean_19940102.nc"
    p["gl_empty"].write_bytes(b"")
    bran = ocean / "bran2020/monthly"
    for m, t in ((1, 15.5), (2, 45.0)):
        p[f"bran_{m}"] = write(
            bran / f"bran2020_monthly_eta_t_1993_{m:02d}.nc",
            {"eta_t": 3},
            [t],
            calendar=None,
            tname="ocean_time",
        )
    # hard link to a GLORYS file from another tree
    cm = root / "cmems/GLOBAL_REANALYSIS_PHY_001_030/monthly"
    cm.mkdir(parents=True)
    p["hardlink"] = cm / p["gl_19930101"].name
    os.link(p["gl_19930101"], p["hardlink"])

    pr = root / "prism"
    p["prism"] = write(
        pr / "netcdf/monthly/ppt_monthly_1895-1899.nc",
        {"ppt": 3},
        [0.0] * 12,
        prism=True,
    )
    p["bil"] = pr / "bil/PRISM_ppt_stable_4kmM2_1895_all_bil.zip"
    p["bil"].parent.mkdir(parents=True)
    p["bil"].write_bytes(b"PK\x03\x04 synthetic zip")
    return s


@pytest.fixture(scope="module")
def other(tmp_path_factory) -> OtherStore:
    return build_other_store(tmp_path_factory.mktemp("other") / "data")


@pytest.fixture(scope="module")
def ncinfo(other, tmp_path_factory) -> Path:
    """check_nc output for every .nc in the store (one parquet)."""
    out = tmp_path_factory.mktemp("ncinfo")
    paths = sorted(str(p) for p in other.root.rglob("*.nc"))
    files = pl.DataFrame(
        {
            "path": paths,
            "rel_path": [os.path.relpath(p, other.root) for p in paths],
            "size_bytes": [os.path.getsize(p) for p in paths],
        }
    )
    check_files(files, out / "ncinfo_all.parquet")
    return out


# --- ncinfo ------------------------------------------------------------------


def test_multi_variable_file(other):
    r = check_file(other.p["gl_19930101"], deep=True)
    assert r["nc_status"] == "ok"
    assert r["data_vars"] == ["thetao", "so", "zos"]
    assert r["main_var"] == "thetao"
    assert r["time_name"] == "time" and r["n_time"] == 1
    assert r["time_step_days"] is None  # one step: no within-file step
    assert r["attr_title"].startswith("SYNTHETIC")
    assert len(r["attr_history"]) == 500  # truncated


def test_cds_new_format(other):
    r = check_file(other.p["era_1980"], deep=True)
    assert r["nc_status"] == "ok", r["nc_error"]
    # number (scalar) and expver (string, in `coordinates`) are not data
    assert r["data_vars"] == ["t2m"] and r["main_var"] == "t2m"
    assert r["time_name"] == "valid_time"
    assert r["time_first"] == "1980-01-01T00:00:00"
    assert r["time_last"] == "1980-12-01T00:00:00"
    assert r["time_calendar"] == "proleptic_gregorian"
    assert 28 <= r["time_step_days"] <= 31


def test_ocean_time_and_hourly_step(other):
    r = check_file(other.p["bran_1"])
    assert r["time_name"] == "ocean_time" and r["data_vars"] == ["eta_t"]
    assert r["time_first"].startswith("1993-01-16")
    assert check_file(other.p["era_hr_0"])["time_step_days"] == pytest.approx(1 / 24)


def test_no_time_coordinate(other):
    r = check_file(other.p["prism"], deep=True)
    assert r["nc_status"] == "ok"
    # year/month are 1-D auxiliaries on t, not data
    assert r["data_vars"] == ["ppt"] and r["main_var"] == "ppt"
    assert r["time_name"] is None and r["time_first"] is None


def test_empty(other):
    r = check_file(other.p["gl_empty"])
    assert r["nc_status"] == "empty" and r["data_vars"] is None


def test_cmip_main_var_unchanged(messy_store):
    r = check_file(messy_store.paths["good_0"])
    assert r["main_var"] == "tas" and r["data_vars"] == ["tas"]
    assert r["time_name"] == "time"


# --- header gaps -------------------------------------------------------------


def _gaps(ncinfo_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    lf = pl.scan_parquet(ncinfo_dir / "ncinfo_all.parquet").with_columns(
        dir=dataset_dir_expr(), vars=pl.col("data_vars").list.join(",")
    )
    return header_gaps(lf, ["dir", "vars"])


def test_header_gaps(ncinfo):
    ds, issues = _gaps(ncinfo)
    by_dir = {(r["dir"], r["vars"]): r for r in ds.iter_rows(named=True)}
    gl = by_dir[("ocean_reanalysis/glorys12v1/*", "thetao,so,zos")]
    # year dirs folded into one dataset; one-step files: step from file starts
    assert gl["n_files"] == 4 and gl["step_days"] == pytest.approx(1.0)
    assert gl["gap_status"] == "gap" and gl["n_gaps"] == 2
    g, g2 = issues.filter(kind="gap", dir=gl["dir"]).iter_rows(named=True)
    assert g["prev_name"].endswith("19930102.nc") and g["delta_days"] == 2.0
    assert g2["next_name"].endswith("19940101.nc")
    # ERA5 monthly: time vs valid_time and gregorian vs proleptic_gregorian
    # files join cleanly; the stray partial file overlaps
    mm = by_dir[("era5/reanalysis/single-levels/monthly-means/2m_temperature", "t2m")]
    assert mm["calendar"] == "standard"
    assert mm["gap_status"] == "overlap" and mm["n_overlaps"] == 1
    hr = by_dir[("era5/reanalysis/single-levels/1hr/2m_temperature", "t2m")]
    # 1979-01-02 23:00 -> 1980-01-01: a year missing between daily and yearly files
    assert hr["gap_status"] == "gap"
    assert by_dir[("prism/netcdf/monthly", "ppt")]["gap_status"] == "fixed"
    assert by_dir[("ocean_reanalysis/bran2020/monthly", "eta_t")]["gap_status"] == "ok"


def _rows(**cols) -> pl.DataFrame:
    n = len(cols["time_first"])
    base = {
        "rel_path": [f"d/f{i}.nc" for i in range(n)],
        "nc_status": ["ok"] * n,
        "n_time": [1] * n,
        "time_calendar": ["standard"] * n,
        "time_step_days": [None] * n,
        "g": ["a"] * n,
    }
    return pl.DataFrame(
        {**base, **cols}, schema_overrides={"time_step_days": pl.Float64}
    )


@pytest.mark.parametrize(
    "firsts, lasts, cals, status",
    [
        (["2000-01-01", "2000-01-02"], ["2000-01-01", "2000-01-02"], None, "ok"),
        (["2000-01-01", "2000-01-01"], ["2000-01-01", "2000-01-01"], None, "duplicate"),
        (
            ["2000-01-01", "2000-01-02", "2000-01-03"],
            ["2000-01-01", "2000-01-02", "2000-01-03"],
            ["standard", "noleap", "standard"],
            "mixed_calendar",
        ),
        (
            # Feb 29 and 30 exist in 360_day: 02-29 -> 03-01 is a 2-day gap
            ["1990-02-28", "1990-02-29", "1990-03-01"],
            ["1990-02-28", "1990-02-29", "1990-03-01"],
            ["360_day"] * 3,
            "gap",
        ),
        (["2000-01-01", None], ["2000-01-01", None], None, "mixed_time_format"),
        # Feb 29 1990 does not exist in the standard calendar
        (
            ["1990-02-28", "1990-02-29"],
            ["1990-02-28", "1990-02-29"],
            None,
            "bad_time_label",
        ),
    ],
)
def test_header_gap_statuses(firsts, lasts, cals, status):
    cols = {"time_first": firsts, "time_last": lasts}
    if cals:
        cols["time_calendar"] = cals
    ds, _ = header_gaps(_rows(**cols), ["g"])
    assert ds["gap_status"].to_list() == [status]


def test_header_gap_overlap_with_within_file_step():
    df = _rows(
        time_first=["2000-01-01T00:00:00", "2000-01-01T12:00:00"],
        time_last=["2000-01-01T23:00:00", "2000-01-02T11:00:00"],
        n_time=[24, 24],
        time_step_days=[1 / 24, 1 / 24],
    )
    ds, issues = header_gaps(df, ["g"])
    assert ds["gap_status"].item() == "overlap"
    assert issues["delta_days"].item() == pytest.approx(-11 / 24)


# --- generic catalog ---------------------------------------------------------


@pytest.mark.parametrize(
    "step, freq",
    [
        (None, "unknown"),
        (1 / 24, "1hr"),
        (0.125, "3hr"),
        (0.25, "6hr"),
        (1.0, "day"),
        (5.0, "5day"),
        (28.0, "mon"),
        (31.0, "mon"),
        (365.0, "yr"),
        (1000.0, "irregular"),
    ],
)
def test_frequency_from_step(step, freq):
    assert generic.frequency_from_step(step) == freq


CATALOGS = {
    "schema_version": 1,
    "products": [
        {
            "name": "glorys12v1",
            "root": "ROOT/ocean_reanalysis/glorys12v1",
            "builder": "generic",
            "collection": "ocean_reanalysis",
            "output": "whoi_glorys12v1",
            "version": "12v1",
            "citation": "GLORYS12V1 citation",
            "license": "CMEMS licence",
        },
        {
            "name": "bran2020",
            "root": "ROOT/ocean_reanalysis/bran2020",
            "builder": "generic",
            "collection": "ocean_reanalysis",
            "output": "whoi_bran2020",
            # facet-wise: frequency from the first rule, level from the second
            "rules": [
                {"match": "^monthly/", "frequency": "mon"},
                {"match": "_eta_t_", "level": "sfc"},
            ],
        },
        {
            "name": "prism",
            "root": "ROOT/prism",
            "builder": "generic",
            "collection": "observations",
            "output": "whoi_prism",
            "rules": [{"match": r"^netcdf/monthly/", "frequency": "mon"}],
        },
    ],
}


def _catalogs(tmp_path: Path, root: Path, products=CATALOGS["products"]) -> Path:
    text = json.dumps({**CATALOGS, "products": products}).replace("ROOT", str(root))
    p = tmp_path / "catalogs.yaml"
    p.write_text(yaml.safe_dump(json.loads(text)))
    return p


def test_generic_build(other, ncinfo, tmp_path):
    m = load(_catalogs(tmp_path, other.root), CatalogsManifest)
    nci = generic.load_ncinfo(ncinfo)
    gl, prism = m.products[0], m.products[2]
    df, invalid = generic.build(gl, nci, jobs=1)
    assert df.schema == pl.Schema(CATALOG_SCHEMA)
    # invariant: catalog rows + invalid rows = .nc files under the root
    n_nc = len(list(Path(gl.root).rglob("*.nc")))
    assert df.height + invalid.height == n_nc == 5
    assert invalid.to_dicts() == [
        {"path": str(other.p["gl_empty"]), "reason": "nc_status=empty"}
    ]
    r = df.filter(pl.col("path").str.ends_with("19930104.nc")).row(0, named=True)
    assert r["variable_id"] == "thetao,so,zos"
    assert r["master_id"] == "ocean_reanalysis.glorys12v1.day.thetao-so-zos"
    assert (r["collection"], r["frequency"], r["level"], r["version"]) == (
        "ocean_reanalysis",
        "day",
        None,
        "12v1",
    )
    assert r["time_range"] == "19930104-19930104"
    assert df["master_id"].n_unique() == 1

    df, invalid = generic.build(prism, nci, jobs=1)
    (r,) = df.iter_rows(named=True)
    assert r["frequency"] == "mon"  # from the rule; no time axis in the file
    assert r["time_range"] is None and r["collection"] == "observations"
    assert r["master_id"] == "observations.prism.mon.ppt"


def test_generic_missing_ncinfo_row(other, ncinfo, tmp_path):
    m = load(_catalogs(tmp_path, other.root), CatalogsManifest)
    nci = pl.scan_parquet(ncinfo / "ncinfo_all.parquet").filter(
        ~pl.col("path").str.contains("bran2020_monthly_eta_t_1993_02")
    )
    df, invalid = generic.build(m.products[1], nci, jobs=1)
    assert df.height == 1
    assert invalid["reason"].to_list() == ["no ncinfo row: run check_nc"]
    assert df.select("frequency", "level").rows() == [("mon", "sfc")]
    bran = m.products[1].model_copy(update={"rules": []})
    df, _ = generic.build(bran, nci, jobs=1)
    # one single-step file left: no spacing to infer a frequency from
    assert df["frequency"].to_list() == ["unknown"]
    df, _ = generic.build(bran, generic.load_ncinfo(ncinfo), jobs=1)
    # two single-step files: frequency from the spacing of file starts
    assert df["frequency"].unique().to_list() == ["mon"]


def test_generic_cli_check_and_summary(other, ncinfo, tmp_path):
    cats = _catalogs(tmp_path, other.root)
    out = tmp_path / "next"
    assert (
        build_catalog.main(
            [
                "--manifest",
                str(cats),
                "--out",
                str(out),
                "--ncinfo",
                str(ncinfo),
                "--jobs",
                "1",
            ]
        )
        == 0
    )
    desc = json.loads((out / "whoi_bran2020.json").read_text())
    agg = desc["aggregation_control"]["aggregations"][1]
    assert agg["options"]["dim"] == "ocean_time"
    gl = json.loads((out / "whoi_glorys12v1.json").read_text())
    assert gl["aggregation_control"]["aggregations"][1]["options"]["dim"] == "time"
    inv = pl.read_parquet(out / "whoi_glorys12v1_invalid.parquet")
    assert inv["reason"].to_list() == ["nc_status=empty"]

    for name in ("whoi_glorys12v1", "whoi_bran2020", "whoi_prism"):
        res = check_catalog.run_checks(out / f"{name}.json", sample_datasets=5)
        assert all(r["ok"] for r in res), (name, res)

    site = tmp_path / "site"
    assert (
        summarize_catalog.main(
            [
                *[
                    a
                    for n in ("whoi_glorys12v1", "whoi_bran2020", "whoi_prism")
                    for a in ("--catalog", str(out / f"{n}.csv"))
                ],
                "--root",
                str(other.root),
                "--out",
                str(site),
                "--products",
                str(cats),
            ]
        )
        == 0
    )
    s = pl.read_json(site / "catalog_summary.json")
    gl = s.filter(product="glorys12v1").row(0, named=True)
    # dataset over two year dirs: relative_path is their parent
    assert gl["relative_path"] == "ocean_reanalysis/glorys12v1"
    assert gl["variable_id"] == "thetao,so,zos"
    assert gl["dataset_id"] == "ocean_reanalysis.glorys12v1.day.thetao-so-zos.12v1"
    assert (gl["time_start"], gl["time_end"], gl["n_files"]) == (
        "19930101",
        "19940101",
        4,
    )
    products = json.loads((site / "products.json").read_text())
    assert products["glorys12v1"]["citation"] == "GLORYS12V1 citation"
    stats = json.loads((site / "catalog_stats.json").read_text())
    assert stats["by_collection"] == {"observations": 1, "ocean_reanalysis": 2}
    files = json.loads((site / "files/ocean_reanalysis.glorys12v1.json").read_text())
    assert len(files[gl["dataset_id"]]) == 4


def test_catalogs_manifest_rules(tmp_path):
    base = {"name": "x", "root": "/a/x", "output": "o", "builder": "generic"}
    with pytest.raises(ValueError, match="need a collection"):
        CatalogsManifest.model_validate({"products": [base]})
    with pytest.raises(ValueError, match="generic only"):
        CatalogsManifest.model_validate(
            {
                "products": [
                    {**base, "builder": "cmip6", "rules": [{"match": "a"}]},
                ]
            }
        )
    with pytest.raises(ValueError, match="roots overlap"):
        CatalogsManifest.model_validate(
            {
                "products": [
                    {**base, "collection": "observations"},
                    {
                        **base,
                        "name": "y",
                        "output": "p",
                        "root": "/a/x/sub",
                        "collection": "observations",
                    },
                ]
            }
        )
    # a disabled generic entry may sit under a curated product's root
    CatalogsManifest.model_validate(
        {
            "products": [
                {**base, "collection": "observations"},
                {
                    **base,
                    "name": "y",
                    "output": "p",
                    "root": "/a/x/sub",
                    "collection": "observations",
                    "enabled": False,
                },
            ]
        }
    )
    with pytest.raises(ValueError):
        CatalogsManifest.model_validate(
            {"products": [{**base, "collection": "obs"}]}  # unknown collection
        )


# --- curated ERA5-style product (date tokens, aliases, no source) ------------

ERA5 = {
    "schema_version": 1,
    "product": "era5",
    "title": "ERA5",
    "version": "v1",
    "license": "Copernicus licence",
    "groups": [
        {
            "remote_dir": "single-levels/monthly-means",
            "frequency": "mon",
            "level": "sfc",
            "path_template": "{remote_dir}/{variable}/{file}",
            "file_pattern": "{year}_{variable}.nc",
            "variables": ["2m_temperature"],
            "years": [1979, 2025],
        },
        {
            "remote_dir": "single-levels/1hr",
            "frequency": "1hr",
            "level": "sfc",
            "path_template": "{remote_dir}/{variable}/{file}",
            "file_pattern": "{date:%Y-%m-%d}_{variable}.nc",
            "variables": ["2m_temperature"],
        },
        {
            "remote_dir": "single-levels/1hr",
            "frequency": "1hr",
            "level": "sfc",
            "path_template": "{remote_dir}/{variable}/{file}",
            "file_pattern": "{year}_{variable}.nc",
            "variables": ["2m_temperature"],
            "years": [1980, 2025],
        },
        {
            "remote_dir": "single-levels/3hr",
            "frequency": "3hr",
            "level": "sfc-1deg",
            "path_template": "{remote_dir}/{variable}/{file}",
            "file_pattern": "{variable}_{date:%Y-%m}.nc",
            "variables": ["T2m", "uv10m"],
            "aliases": {"T2m": "2m_temperature"},
        },
    ],
}


def test_dated_manifest_validation():
    m = ProductManifest.model_validate(ERA5)
    assert m.source is None
    assert m.groups[1].dated and not m.groups[0].dated
    # {date} groups are not expanded (catalog-only)
    assert {r["variable_id"] for r in m.expand()} == {"2m_temperature"}
    assert all(r["url"] is None for r in m.expand())
    bad = {**ERA5, "source": {"kind": "https_index", "base_url": "https://x.org/"}}
    with pytest.raises(ValueError, match="catalog-only"):
        ProductManifest.model_validate(bad)
    g = dict(ERA5["groups"][1])
    for pat in (
        "{date}_{variable}.nc",
        "{date:%Y/%m}_{variable}.nc",
        "{date:%b}_{variable}.nc",
    ):
        with pytest.raises(ValueError):
            ProductManifest.model_validate(
                {**ERA5, "groups": [{**g, "file_pattern": pat}]}
            )
    with pytest.raises(ValueError, match="aliases for unknown"):
        ProductManifest.model_validate(
            {**ERA5, "groups": [{**g, "aliases": {"nope": "x"}}]}
        )


def test_dated_reanalysis_build(other):
    spec = ProductManifest.model_validate(ERA5)
    root = other.root / "era5/reanalysis"  # not named after the product
    df, invalid = reanalysis.build(str(root), spec, jobs=1, collection="reanalysis")
    assert df.schema == pl.Schema(CATALOG_SCHEMA)
    assert [Path(p).name for p in invalid["path"]] == ["x.nc"]
    n_nc = len(list(root.rglob("*.nc")))
    assert df.height + invalid.height == n_nc
    by_name = {
        Path(r["path"]).relative_to(root / "single-levels").as_posix(): r
        for r in df.iter_rows(named=True)
    }
    r = by_name["1hr/2m_temperature/1979-01-02_2m_temperature.nc"]
    assert (r["time_range"], r["frequency"]) == ("19790102", "1hr")
    assert r["master_id"] == "reanalysis.era5.1hr.sfc.2m_temperature"
    # yearly 1hr file joins the daily ones in one dataset
    assert by_name["1hr/2m_temperature/1980_2m_temperature.nc"]["master_id"] in {
        r["master_id"] for r in by_name.values() if r["frequency"] == "1hr"
    }
    t2m = by_name["3hr/T2m/T2m_1979-01.nc"]
    assert t2m["variable_id"] == "2m_temperature"  # alias; file name unchanged
    assert t2m["master_id"] == "reanalysis.era5.3hr.sfc-1deg.2m_temperature"
    assert t2m["time_range"] == "197901"
    assert by_name["3hr/uv10m/uv10m_1979-01.nc"]["variable_id"] == "uv10m"
    mon = by_name["monthly-means/2m_temperature/1980_2m_temperature.nc"]
    assert (mon["frequency"], mon["time_range"]) == ("mon", "1980")


# --- inventory ---------------------------------------------------------------


def test_summarize_other(other, tmp_path):
    scans = []
    for tree in ("era5", "ocean_reanalysis", "cmems", "prism"):
        out = tmp_path / f"scan_{tree}"
        scan(other.root / tree, out)
        scans += ["--scan", str(out)]
    out = tmp_path / "inv"
    assert summarize_other.main([*scans, "--depth", "era5=3", "--out", str(out)]) == 0
    inv = pl.read_parquet(out / "inventory.parquet")
    rows = {(r["tree"], r["product"]): r for r in inv.iter_rows(named=True)}
    gl = rows[("ocean_reanalysis", "glorys12v1")]
    assert (gl["n_files"], gl["n_nc"], gl["n_empty_nc"], gl["n_hardlinked"]) == (
        5,
        5,
        1,
        1,
    )
    assert gl["owners"] == f"{os.getuid()}:5"
    pr = rows[("prism", "bil")]
    assert (pr["n_nc"], pr["n_other"], pr["other_suffixes"]) == (0, 1, ".zip:1")
    mm = rows[("era5", "reanalysis/single-levels/monthly-means")]
    assert (mm["n_nc"], mm["n_other"]) == (3, 1)  # + the .lck file
    dup = pl.read_parquet(out / "duplicates.parquet")
    hl = dup.filter(kind="hard_link")
    assert sorted(hl["tree"]) == ["cmems", "ocean_reanalysis"]
    same = dup.filter(kind="same_name")
    # the hard link has the same name in two trees; ERA5 1hr and monthly both
    # have a 1980_2m_temperature.nc
    assert set(same["name"]) == {
        other.p["gl_19930101"].name,
        "1980_2m_temperature.nc",
    }
    assert "inventory" not in (out / "inventory.md").read_text()
    assert (out / "inventory.md").read_text().startswith("| tree | product |")


def test_product_info_from_repo_manifest():
    info = summarize_catalog.product_info(
        Path(__file__).parent.parent / "manifests/catalogs.yaml"
    )
    assert "cmip6" not in info
    # reanalysis: keyed by the spec's product, acknowledgment from the spec
    assert info["20crv3"]["acknowledgment"].startswith("Support for the Twentieth")
    assert info["era5"]["citation"].startswith("Hersbach")
    assert info["glorys12v1"]["license"].startswith("Copernicus Marine")
