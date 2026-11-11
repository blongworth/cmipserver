"""Column schemas for the intake-esm catalog and the site's dataset summary.

Single source of truth shared by the sample catalog generator, the site
summary (``scripts/summarize_catalog.py``), and the tests. A future
``build_catalog.py`` should write ``CATALOG_SCHEMA`` columns too.

CMIP6 facet names come from ``drs.DRS_DIR_FACETS``. Reanalysis rows use
``product``, ``frequency``, ``level`` and ``variable_id``; CMIP6-only facets
are null for them, and vice versa. ``level`` (e.g. ``2m``, ``sfc``, ``prs``)
separates reanalysis datasets that reuse one variable name at several levels
(20CRv3 ``air``); ``variable_id`` stays the native NetCDF variable name.
"""

from __future__ import annotations

import polars as pl

from whoi_cmip_reorg_code import drs

COLLECTIONS = ("cmip6", "reanalysis")

# DRS directory facets except version (version is common to all collections).
CMIP6_FACETS = drs.DRS_DIR_FACETS[:-1]
REANALYSIS_FACETS = ("product", "frequency", "level", "variable_id")
FACETS = (*CMIP6_FACETS, *(f for f in REANALYSIS_FACETS if f not in CMIP6_FACETS))

# File level, one row per NetCDF file (intake-esm CSV).
CATALOG_SCHEMA = {
    "master_id": pl.String,
    "collection": pl.String,
    "path": pl.String,
    **{f: pl.String for f in FACETS},
    "version": pl.String,
    "time_range": pl.String,
    "size_bytes": pl.Int64,
}

# Columns that identify one dataset (down to version).
DATASET_KEYS = ["collection", *FACETS, "version"]


def master_id_expr() -> pl.Expr:
    """ESGF-style unversioned dataset id (ESGF ``master_id``), e.g.
    ``CMIP6.CMIP.NCAR.CESM2.historical.r1i1p1f1.Amon.tas.gn`` or
    ``reanalysis.20crv3.3hr.2m.air``."""
    cmip6 = pl.concat_str(
        [pl.lit("CMIP6"), *(pl.col(f) for f in CMIP6_FACETS)], separator="."
    )
    rean = pl.concat_str(
        [pl.lit("reanalysis"), *(pl.col(f) for f in REANALYSIS_FACETS)],
        separator=".",
    )
    return (
        pl.when(pl.col("collection") == "cmip6")
        .then(cmip6)
        .otherwise(rean)
        .alias("master_id")
    )


def dataset_id_expr() -> pl.Expr:
    """Versioned dataset id (ESGF ``instance_id``): ``<master_id>.<version>``.

    This is also the intake-esm group key, because the descriptor groups on
    ``["master_id", "version"]`` joined with ``.``."""
    return pl.concat_str([master_id_expr(), pl.col("version")], separator=".").alias(
        "dataset_id"
    )


# Dataset level, one row per dataset (site/data/catalog_summary.json).
# relative_path is relative to the data root (e.g. "cmip6/CMIP/..."); it never
# contains an absolute path. The site builds VAST paths and URLs from prefixes.
# file_list is the site-relative JSON shard (one per source_id or product)
# mapping dataset_id -> file names, fetched on demand for download lists.
SUMMARY_SCHEMA = {
    "dataset_id": pl.String,
    "collection": pl.String,
    **{f: pl.String for f in FACETS},
    "version": pl.String,
    "time_start": pl.String,
    "time_end": pl.String,
    "n_files": pl.UInt32,
    "size_gb": pl.Float64,
    "relative_path": pl.String,
    "file_list": pl.String,
}

# Default data root; the catalog's ``path`` column starts with this. Notebooks
# and the site swap it for another prefix (HTTPS, a local copy).
DEFAULT_DATA_ROOT = "/proj/cmip6/data"
