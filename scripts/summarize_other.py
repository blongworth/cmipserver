"""Per-product inventory of non-CMIP6 trees from holdings scans (offline).

Reads ``holdings_*.parquet`` from one or more scan dirs (one per tree:
``era5``, ``ocean_reanalysis``, ``cmems``, ``prism``). DRS columns are
ignored. The tree is the scan root's last component (``path`` minus
``rel_path``); the product is the first ``--depth`` components of
``rel_path`` (default 1; e.g. ``--depth era5=3`` for
``reanalysis/single-levels/1hr``). Files directly under a tree root are
product ``.``.

Writes to --out:
    inventory.parquet     one row per product (also inventory.md, a table)
    duplicates.parquet    real files whose name appears in more than one
                          product, or that share an inode with another path
    owners.parquet        files and bytes per product and uid

Example:
    uv run scripts/summarize_other.py --scan $R/scan_era5 --scan $R/scan_ocean \\
        --depth era5=3 --out $R/inventory
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

TB = 1e12


def load(scan_dirs: list[str]) -> pl.LazyFrame:
    files = [p for d in scan_dirs for p in sorted(Path(d).glob("holdings_*.parquet"))]
    if not files:
        raise ValueError(f"no holdings_*.parquet in {scan_dirs}")
    lf = pl.scan_parquet(files, missing_columns="insert")
    root = pl.col("path").str.head(
        pl.col("path").str.len_chars() - pl.col("rel_path").str.len_chars() - 1
    )
    return lf.with_columns(tree=root.str.extract(r"([^/]+)$"))


def with_product(lf: pl.LazyFrame, depth: dict[str, int], default: int) -> pl.LazyFrame:
    parts = pl.col("rel_path").str.split("/")
    n = (
        pl.col("tree")
        .replace_strict(depth, default=default, return_dtype=pl.Int64)
        .alias("_d")
    )
    # Directories only: never use the file name itself as the product.
    k = pl.min_horizontal(pl.col("_d"), parts.list.len() - 1)
    return (
        lf.with_columns(n)
        .with_columns(
            product=pl.when(k > 0)
            .then(parts.list.head(k).list.join("/"))
            .otherwise(pl.lit("."))
        )
        .drop("_d")
    )


def inventory(lf: pl.LazyFrame) -> pl.DataFrame:
    real = pl.col("file_type") == "file"
    nc = real & (pl.col("suffix") == ".nc")
    other = real & (pl.col("suffix") != ".nc")
    main = (
        lf.group_by("tree", "product")
        .agg(
            n_entries=pl.len(),
            n_files=real.sum(),
            n_nc=nc.sum(),
            n_other=other.sum(),
            size_tb=(pl.col("size_bytes").filter(real).sum() / TB).round(4),
            alloc_tb=(pl.col("alloc_bytes").filter(real).sum() / TB).round(4),
            mtime_first=pl.col("mtime").filter(real).min().dt.year(),
            mtime_last=pl.col("mtime").filter(real).max().dt.year(),
            n_empty=(real & (pl.col("size_bytes") == 0)).sum(),
            n_empty_nc=(nc & (pl.col("size_bytes") == 0)).sum(),
            n_symlinks=pl.col("is_symlink").sum(),
            n_broken_symlinks=(
                pl.col("is_symlink") & ~pl.col("link_resolves").fill_null(False)
            ).sum(),
            n_hardlinked=(real & (pl.col("nlink") > 1)).sum(),
            n_hidden=pl.col("name").str.starts_with(".").sum(),
        )
        .collect()
    )
    suffixes = (
        lf.filter(pl.col("file_type") == "file", pl.col("suffix") != ".nc")
        .group_by("tree", "product", "suffix")
        .len()
        .sort("len", descending=True)
        .group_by("tree", "product", maintain_order=True)
        .agg(
            other_suffixes=pl.format(
                "{}:{}", pl.col("suffix").replace("", "(none)"), "len"
            )
            .head(6)
            .str.join(" ")
        )
        .collect()
    )
    owners = (
        lf.filter(pl.col("file_type") == "file")
        .group_by("tree", "product", "uid")
        .len()
        .sort("len", descending=True)
        .group_by("tree", "product", maintain_order=True)
        .agg(owners=pl.format("{}:{}", "uid", "len").str.join(" "))
        .collect()
    )
    return (
        main.join(suffixes, on=["tree", "product"], how="left")
        .join(owners, on=["tree", "product"], how="left")
        .sort("tree", "product")
    )


def duplicates(lf: pl.LazyFrame) -> pl.DataFrame:
    """Same file name in more than one product (``same_name``), or the same
    inode at more than one path (``hard_link``)."""
    real = lf.filter(
        pl.col("file_type") == "file", ~pl.col("name").str.starts_with(".")
    )
    cols = ["tree", "product", "rel_path", "name", "size_bytes", "inode", "dev"]
    same_name = (
        real.with_columns(
            n_products=pl.concat_str("tree", "product", separator="/")
            .n_unique()
            .over("name")
        )
        .filter(pl.col("n_products") > 1)
        .select(*cols, kind=pl.lit("same_name"))
    )
    hard = (
        real.filter(pl.col("nlink") > 1)
        .with_columns(n=pl.len().over("dev", "inode"))
        .filter(pl.col("n") > 1)
        .select(*cols, kind=pl.lit("hard_link"))
    )
    return pl.concat([same_name, hard]).collect().sort("kind", "name", "tree")


def owners(lf: pl.LazyFrame) -> pl.DataFrame:
    return (
        lf.filter(pl.col("file_type") == "file")
        .group_by("tree", "product", "uid", "gid")
        .agg(n_files=pl.len(), size_tb=(pl.col("size_bytes").sum() / TB).round(4))
        .sort("tree", "product", "n_files", descending=[False, False, True])
        .collect()
    )


def to_markdown(df: pl.DataFrame) -> str:
    cols = df.columns
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.iter_rows():
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    return "\n".join(lines) + "\n"


def _depths(items: list[str]) -> tuple[dict[str, int], int]:
    per, default = {}, 1
    for it in items:
        if "=" in it:
            k, v = it.split("=", 1)
            per[k] = int(v)
        else:
            default = int(it)
    return per, default


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", action="append", required=True, help="scan dir")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--depth",
        action="append",
        default=[],
        help="product depth: N (all trees) or TREE=N (repeatable)",
    )
    args = ap.parse_args(argv)
    per, default = _depths(args.depth)
    try:
        lf = with_product(load(args.scan), per, default)
    except ValueError as exc:
        ap.error(str(exc))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    inv = inventory(lf)
    inv.write_parquet(out / "inventory.parquet")
    (out / "inventory.md").write_text(to_markdown(inv))
    dup = duplicates(lf)
    dup.write_parquet(out / "duplicates.parquet")
    owners(lf).write_parquet(out / "owners.parquet")
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200):
        print(
            inv.select(
                "tree", "product", "n_files", "n_nc", "n_other", "size_tb", "owners"
            ),
            file=sys.stderr,
        )
    print(
        f"duplicates: {dup.group_by('kind').len().rows()}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
