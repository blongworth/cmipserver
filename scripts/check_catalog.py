"""Check a built intake-esm catalog. Reports only; never changes anything.

Checks: columns/types match CATALOG_SCHEMA, paths unique, key columns not
null, a sample of paths exists, intake-esm opens the catalog and
to_dataset_dict() loads a few sample datasets (a dataset that opens only
with ``normalize_time`` passes, and the detail says so), and the dataset count did not
drop more than --max-drop vs the last good catalog (--previous).

Exit 0 if every check passes, else 1. --report writes the results as JSON.

Example:
    uv run scripts/check_catalog.py --catalog catalog/next/whoi_cmip6.json \
        --previous catalog/live/whoi_cmip6.json --report runs/check.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import warnings
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA

# Sample datasets are opened read-only. HDF5 file locking fails on VAST/NFS and
# makes good files fail to open. Must be set before HDF5 loads (lazy imports).
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

REQUIRED = ("master_id", "collection", "path", "version")


def _intake_errors() -> tuple[type[BaseException], ...]:
    from intake_esm.source import ESMDataSourceError

    # ESMDataSourceError wraps per-dataset load failures in to_dataset_dict
    return (
        ESMDataSourceError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        IndexError,
        AttributeError,
    )


INTAKE_ERRORS = _intake_errors()


def normalize_time(ds):
    """``preprocess`` for datasets that mix NetCDF time formats: ERA5 files
    from the old CDS use ``time`` (calendar ``gregorian``), files from the new
    CDS use ``valid_time`` (``proleptic_gregorian``) plus ``number`` and
    ``expver``. Rename, drop the extras, and make the calendars equal (they
    are the same after 1582). Meant for users too (docs/audit-findings.md)."""
    if "valid_time" in ds.dims:
        ds = ds.rename(valid_time="time")
    ds = ds.drop_vars(["number", "expver"], errors="ignore")
    if "time" in ds.dims and ds["time"].dtype == object:  # cftime
        ds = ds.convert_calendar("standard", dim="time", use_cftime=True)
    return ds


def _csv(json_path: Path) -> Path:
    desc = json.loads(json_path.read_text())
    return json_path.parent / desc["catalog_file"]


def _read(json_path: Path) -> pl.DataFrame:
    return pl.read_csv(_csv(json_path), infer_schema_length=0)


def run_checks(
    catalog: Path,
    previous: Path | None = None,
    sample_paths: int = 200,
    sample_datasets: int = 3,
    max_drop: float = 0.05,
    seed: int = 0,
    datasets: list[str] | None = None,
) -> list[dict]:
    rng = random.Random(seed)
    results: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "ok": bool(ok), "detail": detail})

    raw = _read(catalog)
    cols = list(raw.columns)
    add("columns", cols == list(CATALOG_SCHEMA), f"got {cols}")
    try:
        df = raw.select(
            [pl.col(c).cast(t, strict=True) for c, t in CATALOG_SCHEMA.items()]
        )
        add("types", True)
    except (pl.exceptions.PolarsError, KeyError) as exc:
        add("types", False, str(exc).splitlines()[0])
        return results
    n_dup = df.height - df["path"].n_unique()
    add("unique_paths", n_dup == 0, f"{n_dup} duplicate paths")
    nulls = {c: df[c].null_count() for c in REQUIRED}
    add("required_not_null", not any(nulls.values()), json.dumps(nulls))

    paths = df["path"].to_list()
    sample = rng.sample(paths, min(sample_paths, len(paths)))
    missing = [p for p in sample if not os.path.exists(p)]
    add(
        "sample_paths_exist",
        not missing,
        f"{len(missing)}/{len(sample)} missing; e.g. {missing[:3]}",
    )

    n_ds = df.select("master_id", "version").n_unique() if df.height else 0
    if previous is not None and previous.exists():
        prev = _read(previous)
        p_ds = prev.select("master_id", "version").n_unique() if prev.height else 0
        ok = n_ds >= (1 - max_drop) * p_ds
        add(
            "dataset_count_vs_previous",
            ok,
            f"{p_ds} -> {n_ds} datasets ({prev.height} -> {df.height} files)",
        )
    else:
        add("dataset_count_vs_previous", True, f"no previous; {n_ds} datasets")

    import dask
    import intake

    # dask's threaded scheduler opens a dataset's files from several threads at
    # once; on hydra the netCDF4/HDF5 build segfaults (exit 139) when it does.
    # Open one file at a time.
    with warnings.catch_warnings(), dask.config.set(scheduler="synchronous"):
        warnings.simplefilter("ignore")
        try:
            cat = intake.open_esm_datastore(str(catalog))
            keys = list(cat.keys())
        except INTAKE_ERRORS as exc:
            add("intake_open", False, f"{type(exc).__name__}: {exc}")
            return results
        add("intake_open", len(keys) == n_ds, f"{len(keys)} keys, {n_ds} datasets")
        if datasets:
            unknown = [k for k in datasets if k not in keys]
            add("dataset_keys", not unknown, f"unknown: {unknown[:3]}")
            chosen = [k for k in datasets if k in keys]
        else:
            chosen = rng.sample(keys, min(sample_datasets, len(keys)))
        for key in chosen:
            try:
                # long runs (piControl past year 2262) mix pandas and cftime
                # time types unless cftime is forced; users need this too
                cat[key](xarray_open_kwargs={"use_cftime": True}).to_dask()
                add("to_dataset_dict", True, key)
                continue
            except INTAKE_ERRORS as exc:
                msg = str(exc).strip().splitlines()[0]
                plain = f"{key}: {type(exc).__name__}: {msg}"
            # known case: files in two time formats; passes only with the
            # documented preprocess, which the detail records
            try:
                cat[key](
                    xarray_open_kwargs={"use_cftime": True}, preprocess=normalize_time
                ).to_dask()
                add("to_dataset_dict", True, f"{key} (only with preprocess)")
            except INTAKE_ERRORS:
                add("to_dataset_dict", False, plain)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalog", required=True, help="catalog JSON")
    ap.add_argument("--previous", help="last good catalog JSON")
    ap.add_argument("--sample-paths", type=int, default=200)
    ap.add_argument("--sample-datasets", type=int, default=3)
    ap.add_argument("--max-drop", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--dataset",
        action="append",
        help="open this dataset key instead of a random sample (repeatable); "
        "e.g. reanalysis.era5.mon.plev.geopotential.v1",
    )
    ap.add_argument("--report", help="write results JSON here")
    args = ap.parse_args(argv)

    res = run_checks(
        Path(args.catalog),
        Path(args.previous) if args.previous else None,
        args.sample_paths,
        args.sample_datasets,
        args.max_drop,
        args.seed,
        args.dataset,
    )
    for r in res:
        print(f"{'PASS' if r['ok'] else 'FAIL'} {r['check']}: {r['detail']}")
    if args.report:
        Path(args.report).write_text(json.dumps(res, indent=2) + "\n")
    return 0 if all(r["ok"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
