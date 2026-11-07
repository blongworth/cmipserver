"""Check NetCDF files from a holdings scan (read-only) and write ncinfo parquet.

One output per holdings shard: ``ncinfo_<tag>.parquet`` with ``nc_status``,
``nc_error``, global attributes, main variable, time axis, and
``attr_mismatch`` (attributes vs filename facets). A bad file never stops the
run; its problem is recorded in its row.

Examples:
    # L1: one dataset directory from one shard
    uv run scripts/check_nc.py --scan scan/ --out ncinfo/ --shard CMIP \
        --prefix CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas

    # array job: index into the sorted holdings_*.parquet tags
    uv run scripts/check_nc.py --scan scan/ --out ncinfo/ --shard-index 3 --deep

    uv run scripts/check_nc.py --scan scan/ --list-shards
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.ncinfo import check_files, nc_candidates
from whoi_cmip_reorg_code.scan import holdings_tags, pick_tag


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", required=True, help="dir with holdings_*.parquet")
    ap.add_argument("--out", help="output directory")
    ap.add_argument("--list-shards", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--shard", help="holdings shard tag or dir (e.g. CMIP)")
    g.add_argument(
        "--shard-index", type=int, help="index into --list-shards (default: array id)"
    )
    ap.add_argument("--prefix", help="only rel_paths under this directory")
    ap.add_argument("--limit", type=int, help="check at most N files (pilots)")
    ap.add_argument(
        "--deep", action="store_true", help="also read the main variable's last step"
    )
    args = ap.parse_args(argv)

    if args.list_shards:
        print("\n".join(holdings_tags(args.scan)))
        return 0
    if not args.out:
        ap.error("--out is required unless --list-shards")
    try:
        tag = pick_tag(args.scan, args.shard, args.shard_index)
    except ValueError as exc:
        ap.error(str(exc))

    lf = pl.scan_parquet(Path(args.scan) / f"holdings_{tag}.parquet")
    files = nc_candidates(lf, args.prefix).collect()
    if args.limit:
        files = files.head(args.limit)
    suffix = (
        f"{tag}__{args.prefix.strip('/').replace('/', '__')}" if args.prefix else tag
    )
    t0 = time.monotonic()
    res = check_files(
        files, Path(args.out) / f"ncinfo_{suffix}.parquet", deep=args.deep
    )
    dt = time.monotonic() - t0
    rate = res["n_rows"] / dt if dt else 0.0
    print(
        f"shard={tag} files={res['n_rows']} elapsed={dt:.1f}s ({rate:.1f}/s) "
        f"status={res['counts']} -> {res['out']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
