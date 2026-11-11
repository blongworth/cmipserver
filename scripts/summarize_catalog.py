"""Summarize intake-esm catalog CSVs into the site's dataset-level summary.

--catalog may be given more than once. A later catalog replaces every row of
earlier catalogs in the same file-list shard (collection + source_id or
product), so ``--catalog sample.csv --catalog whoi_20crv3.csv`` swaps the
sample's synthetic 20CRv3 for the real one and keeps the rest of the sample.

Only reads the catalogs; writes to --out:
    catalog_summary.json      one record per dataset (catalog_schema.SUMMARY_SCHEMA)
    catalog_summary.parquet   same, with --parquet
    catalog_stats.json        headline counts and the source catalogs
    files/<shard>.json        dataset_id -> file names, one shard per
                              source_id (cmip6) or product (reanalysis)

``relative_path`` is the dataset directory relative to --root. The summary
never contains absolute paths; the site adds prefixes from its config.

Example:
    uv run scripts/summarize_catalog.py \\
        --catalog tests/fixtures/sample_catalog.csv \\
        --catalog tests/fixtures/whoi_20crv3.csv --out site/data
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog_schema import (
    CATALOG_SCHEMA,
    DATASET_KEYS,
    DEFAULT_DATA_ROOT,
    SUMMARY_SCHEMA,
    dataset_id_expr,
)

GB = 1e9
# Catalog names (CSV stem, which is also the descriptor id) of synthetic catalogs.
SYNTHETIC_CATALOGS = {"sample_catalog"}


def shard_expr() -> pl.Expr:
    """File-list shard of a row: one per source_id (cmip6) or product."""
    return pl.format(
        "files/{}.{}.json", "collection", pl.coalesce("source_id", "product")
    )


def summarize(lf: pl.LazyFrame, root: str = DEFAULT_DATA_ROOT) -> pl.LazyFrame:
    prefix = root.rstrip("/") + "/"
    return (
        lf.with_columns(
            relative_path=pl.col("path")
            .str.strip_prefix(prefix)
            .str.replace(r"/[^/]*$", ""),
            time_start=pl.col("time_range").str.split("-").list.first(),
            time_end=pl.col("time_range").str.split("-").list.last(),
        )
        .group_by(DATASET_KEYS)
        .agg(
            time_start=pl.col("time_start").min(),
            time_end=pl.col("time_end").max(),
            n_files=pl.len(),
            size_gb=(pl.col("size_bytes").sum() / GB).round(3),
            relative_path=pl.col("relative_path").first(),
            n_dirs=pl.col("relative_path").n_unique(),
        )
        .with_columns(
            dataset_id_expr(),
            file_list=shard_expr(),
        )
        .sort("dataset_id")
    )


def file_lists(lf: pl.LazyFrame) -> dict[str, dict[str, list[str]]]:
    """{file_list shard: {dataset_id: [file names]}}."""
    df = (
        lf.with_columns(
            dataset_id_expr(),
            name=pl.col("path").str.extract(r"([^/]+)$"),
            shard=shard_expr(),
        )
        .group_by("shard", "dataset_id")
        .agg(pl.col("name").sort())
        .sort("shard", "dataset_id")
        .collect()
    )
    out: dict[str, dict[str, list[str]]] = {}
    for r in df.iter_rows(named=True):
        out.setdefault(r["shard"], {})[r["dataset_id"]] = r["name"]
    return out


def combine(catalogs: list[tuple[str, pl.DataFrame]]) -> pl.DataFrame:
    """Stack catalogs; a later one replaces earlier rows in shards it covers.

    Adds a ``catalog`` column with the name of the catalog each row came from.
    """
    out = pl.DataFrame(
        schema={**CATALOG_SCHEMA, "catalog": pl.String, "shard": pl.String}
    )
    for name, df in catalogs:
        df = df.with_columns(catalog=pl.lit(name), shard=shard_expr())
        out = pl.concat(
            [out.filter(~pl.col("shard").is_in(df["shard"].unique().implode())), df]
        )
    return out.drop("shard")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--catalog",
        action="append",
        required=True,
        help="intake-esm catalog CSV; repeat to combine (later ones win per shard)",
    )
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--root", default=DEFAULT_DATA_ROOT, help="data root prefix")
    ap.add_argument("--parquet", action="store_true", help="also write parquet")
    args = ap.parse_args(argv)

    prefix = args.root.rstrip("/") + "/"
    catalogs = []
    for path in args.catalog:
        name = Path(path).stem
        df = pl.read_csv(path, schema_overrides=CATALOG_SCHEMA)
        if (n_bad := df.filter(~pl.col("path").str.starts_with(prefix)).height) > 0:
            ap.error(f"{name}: {n_bad} paths do not start with --root {prefix}")
        if not df["path"].is_unique().all():
            ap.error(f"{name}: duplicate paths")
        ids = summarize(df.lazy(), args.root).select("dataset_id").collect()
        if not ids["dataset_id"].is_unique().all():
            ap.error(f"{name}: duplicate dataset_id")
        catalogs.append((name, df))
    if len({n for n, _ in catalogs}) < len(catalogs):
        ap.error("catalog names (file stems) must be unique")

    rows = combine(catalogs)
    lf = rows.drop("catalog").lazy()
    ds = summarize(lf, args.root).collect()
    if (n_split := ds.filter(pl.col("n_dirs") > 1).height) > 0:
        ap.error(f"{n_split} datasets span more than one directory")
    if not ds["dataset_id"].is_unique().all():
        ap.error("duplicate dataset_id across catalogs")
    ds = ds.select(list(SUMMARY_SCHEMA)).cast(SUMMARY_SCHEMA)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ds.write_json(out / "catalog_summary.json")
    files_dir = out / "files"
    files_dir.mkdir(exist_ok=True)
    for old in files_dir.glob("*.json"):
        old.unlink()
    for shard, lists in file_lists(lf).items():
        (out / shard).write_text(json.dumps(lists, separators=(",", ":")) + "\n")
    if args.parquet:
        ds.write_parquet(out / "catalog_summary.parquet")
    per_catalog = rows.select("catalog", *DATASET_KEYS).unique().group_by("catalog")
    n_by_catalog = dict(per_catalog.len().iter_rows())
    stats = {
        "catalogs": [
            {
                "name": name,
                "n_datasets": n_by_catalog.get(name, 0),
                "synthetic": name in SYNTHETIC_CATALOGS,
            }
            for name, _ in catalogs
        ],
        "n_datasets": ds.height,
        "n_files": int(ds["n_files"].sum()),
        "size_tb": round(float(ds["size_gb"].sum()) / 1000, 3),
        "by_collection": {
            r["collection"]: r["len"]
            for r in ds.group_by("collection").len().sort("collection").to_dicts()
        },
        "by_product": {
            r["product"]: r["len"]
            for r in ds.drop_nulls("product")
            .group_by("product")
            .len()
            .sort("product")
            .to_dicts()
        },
    }
    (out / "catalog_stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
