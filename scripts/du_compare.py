"""Compare on-disk totals (scripts/du_shard.sh output) with catalog sizes.

Reads the per-shard TSVs from du_shard.sh (shard, files, apparent_bytes,
allocated_bytes, nonnc_files, nonnc_bytes, errors) and the catalog CSVs
(``path``, ``size_bytes``). Catalogs index ``.nc`` files only, so the disk
side is compared as ``nc_files`` / ``nc_bytes`` (all files minus non-.nc).

Two tables:
    by shard     disk totals per shard, with the catalog rows under it
    by product   every product in --products (enabled, disabled and listed):
                 disk totals summed over the shards under its root, and the
                 catalog rows under that root. Disk is null when a shard is
                 coarser than the root (the shard also holds other data);
                 split that shard to compare.

Sizes are printed in GB (1e9) and GiB (2**30). Only reads its inputs; with
--out writes du_by_shard.parquet and du_by_product.parquet (and .csv with
--csv).

Example:
    L=/proj/cmip6/catalog/live
    uv run scripts/du_compare.py --du runs/du/123456 \\
        --catalog $L/whoi_cmip6.csv --catalog $L/whoi_era5.csv \\
        --products manifests/catalogs.yaml --out runs/du/123456/compare
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog_schema import DEFAULT_DATA_ROOT
from whoi_cmip_reorg_code.manifest import CatalogsManifest, load

GB = 1e9
GIB = 2**30
DU_COLUMNS = {
    "shard": pl.String,
    "files": pl.Int64,
    "apparent_bytes": pl.Int64,
    "allocated_bytes": pl.Int64,
    "nonnc_files": pl.Int64,
    "nonnc_bytes": pl.Int64,
    "errors": pl.Int64,
}


def read_du(paths: list[str | Path]) -> pl.DataFrame:
    """Shard totals from du_shard.sh TSVs (files or dirs of ``*.tsv``)."""
    files: list[Path] = []
    for p in map(Path, paths):
        files.extend(sorted(p.glob("*.tsv")) if p.is_dir() else [p])
    if not files:
        raise SystemExit("no du TSV files found")
    du = pl.concat(
        [
            pl.read_csv(f, separator="\t", has_header=False, schema=DU_COLUMNS)
            for f in files
        ]
    ).with_columns(
        pl.col("shard").str.strip_chars("/"),
        nc_files=pl.col("files") - pl.col("nonnc_files"),
        nc_bytes=pl.col("apparent_bytes") - pl.col("nonnc_bytes"),
    )
    dup = du.filter(pl.col("shard").is_duplicated())["shard"].unique().to_list()
    if dup:
        raise SystemExit(f"shards appear more than once: {sorted(dup)}")
    shards = du["shard"].to_list()
    nested = [(a, b) for a in shards for b in shards if b.startswith(a + "/")]
    if nested:
        raise SystemExit(f"overlapping shards would double count: {nested[:5]}")
    return du


def read_catalogs(paths: list[str | Path], root: str) -> pl.DataFrame:
    """``rel_path`` (relative to root) and ``size_bytes`` of catalogued files."""
    if not paths:
        return pl.DataFrame(schema={"rel_path": pl.String, "size_bytes": pl.Int64})
    prefix = root.rstrip("/") + "/"
    cat = pl.concat(
        [
            pl.scan_csv(p, schema_overrides={"path": pl.String, "size_bytes": pl.Int64})
            .select("path", "size_bytes")
            .collect()
            for p in paths
        ]
    )
    outside = cat.filter(~pl.col("path").str.starts_with(prefix)).height
    if outside:
        print(f"warning: {outside} catalog rows not under {prefix}", file=sys.stderr)
    return cat.filter(pl.col("path").str.starts_with(prefix)).select(
        rel_path=pl.col("path").str.strip_prefix(prefix), size_bytes="size_bytes"
    )


def _under(rel: pl.Expr, dir_: str) -> pl.Expr:
    return rel.str.starts_with(dir_.rstrip("/") + "/")


def by_shard(du: pl.DataFrame, cat: pl.DataFrame) -> pl.DataFrame:
    """Disk totals per shard with the catalog rows under each shard."""
    # Shards may sit at different depths; they do not overlap, so each
    # catalog row matches at most one shard prefix.
    parts = pl.col("rel_path").str.split("/")
    matched = []
    for depth in (du["shard"].str.count_matches("/") + 1).unique().to_list():
        matched.append(
            cat.with_columns(shard=parts.list.head(depth).list.join("/")).join(
                du.select("shard"), on="shard", how="semi"
            )
        )
    cat_by = (
        pl.concat(matched)
        .group_by("shard")
        .agg(cat_files=pl.len(), cat_bytes=pl.col("size_bytes").sum())
        if matched
        else pl.DataFrame(
            schema={"shard": pl.String, "cat_files": pl.UInt32, "cat_bytes": pl.Int64}
        )
    )
    unmatched = cat.height - cat_by["cat_files"].sum()
    if unmatched:
        print(f"warning: {unmatched} catalog rows not under any shard", file=sys.stderr)
    return (
        du.join(cat_by, on="shard", how="left")
        .with_columns(
            pl.col("cat_files").fill_null(0).cast(pl.Int64),
            pl.col("cat_bytes").fill_null(0),
        )
        .with_columns(
            diff_files=pl.col("nc_files") - pl.col("cat_files"),
            diff_bytes=pl.col("nc_bytes") - pl.col("cat_bytes"),
        )
        .sort("shard")
    )


def product_dirs(manifest: CatalogsManifest, root: str) -> pl.DataFrame:
    """name, kind (enabled/disabled/listed) and dir relative to root."""
    prefix = root.rstrip("/") + "/"
    rows = []
    for p in manifest.products:
        r = p.root.rstrip("/")
        if not (r + "/").startswith(prefix):
            print(f"warning: {p.name} root {r} not under {root}", file=sys.stderr)
            continue
        rows.append(
            (p.name, "enabled" if p.enabled else "disabled", r.removeprefix(prefix))
        )
    rows += [(x.name, "listed", x.path.strip("/")) for x in manifest.listed]
    return pl.DataFrame(rows, schema=["product", "kind", "dir"], orient="row")


def by_product(
    shards: pl.DataFrame, cat: pl.DataFrame, products: pl.DataFrame
) -> pl.DataFrame:
    """Disk and catalog totals under each product dir."""
    sums = ["files", "nc_files", "apparent_bytes", "allocated_bytes", "nc_bytes"]
    rows = []
    for name, kind, dir_ in products.iter_rows():
        inside = shards.filter(
            (pl.col("shard") == dir_) | _under(pl.col("shard"), dir_)
        )
        coarser = shards.filter(
            pl.lit(dir_ + "/").str.starts_with(pl.col("shard") + "/")
            & (pl.col("shard") != dir_)
        )
        c = cat.filter(_under(pl.col("rel_path"), dir_))
        row = {"product": name, "kind": kind, "dir": dir_}
        ok = coarser.is_empty() and not inside.is_empty()
        row["shards"] = inside.height
        row["note"] = (
            f"shard {coarser['shard'][0]} is coarser than the root"
            if not coarser.is_empty()
            else ("no shard" if inside.is_empty() else None)
        )
        for col in sums:
            row[col] = int(inside[col].sum()) if ok else None
        row["errors"] = int(inside["errors"].sum()) if ok else None
        row["cat_files"] = c.height
        row["cat_bytes"] = int(c["size_bytes"].sum())
        rows.append(row)
    return pl.DataFrame(
        rows,
        schema={
            "product": pl.String,
            "kind": pl.String,
            "dir": pl.String,
            "shards": pl.Int64,
            "note": pl.String,
            **{c: pl.Int64 for c in sums},
            "errors": pl.Int64,
            "cat_files": pl.Int64,
            "cat_bytes": pl.Int64,
        },
    ).with_columns(
        diff_files=pl.col("nc_files") - pl.col("cat_files"),
        diff_bytes=pl.col("nc_bytes") - pl.col("cat_bytes"),
    )


def _display(df: pl.DataFrame, key: list[str]) -> pl.DataFrame:
    """Byte columns as GB and GiB for printing."""
    out = df.select(
        *key,
        "nc_files",
        "cat_files",
        "diff_files",
        nc_GB=pl.col("nc_bytes") / GB,
        cat_GB=pl.col("cat_bytes") / GB,
        diff_GB=pl.col("diff_bytes") / GB,
        nc_GiB=pl.col("nc_bytes") / GIB,
        cat_GiB=pl.col("cat_bytes") / GIB,
        alloc_GiB=pl.col("allocated_bytes") / GIB,
        nonnc_files=pl.col("files") - pl.col("nc_files"),
        errors="errors",
    )
    return out.with_columns(pl.selectors.float().round(2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--du", action="append", required=True, help="du_shard.sh TSV or dir of them"
    )
    ap.add_argument("--catalog", action="append", default=[], help="catalog CSV")
    ap.add_argument("--products", help="catalogs manifest (manifests/catalogs.yaml)")
    ap.add_argument("--root", default=DEFAULT_DATA_ROOT, help="data root")
    ap.add_argument("--out", help="write du_by_shard/du_by_product parquet here")
    ap.add_argument("--csv", action="store_true", help="also write CSV with --out")
    args = ap.parse_args(argv)

    du = read_du(args.du)
    cat = read_catalogs(args.catalog, args.root)
    shards = by_shard(du, cat)

    pl.Config.set_tbl_cols(-1)
    pl.Config.set_tbl_rows(-1)
    pl.Config.set_fmt_str_lengths(60)
    pl.Config.set_tbl_hide_dataframe_shape(True)
    pl.Config.set_tbl_hide_column_data_types(True)
    pl.Config.set_tbl_width_chars(250)
    tot = du.select(pl.exclude("shard").sum()).row(0, named=True)
    print(
        f"{du.height} shards: {tot['files']} files ({tot['nc_files']} .nc), "
        f"{tot['apparent_bytes'] / GB:.1f} GB / {tot['apparent_bytes'] / GIB:.1f} GiB "
        f"apparent, {tot['allocated_bytes'] / GIB:.1f} GiB allocated, "
        f"{tot['errors']} find errors"
    )
    print(
        f"catalogs: {cat.height} files, {cat['size_bytes'].sum() / GB:.1f} GB / "
        f"{cat['size_bytes'].sum() / GIB:.1f} GiB"
    )
    print("\n== by shard")
    print(_display(shards, ["shard"]))

    products = None
    if args.products:
        manifest = load(args.products, CatalogsManifest)
        products = by_product(shards, cat, product_dirs(manifest, args.root))
        print("\n== by product")
        print(_display(products, ["product", "kind"]))
        notes = products.filter(pl.col("note").is_not_null())
        if not notes.is_empty():
            print(notes.select("product", "dir", "note"))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        tables = {"du_by_shard": shards}
        if products is not None:
            tables["du_by_product"] = products
        for name, df in tables.items():
            df.write_parquet(out / f"{name}.parquet")
            if args.csv:
                df.write_csv(out / f"{name}.csv")
        print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
