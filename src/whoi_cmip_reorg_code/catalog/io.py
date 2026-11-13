"""Write an intake-esm catalog (CSV + esmcat JSON descriptor) atomically."""

from __future__ import annotations

import json
import os
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA, master_id_expr

INVALID_SCHEMA = {"path": pl.String, "reason": pl.String}


def esm_descriptor(
    csv_name: str,
    catalog_id: str,
    description: str,
    time_dim: str = "time",
) -> dict:
    """intake-esm (esmcat 0.1.0) descriptor for a ``CATALOG_SCHEMA`` CSV.

    ``time_dim``: the dimension files are joined along (BRAN:
    ``ocean_time``)."""
    return {
        "esmcat_version": "0.1.0",
        "id": catalog_id,
        "description": description,
        "catalog_file": csv_name,
        "attributes": [
            {"column_name": c, "vocabulary": ""}
            for c in CATALOG_SCHEMA
            if c not in ("master_id", "path", "size_bytes")
        ],
        "assets": {"column_name": "path", "format": "netcdf"},
        "aggregation_control": {
            "variable_column_name": "variable_id",
            # CMIP6 facets are null on reanalysis rows and intake-esm rejects
            # partly-null groupby columns, so group on master_id + version.
            # Keys are then "<master_id>.<version>" (= summary dataset_id).
            # (A single groupby column breaks key lookup with pandas 3.)
            "groupby_attrs": ["master_id", "version"],
            "aggregations": [
                {"type": "union", "attribute_name": "variable_id"},
                {
                    "type": "join_existing",
                    "attribute_name": "time_range",
                    "options": {
                        "dim": time_dim,
                        "coords": "minimal",
                        "compat": "override",
                    },
                },
            ],
        },
    }


def write_catalog(
    df: pl.DataFrame,
    out_dir: str | os.PathLike,
    name: str,
    description: str,
    time_dim: str = "time",
) -> tuple[Path, Path]:
    """Write ``<name>.csv`` and ``<name>.json`` to ``out_dir`` (temp file, then
    rename each). ``df`` must have exactly the ``CATALOG_SCHEMA`` columns."""
    if df.schema != pl.Schema(CATALOG_SCHEMA):
        raise ValueError(
            f"catalog columns/types differ from CATALOG_SCHEMA: {df.schema}"
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv, js = out / f"{name}.csv", out / f"{name}.json"
    tmp_csv, tmp_js = csv.with_name(csv.name + ".tmp"), js.with_name(js.name + ".tmp")
    df.write_csv(tmp_csv)
    tmp_js.write_text(
        json.dumps(
            esm_descriptor(csv.name, name, description, time_dim),
            indent=2,
        )
        + "\n"
    )
    tmp_csv.replace(csv)
    tmp_js.replace(js)
    return csv, js


def from_builder(
    b, exclude_paths: set[str] | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(catalog, invalid) from a parsed ecgtools ``Builder``: ``CATALOG_SCHEMA``
    columns with ``master_id``, one row per path. ``exclude_paths`` move to
    ``invalid`` with reason ``excluded``."""
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
