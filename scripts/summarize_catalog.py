"""Summarize an intake-esm catalog CSV into the site's dataset-level summary.

Only reads the catalog; writes to --out:
    catalog_summary.json      one record per dataset (catalog_schema.SUMMARY_SCHEMA)
    catalog_summary.parquet   same, with --parquet
    catalog_stats.json        headline counts for the site
    files/<shard>.json        dataset_id -> file names, one shard per
                              source_id (cmip6) or product (reanalysis)

``relative_path`` is the dataset directory relative to --root. The summary
never contains absolute paths; the site adds prefixes from its config.

Example:
    uv run scripts/summarize_catalog.py \\
        --catalog /proj/cmip6/catalog/live/whoi_cmip6.csv \\
        --catalog /proj/cmip6/catalog/live/whoi_20crv3.csv --out site/data
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
            file_list=pl.format(
                "files/{}.{}.json",
                "collection",
                pl.coalesce("source_id", "product"),
            ),
        )
        .sort("dataset_id")
    )


def file_lists(lf: pl.LazyFrame) -> dict[str, dict[str, list[str]]]:
    """{file_list shard: {dataset_id: [file names]}}."""
    df = (
        lf.with_columns(
            dataset_id_expr(),
            name=pl.col("path").str.extract(r"([^/]+)$"),
            shard=pl.format(
                "files/{}.{}.json",
                "collection",
                pl.coalesce("source_id", "product"),
            ),
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--catalog",
        required=True,
        action="append",
        help="intake-esm catalog CSV (repeat to combine, e.g. cmip6 + reanalysis)",
    )
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--root", default=DEFAULT_DATA_ROOT, help="data root prefix")
    ap.add_argument("--parquet", action="store_true", help="also write parquet")
    args = ap.parse_args(argv)

    lf = pl.concat(
        [pl.scan_csv(c, schema_overrides=CATALOG_SCHEMA) for c in args.catalog]
    )
    prefix = args.root.rstrip("/") + "/"
    bad = lf.filter(~pl.col("path").str.starts_with(prefix)).select(pl.len())
    if (n_bad := bad.collect().item()) > 0:
        ap.error(f"{n_bad} catalog paths do not start with --root {prefix}")

    ds = summarize(lf, args.root).collect()
    if (n_split := ds.filter(pl.col("n_dirs") > 1).height) > 0:
        ap.error(f"{n_split} datasets span more than one directory")
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
    stats = {
        "catalog": ", ".join(Path(c).name for c in args.catalog),
        "n_datasets": ds.height,
        "n_files": int(ds["n_files"].sum()),
        "size_tb": round(float(ds["size_gb"].sum()) / 1000, 3),
        "by_collection": {
            r["collection"]: r["len"]
            for r in ds.group_by("collection").len().sort("collection").to_dicts()
        },
    }
    (out / "catalog_stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
