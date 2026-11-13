"""Generic catalog builder for non-CMIP products catalogued in place.

Tier 1 of the non-CMIP6 plan: any NetCDF product gets a catalog with facets
taken from the file headers, without a hand-written product manifest.

The crawl lists ``*.nc`` under the product root (ecgtools, like the other
builders). Nothing is opened: every file is joined by ``path`` to the
``ncinfo`` parquet written by ``check_nc.py`` (Phase 2), so the scan, the
check and the build must use the same mount prefix (``/proj/cmip6`` on HPC).

Per file:

- ``variable_id``: the file's ``data_vars`` joined with ``,``
  (``thetao,so,zos``). Not an intake-esm ``columns_with_iterables`` list:
  that path fails with intake-esm 2025.7.9 and polars 2 (``json_decode``
  needs a dtype), so the column stays a plain string.
- ``time_range``: ``YYYYMMDD-YYYYMMDD`` from ``time_first``/``time_last``.
- ``frequency``/``level``: each from the first matching path rule in
  ``catalogs.yaml`` that sets it;
  else ``frequency`` from the dataset's time step (``frequency_from_step``;
  ``unknown`` if one single-step file), ``fx`` if it has no time axis.

A dataset is a directory (year dirs folded to ``*``) plus the set of data
variables, the same grouping as ``find_gaps.py --ncinfo``.

Invalid (same contract as the cmip6 builder): ``nc_status != ok``
(``nc_status=<status>``), no ncinfo row (``no ncinfo row: run check_nc``),
and anything in ``exclude_paths`` (``excluded``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog.io import INVALID_SCHEMA
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA, master_id_expr
from whoi_cmip_reorg_code.gaps import dataset_dir_expr, header_gaps
from whoi_cmip_reorg_code.manifest import Product

CRAWL_DEPTH = 10
DEFAULT_VERSION = "1"
# (max step in days, frequency); first row with step <= max wins. Ranges are
# wide enough for month and year lengths.
_FREQUENCIES = (
    (0.75 / 24, "subhr"),
    (1.5 / 24, "1hr"),
    (4.5 / 24, "3hr"),
    (9 / 24, "6hr"),
    (1.5, "day"),
    (6, "5day"),
    (9, "week"),
    (35, "mon"),
    (100, "3mon"),
    (370, "yr"),
)


def frequency_from_step(step_days: float | None) -> str:
    """Frequency token for a time step in days (``unknown``: none, e.g. a
    dataset of one single-step file)."""
    if step_days is None:
        return "unknown"
    for top, freq in _FREQUENCIES:
        if step_days <= top:
            return freq
    return "irregular"


def _crawl(root: str, exclude_patterns: list[str], jobs: int) -> list[str]:
    from ecgtools import Builder

    b = Builder(
        paths=[root],
        depth=CRAWL_DEPTH,
        include_patterns=["*.nc"],
        exclude_patterns=exclude_patterns,
        joblib_parallel_kwargs={"n_jobs": jobs},
    )
    b.get_assets()
    return sorted(b.assets)


def _apply_rules(df: pl.DataFrame, prod: Product) -> pl.DataFrame:
    """Add ``rule_frequency`` and ``rule_level``: each from the first matching
    rule that sets it."""
    freq, level = [], []
    for rel in df["rel"].to_list():
        hits = [r for r in prod.rules if re.search(r.match, rel)]
        freq.append(next((r.frequency for r in hits if r.frequency), None))
        level.append(next((r.level for r in hits if r.level), None))
    return df.with_columns(
        rule_frequency=pl.Series(freq, dtype=pl.String),
        rule_level=pl.Series(level, dtype=pl.String),
    )


def build_from_paths(
    paths: list[str],
    root: str,
    prod: Product,
    ncinfo: pl.LazyFrame | pl.DataFrame,
    exclude_paths: set[str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(catalog, invalid) for ``paths`` (absolute, under ``root``)."""
    root = os.path.abspath(root)
    files = pl.DataFrame({"path": paths}, schema={"path": pl.String}).with_columns(
        rel=pl.col("path").str.strip_prefix(root.rstrip("/") + "/")
    )
    nci = ncinfo.lazy() if isinstance(ncinfo, pl.DataFrame) else ncinfo
    nci = (
        nci.select(
            "path",
            "nc_status",
            "size_bytes",
            "data_vars",
            "time_first",
            "time_last",
            "time_calendar",
            "time_step_days",
            "n_time",
        )
        .unique("path", keep="first")
        .collect()
    )
    j = files.join(nci, on="path", how="left")
    excluded = pl.Series(sorted(exclude_paths or ()), dtype=pl.String).implode()
    reason = (
        pl.when(pl.col("nc_status").is_null())
        .then(pl.lit("no ncinfo row: run check_nc"))
        .when(pl.col("nc_status") != "ok")
        .then(pl.format("nc_status={}", "nc_status"))
        .when(pl.col("path").is_in(excluded))
        .then(pl.lit("excluded"))
    )
    j = j.with_columns(reason=reason)
    invalid = j.filter(pl.col("reason").is_not_null()).select("path", pl.col("reason"))
    ok = j.filter(pl.col("reason").is_null()).drop("reason")
    if ok.is_empty():
        return pl.DataFrame(schema=CATALOG_SCHEMA), invalid.cast(INVALID_SCHEMA).sort(
            "path"
        )

    ok = ok.with_columns(
        dir=dataset_dir_expr("rel"), vars=pl.col("data_vars").list.join(",")
    )
    ds, _ = header_gaps(ok.with_columns(rel_path="rel"), ["dir", "vars"])
    ok = ok.join(ds.select("dir", "vars", "step_days"), on=["dir", "vars"], how="left")
    ok = _apply_rules(ok, prod)
    steps = ok["step_days"].to_list()
    has_time = ok["time_first"].is_not_null().to_list()
    auto = [frequency_from_step(s) if t else "fx" for s, t in zip(steps, has_time)]
    ok = ok.with_columns(auto_frequency=pl.Series(auto, dtype=pl.String))

    day = r"^(-?\d+)-(\d\d)-(\d\d).*$"
    df = ok.select(
        collection=pl.lit(prod.catalog_collection),
        path="path",
        product=pl.lit(prod.name),
        frequency=pl.coalesce("rule_frequency", "auto_frequency"),
        level="rule_level",
        variable_id=pl.col("data_vars").list.join(","),
        version=pl.lit(prod.version or DEFAULT_VERSION),
        time_range=pl.when(pl.col("time_first").is_not_null()).then(
            pl.concat_str(
                pl.col("time_first").str.replace(day, "${1}${2}${3}"),
                pl.col("time_last").str.replace(day, "${1}${2}${3}"),
                separator="-",
            )
        ),
        size_bytes="size_bytes",
    )
    schema = {k: v for k, v in CATALOG_SCHEMA.items() if k != "master_id"}
    df = (
        df.with_columns(
            pl.lit(None, t).alias(c) for c, t in schema.items() if c not in df.columns
        )
        .select([pl.col(c).cast(t) for c, t in schema.items()])
        .with_columns(master_id_expr())
        .select(list(CATALOG_SCHEMA))
        .sort("path")
    )
    return df, invalid.cast(INVALID_SCHEMA).sort("path")


def load_ncinfo(ncinfo_dir: str | os.PathLike) -> pl.LazyFrame:
    files = sorted(Path(ncinfo_dir).glob("ncinfo_*.parquet"))
    if not files:
        raise ValueError(f"no ncinfo_*.parquet in {ncinfo_dir}")
    return pl.scan_parquet(files)


def build(
    prod: Product,
    ncinfo: pl.LazyFrame | pl.DataFrame,
    jobs: int = -1,
    exclude_patterns: list[str] | None = None,
    exclude_paths: set[str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Crawl ``prod.root`` and build (catalog, invalid) from ``ncinfo``."""
    root = os.path.abspath(prod.root)
    if not os.path.isdir(root):
        raise ValueError(f"no such directory {root}")
    paths = _crawl(root, [*prod.exclude_patterns, *(exclude_patterns or [])], jobs)
    return build_from_paths(paths, root, prod, ncinfo, exclude_paths)


def time_dim(ncinfo: pl.LazyFrame | pl.DataFrame, paths: pl.Series) -> str:
    """Most common ``time_name`` among ``paths`` (default ``time``)."""
    nci = ncinfo.lazy() if isinstance(ncinfo, pl.DataFrame) else ncinfo
    top = (
        nci.filter(pl.col("path").is_in(paths.implode()))
        .drop_nulls("time_name")
        .group_by("time_name")
        .len()
        .sort("len", descending=True)
        .head(1)
        .collect()
    )
    return top["time_name"][0] if top.height else "time"
