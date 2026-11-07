"""Generate a deterministic sample intake-esm catalog (no VAST access needed).

Writes to --out:
    sample_catalog.csv     file-level catalog (catalog_schema.CATALOG_SCHEMA)
    sample_catalog.json    intake-esm descriptor pointing at the CSV
    sample_data/...        tiny synthetic NetCDF files for one dataset, laid out
                           like the store, so notebooks can load data offline

Every ``path`` starts with --root (default /proj/cmip6). Only the files under
sample_data/ exist; all other rows are path-only. The NetCDF values are
synthetic and are labelled as such: they are not model output.

Example:
    uv run scripts/make_sample_catalog.py --out tests/fixtures
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog import io as catalog_io
from whoi_cmip_reorg_code.catalog_schema import (
    CATALOG_SCHEMA,
    CMIP6_FACETS,
    DEFAULT_DATA_ROOT,
    master_id_expr,
)

SEED = 20261105
CATALOG_NAME = "sample_catalog"

# (activity_id, experiment_id, first year, last year)
EXPERIMENTS = [
    ("CMIP", "historical", 1850, 2014),
    ("ScenarioMIP", "ssp585", 2015, 2100),
    ("CMIP", "piControl", 1850, 1949),
]
# (institution_id, source_id, grid_label)
SOURCES = [
    ("NCAR", "CESM2", "gn"),
    ("NOAA-GFDL", "GFDL-ESM4", "gr1"),
    ("MPI-M", "MPI-ESM1-2-HR", "gn"),
    ("IPSL", "IPSL-CM6A-LR", "gr"),
]
# (table_id, variable_id, years per file, "mon" or "day")
VARIABLES = [
    ("Amon", "tas", 50, "mon"),
    ("Amon", "pr", 50, "mon"),
    ("Amon", "psl", 50, "mon"),
    ("Omon", "tos", 20, "mon"),
    ("Omon", "thetao", 10, "mon"),
    ("Omon", "so", 10, "mon"),
    ("Oday", "tos", 10, "day"),
]
MEMBERS = ["r1i1p1f1", "r2i1p1f1", "r3i1p1f1"]
# ERA5: (frequency, variable_id) with one file per year
ERA5_VARIABLES = [("1hr", "t2m"), ("1hr", "msl"), ("mon", "t2m"), ("mon", "sst")]
ERA5_YEARS = (2015, 2024)
ERA5_VERSION = "v20261101"

# The one dataset that gets real (tiny, synthetic) files.
REAL_DATASET = {
    "activity_id": "CMIP",
    "institution_id": "NCAR",
    "source_id": "CESM2",
    "experiment_id": "historical",
    "member_id": "r1i1p1f1",
    "table_id": "Amon",
    "variable_id": "tas",
    "grid_label": "gn",
}


def _chunks(y0: int, y1: int, step: int) -> list[tuple[int, int]]:
    return [(a, min(a + step - 1, y1)) for a in range(y0, y1 + 1, step)]


def _time_range(a: int, b: int, kind: str) -> str:
    if kind == "day":
        return f"{a}0101-{b}1231"
    return f"{a}01-{b}12"


def _version(rng: random.Random) -> str:
    return f"v{rng.randint(2019, 2023)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"


def cmip6_rows(rng: random.Random, root: str) -> list[dict]:
    rows = []
    for activity, experiment, y0, y1 in EXPERIMENTS:
        for institution, source, grid in SOURCES:
            for table, variable, step, kind in VARIABLES:
                for member in MEMBERS:
                    facets = {
                        "activity_id": activity,
                        "institution_id": institution,
                        "source_id": source,
                        "experiment_id": experiment,
                        "member_id": member,
                        "table_id": table,
                        "variable_id": variable,
                        "grid_label": grid,
                    }
                    real = facets == REAL_DATASET
                    # keep every r1, about half of the other members
                    if not real and member != "r1i1p1f1" and rng.random() < 0.5:
                        continue
                    # some sources lack daily ocean output
                    if not real and table == "Oday" and rng.random() < 0.4:
                        continue
                    version = "v20190308" if real else _version(rng)
                    ddir = "/".join(
                        ["cmip6", *(facets[f] for f in CMIP6_FACETS), version]
                    )
                    for a, b in _chunks(y0, y1, step):
                        tr = _time_range(a, b, kind)
                        fn = (
                            f"{variable}_{table}_{source}_{experiment}_"
                            f"{member}_{grid}_{tr}.nc"
                        )
                        per_year = 3e7 if kind == "mon" else 9e8
                        if table == "Omon" and variable != "tos":
                            per_year *= 40  # 3-D ocean fields
                        size = int(per_year * (b - a + 1) * rng.uniform(0.6, 1.4))
                        rows.append(
                            dict(
                                collection="cmip6",
                                path=f"{root}/{ddir}/{fn}",
                                **facets,
                                version=version,
                                time_range=tr,
                                size_bytes=size,
                            )
                        )
    return rows


def era5_rows(rng: random.Random, root: str) -> list[dict]:
    rows = []
    for freq, variable in ERA5_VARIABLES:
        ddir = f"reanalysis/era5/{freq}/{variable}/{ERA5_VERSION}"
        for year in range(ERA5_YEARS[0], ERA5_YEARS[1] + 1):
            per_year = 1.9e10 if freq == "1hr" else 2.6e7
            rows.append(
                {
                    "collection": "reanalysis",
                    "path": f"{root}/{ddir}/era5_{variable}_{freq}_{year}.nc",
                    "product": "era5",
                    "frequency": freq,
                    "variable_id": variable,
                    "version": ERA5_VERSION,
                    "time_range": f"{year}01-{year}12"
                    if freq == "mon"
                    else f"{year}010100-{year}123123",
                    "size_bytes": int(per_year * rng.uniform(0.95, 1.05)),
                }
            )
    return rows


def make_catalog(root: str = DEFAULT_DATA_ROOT, seed: int = SEED) -> pl.DataFrame:
    rng = random.Random(seed)
    rows = cmip6_rows(rng, root) + era5_rows(rng, root)
    schema = {k: v for k, v in CATALOG_SCHEMA.items() if k != "master_id"}
    return (
        pl.DataFrame(rows, schema=schema)
        .with_columns(master_id_expr())
        .select(list(CATALOG_SCHEMA))
        .sort("path")
    )


def esm_descriptor(csv_name: str) -> dict:
    """intake-esm (esmcat 0.1.0) descriptor for the catalog CSV."""
    return catalog_io.esm_descriptor(
        csv_name,
        CATALOG_NAME,
        "SAMPLE WHOI CMIP catalog for development and tests. Synthetic: "
        "paths do not exist except for one dataset in sample_data/.",
    )


def write_real_files(cat: pl.DataFrame, root: str, data_dir: Path) -> list[Path]:
    """Write tiny synthetic NetCDF files for REAL_DATASET under data_dir."""
    import numpy as np
    import xarray as xr

    sel = cat.filter(
        *(pl.col(k) == v for k, v in REAL_DATASET.items()), collection="cmip6"
    ).sort("time_range")
    lat = np.array([-67.5, -22.5, 22.5, 67.5])
    lon = np.arange(0.0, 360.0, 45.0)
    written = []
    for row in sel.iter_rows(named=True):
        a, b = (int(x[:4]) for x in row["time_range"].split("-"))
        n = (b - a + 1) * 12
        start = (a - 1850) * 365.0
        # mid-month on a 365-day (noleap) calendar
        mdays = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
        mid = np.cumsum(mdays) - mdays / 2
        t = start + (np.arange(n) // 12) * 365.0 + np.tile(mid, n // 12)
        years = a + np.arange(n) / 12
        clim = 288.0 - 30.0 * np.sin(np.deg2rad(np.abs(lat)))
        season = 8.0 * np.sin(2 * np.pi * (np.arange(n) % 12) / 12)
        trend = 0.008 * np.clip(years - 1950, 0, None)
        tas = (
            clim[None, :, None]
            + season[:, None, None] * np.sign(lat)[None, :, None]
            + trend[:, None, None]
        )
        tas = np.broadcast_to(tas, (n, lat.size, lon.size)).astype("float32")
        ds = xr.Dataset(
            {
                "tas": (
                    ("time", "lat", "lon"),
                    tas,
                    {
                        "standard_name": "air_temperature",
                        "long_name": "Near-Surface Air Temperature",
                        "units": "K",
                    },
                )
            },
            coords={
                "time": (
                    "time",
                    t,
                    {"units": "days since 1850-01-01", "calendar": "noleap"},
                ),
                "lat": ("lat", lat, {"units": "degrees_north"}),
                "lon": ("lon", lon, {"units": "degrees_east"}),
            },
            attrs={
                "title": "SYNTHETIC sample data for WHOI CMIP site tests",
                "comment": "Not model output. Generated by make_sample_catalog.py.",
                **{k: v for k, v in REAL_DATASET.items()},
                "Conventions": "CF-1.7",
            },
        )
        rel = row["path"].removeprefix(root).lstrip("/")
        out = data_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        ds.to_netcdf(out, encoding={"tas": {"zlib": True, "complevel": 4}})
        written.append(out)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--root", default=DEFAULT_DATA_ROOT, help="path prefix")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument(
        "--no-data", action="store_true", help="skip writing sample NetCDF files"
    )
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root = args.root.rstrip("/")
    cat = make_catalog(root, args.seed)
    csv_name = f"{CATALOG_NAME}.csv"
    cat.write_csv(out / csv_name)
    (out / f"{CATALOG_NAME}.json").write_text(
        json.dumps(esm_descriptor(csv_name), indent=2) + "\n"
    )
    n_real = 0
    if not args.no_data:
        n_real = len(write_real_files(cat, root, out / "sample_data"))
    print(f"wrote {cat.height} rows ({n_real} real files) to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
