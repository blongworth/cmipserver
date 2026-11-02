"""Summarize holdings_*.parquet shards into inventory outputs.

Writes to --out:
    totals.parquet                 one-row headline numbers (also printed)
    datasets.parquet               dataset-level inventory (+ .csv with --csv)
    multi_version.parquet          datasets present in more than one version
    conformance.parquet            files/bytes by drs_status x top_dir
    conformance_samples.parquet    sample paths per drs_status x top_dir
    unparsed_by_dir.parquet        .nc files failing the filename pattern, per dir
    unparsed_samples.parquet       sample unparseable .nc paths
    errors.parquet                 all scan errors concatenated

Example:
    uv run scripts/summarize_scan.py --scan scan/ --out inventory/ --csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

TB = 1e12
DATASET_KEYS = [
    "source_id",
    "experiment_id",
    "member_id",
    "table_id",
    "variable_id",
    "grid_label",
    "version",
]


def totals(lf: pl.LazyFrame) -> pl.DataFrame:
    real = ~pl.col("is_symlink")
    nc = pl.col("suffix") == ".nc"
    t = lf.select(
        n_entries=pl.len(),
        n_files=real.sum(),
        n_nc=(real & nc).sum(),
        n_nc_parsed=(real & nc & pl.col("facet_parse_ok")).sum(),
        n_drs_ok=(real & (pl.col("drs_status") == "ok")).sum(),
        apparent_tb=pl.col("size_bytes").filter(real).sum() / TB,
        allocated_tb=pl.col("alloc_bytes").filter(real).sum() / TB,
        n_symlinks=pl.col("is_symlink").sum(),
        n_broken_symlinks=(pl.col("is_symlink") & ~pl.col("link_resolves")).sum(),
        n_hardlinked=(real & (pl.col("nlink") > 1)).sum(),
    ).collect()
    uniq = (
        lf.filter(real)
        .unique(subset=["dev", "inode"])
        .select(
            n_unique_inodes=pl.len(),
            unique_inode_apparent_tb=pl.col("size_bytes").sum() / TB,
            unique_inode_allocated_tb=pl.col("alloc_bytes").sum() / TB,
        )
        .collect()
    )
    n_ds = (
        lf.filter(real & pl.col("facet_parse_ok"))
        .select(pl.struct(DATASET_KEYS).n_unique().alias("n_datasets"))
        .collect()
    )
    return pl.concat([t, uniq, n_ds], how="horizontal").with_columns(
        pct_nc_parsed=100 * pl.col("n_nc_parsed") / pl.col("n_nc")
    )


def datasets(lf: pl.LazyFrame) -> pl.LazyFrame:
    real = ~pl.col("is_symlink")
    return (
        lf.filter(pl.col("facet_parse_ok"))
        .group_by(DATASET_KEYS)
        .agg(
            n_files=real.sum(),
            n_symlinks=pl.col("is_symlink").sum(),
            size_bytes=pl.col("size_bytes").filter(real).sum(),
            alloc_bytes=pl.col("alloc_bytes").filter(real).sum(),
            time_start=pl.col("time_start").min(),
            time_end=pl.col("time_end").max(),
            activity_ids=pl.col("activity_id").drop_nulls().unique().sort(),
            institution_ids=pl.col("institution_id").drop_nulls().unique().sort(),
            top_dirs=pl.col("top_dir").unique().sort(),
            n_dirs=pl.col("rel_path").str.replace(r"/[^/]*$", "").n_unique(),
        )
        .sort(DATASET_KEYS, nulls_last=True)
    )


def multi_version(ds: pl.LazyFrame) -> pl.LazyFrame:
    keys = [k for k in DATASET_KEYS if k != "version"]
    return (
        ds.filter(pl.col("version").is_not_null())
        .group_by(keys)
        .agg(
            n_versions=pl.len(),
            versions=pl.col("version").sort(),
            n_files=pl.col("n_files").sum(),
            size_bytes=pl.col("size_bytes").sum(),
        )
        .filter(pl.col("n_versions") > 1)
        .sort(keys)
    )


def conformance(lf: pl.LazyFrame) -> pl.LazyFrame:
    return (
        lf.group_by("drs_status", "top_dir")
        .agg(
            n=pl.len(),
            size_bytes=pl.col("size_bytes").sum(),
            alloc_bytes=pl.col("alloc_bytes").sum(),
        )
        .sort("drs_status", "top_dir")
    )


def samples(lf: pl.LazyFrame, by: list[str], n: int) -> pl.LazyFrame:
    cols = [*by, "rel_path", "drs_depth", "expected_rel_path"]
    return lf.sort("rel_path").group_by(by).head(n).select(cols).sort(by)


def unparsed(lf: pl.LazyFrame) -> pl.LazyFrame:
    return lf.filter(pl.col("drs_status") == "bad_filename")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", required=True, help="dir with holdings_*.parquet")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--csv", action="store_true", help="also write datasets.csv")
    ap.add_argument("--samples", type=int, default=10, help="sample paths per group")
    args = ap.parse_args(argv)

    scan_dir, out = Path(args.scan), Path(args.out)
    shards = sorted(scan_dir.glob("holdings_*.parquet"))
    if not shards:
        ap.error(f"no holdings_*.parquet in {scan_dir}")
    out.mkdir(parents=True, exist_ok=True)
    lf = pl.scan_parquet(shards)

    tot = totals(lf)
    tot.write_parquet(out / "totals.parquet")

    ds = datasets(lf).collect()
    ds.write_parquet(out / "datasets.parquet")
    if args.csv:
        list_cols = [c for c, t in ds.schema.items() if isinstance(t, pl.List)]
        ds.with_columns(pl.col(list_cols).list.join(";")).write_csv(
            out / "datasets.csv"
        )
    multi_version(ds.lazy()).collect().write_parquet(out / "multi_version.parquet")

    conformance(lf).collect().write_parquet(out / "conformance.parquet")
    samples(
        lf.filter(pl.col("drs_status") != "ok"), ["drs_status", "top_dir"], args.samples
    ).collect().write_parquet(out / "conformance_samples.parquet")

    up = unparsed(lf)
    (
        up.with_columns(dir=pl.col("rel_path").str.replace(r"/[^/]*$", ""))
        .group_by("top_dir", "dir")
        .agg(
            n=pl.len(),
            size_bytes=pl.col("size_bytes").sum(),
            example=pl.col("name").sort().first(),
        )
        .sort("n", descending=True)
        .collect()
        .write_parquet(out / "unparsed_by_dir.parquet")
    )
    samples(up, ["top_dir"], args.samples).collect().write_parquet(
        out / "unparsed_samples.parquet"
    )

    errs = sorted(scan_dir.glob("errors_*.parquet"))
    if errs:
        pl.scan_parquet(errs).collect().write_parquet(out / "errors.parquet")

    with pl.Config(tbl_cols=-1, tbl_width_chars=200, tbl_rows=50):
        print(tot.transpose(include_header=True, column_names=["value"]))
        print(
            conformance(lf)
            .group_by("drs_status")
            .agg(pl.col("n").sum(), (pl.col("size_bytes").sum() / TB).alias("tb"))
            .sort("drs_status")
            .collect()
        )
    print(f"wrote inventory to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
