"""Compare a holdings scan with a plain file list (one relative path per line).

Built for the old server's `nc-file-list.txt` (a stand-in for the unbuilt Solr
export). Paths in the list and `rel_path` in the scan must be relative to the
same root. Only `.nc` files that are real files are compared.

Prints counts, the groups (activity/institution/model) with the most
differences, and sample paths. With --out, writes `only_in_scan.parquet`
(with `drs_status`, `size_bytes`) and `only_in_list.parquet`.

Example:
    uv run scripts/compare_file_list.py --scan runs/2026-11-12_L4/scan \
        --list /proj/cmip6/tmp/cmip6/nc-file-list.txt --out runs/2026-11-12_L4/compare
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

GROUP_DEPTH = 3  # activity/institution/model


def read_list(path: str | Path) -> pl.DataFrame:
    lines = Path(path).read_text().splitlines()
    return pl.DataFrame(
        {"rel_path": [x.strip().lstrip("./") for x in lines if x.strip()]}
    )


def compare(scan_dir: str | Path, list_path: str | Path) -> dict[str, pl.DataFrame]:
    scan = (
        pl.scan_parquet(sorted(Path(scan_dir).glob("holdings_*.parquet")))
        .filter(pl.col("file_type") == "file", pl.col("suffix") == ".nc")
        .select("rel_path", "size_bytes", "drs_status")
        .collect()
    )
    listed = read_list(list_path).unique()
    both = scan.join(listed, on="rel_path", how="inner")
    only_scan = scan.join(listed, on="rel_path", how="anti").sort("rel_path")
    only_list = listed.join(scan.select("rel_path"), on="rel_path", how="anti").sort(
        "rel_path"
    )
    return {
        "scan": scan,
        "listed": listed,
        "both": both,
        "only_in_scan": only_scan,
        "only_in_list": only_list,
    }


def by_group(df: pl.DataFrame, n: int = 15) -> pl.DataFrame:
    return (
        df.with_columns(
            group=pl.col("rel_path")
            .str.split("/")
            .list.head(GROUP_DEPTH)
            .list.join("/")
        )
        .group_by("group")
        .agg(n=pl.len())
        .sort("n", descending=True)
        .head(n)
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", required=True, help="dir with holdings_*.parquet")
    ap.add_argument("--list", required=True, help="file list, one path per line")
    ap.add_argument("--out", help="write only_in_scan/only_in_list parquet here")
    ap.add_argument("--samples", type=int, default=10)
    args = ap.parse_args(argv)

    r = compare(args.scan, args.list)
    pl.Config.set_tbl_cols(-1)
    pl.Config.set_fmt_str_lengths(140)
    pl.Config.set_tbl_rows(40)
    print(
        f"scan .nc: {r['scan'].height}   list: {r['listed'].height}   "
        f"both: {r['both'].height}   only in scan: {r['only_in_scan'].height}   "
        f"only in list: {r['only_in_list'].height}"
    )
    for name in ("only_in_scan", "only_in_list"):
        df = r[name]
        print(f"\n== {name}: {df.height}")
        if df.is_empty():
            continue
        print(by_group(df))
        print(df.head(args.samples))
    if r["only_in_scan"].height:
        print("\n== only_in_scan by drs_status")
        print(
            r["only_in_scan"]["drs_status"]
            .value_counts()
            .sort("count", descending=True)
        )
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        r["only_in_scan"].write_parquet(out / "only_in_scan.parquet")
        r["only_in_list"].write_parquet(out / "only_in_list.parquet")
        print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
