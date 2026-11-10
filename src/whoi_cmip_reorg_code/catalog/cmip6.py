"""CMIP6 catalog builder: ecgtools crawl + path-only parser.

The parser never opens files: facets come from the DRS path and filename
(``drs.py``), size from ``stat``. Anything not DRS-conformant is an invalid
asset (written to a review file, not the catalog).
"""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code import drs
from whoi_cmip_reorg_code.catalog.io import INVALID_SCHEMA, from_builder
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA


def parse_cmip6(path: str, drs_root: str) -> dict:
    """ecgtools parsing function. ``drs_root`` holds the activity_id dirs."""
    from ecgtools.builder import INVALID_ASSET, TRACEBACK

    try:
        rel = Path(path).relative_to(drs_root).as_posix()
    except ValueError:
        return {INVALID_ASSET: path, TRACEBACK: f"not under {drs_root}"}
    chk = drs.drs_check(rel)
    if chk["drs_status"] != "ok":
        return {INVALID_ASSET: path, TRACEBACK: f"drs_status={chk['drs_status']}"}
    fn = drs.parse_filename(os.path.basename(path))
    pf = drs.parse_path_facets(rel, fn)
    try:
        size = os.stat(path).st_size
    except OSError as exc:
        return {INVALID_ASSET: path, TRACEBACK: f"stat: {exc}"}
    tr = None
    if fn["time_start"]:
        tr = f"{fn['time_start']}-{fn['time_end']}" + (
            "-clim" if fn["climatology"] else ""
        )
    return {
        "collection": "cmip6",
        "path": path,
        "activity_id": pf["activity_id"],
        "institution_id": pf["institution_id"],
        **{k: fn[k] for k in drs.FILENAME_FACETS},
        "version": pf["version"],
        "time_range": tr,
        "size_bytes": size,
    }


def build(
    roots: list[str],
    drs_root: str,
    jobs: int = -1,
    exclude_patterns: list[str] | None = None,
    exclude_paths: set[str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Crawl ``roots`` (the DRS root or subtrees of it). Returns (catalog,
    invalid). ``exclude_paths`` (e.g. files whose ``nc_status`` isn't ok)
    are moved to ``invalid`` with reason ``excluded``."""
    from ecgtools import Builder

    drs_root = os.path.abspath(drs_root)
    roots = [os.path.abspath(r) for r in roots]
    for r in roots:
        if r != drs_root and not Path(r).is_relative_to(drs_root):
            raise ValueError(f"root {r} is not under drs_root {drs_root}")
    b = Builder(
        paths=roots,
        depth=drs.DRS_DEPTH,
        include_patterns=["*.nc"],
        exclude_patterns=exclude_patterns or [],
        joblib_parallel_kwargs={"n_jobs": jobs},
    )
    b.get_assets()
    if not b.assets:
        return pl.DataFrame(schema=CATALOG_SCHEMA), pl.DataFrame(schema=INVALID_SCHEMA)
    b.parse(parsing_func=parse_cmip6, parsing_func_kwargs={"drs_root": drs_root})
    return from_builder(b, exclude_paths)
