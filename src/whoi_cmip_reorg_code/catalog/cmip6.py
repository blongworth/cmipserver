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
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA, master_id_expr

INVALID_SCHEMA = {"path": pl.String, "reason": pl.String}


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
    b.clean_dataframe()
    invalid = pl.DataFrame(schema=INVALID_SCHEMA)
    inv = getattr(b, "invalid_assets", None)
    if inv is not None and len(inv):
        invalid = pl.from_pandas(inv).select(
            pl.col("INVALID_ASSET").alias("path"), pl.col("TRACEBACK").alias("reason")
        )
    if b.df.empty:
        return pl.DataFrame(schema=CATALOG_SCHEMA), invalid
    df = pl.from_pandas(b.df)
    schema = {k: v for k, v in CATALOG_SCHEMA.items() if k != "master_id"}
    df = df.with_columns(
        pl.lit(None, t).alias(c) for c, t in schema.items() if c not in df.columns
    ).select([pl.col(c).cast(t) for c, t in schema.items()])
    if exclude_paths:
        ex = pl.Series(sorted(exclude_paths), dtype=pl.String)
        invalid = pl.concat(
            [
                invalid,
                df.filter(pl.col("path").is_in(ex.implode())).select(
                    "path", reason=pl.lit("excluded")
                ),
            ]
        )
        df = df.filter(~pl.col("path").is_in(ex.implode()))
    df = (
        df.with_columns(master_id_expr())
        .select(list(CATALOG_SCHEMA))
        .unique("path", keep="first")
        .sort("path")
    )
    return df, invalid.sort("path")
