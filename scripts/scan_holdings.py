"""Scan a holdings tree (read-only) and write per-shard inventory parquet.

Examples:
    # list shards (one per top-level directory)
    uv run scripts/scan_holdings.py --root /proj/cmip6 --list-shards

    # scan one shard by name or index (index defaults to $SLURM_ARRAY_TASK_ID)
    uv run scripts/scan_holdings.py --root /proj/cmip6 --out scan/ --shard CMIP
    uv run scripts/scan_holdings.py --root /proj/cmip6 --out scan/ --shard-index 3

    # small test: a nested directory (paths stay relative to --root)
    uv run scripts/scan_holdings.py --root /proj/cmip6 --out scan/ \
        --shard CMIP/NCAR/CESM2/historical

    # no shard and no $SLURM_ARRAY_TASK_ID: scan the whole tree as shard "all"
    uv run scripts/scan_holdings.py --root tests/fixture --out scan/
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from whoi_cmip_reorg_code.scan import list_shards, peak_rss_mb, scan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="tree to scan")
    ap.add_argument("--out", help="output directory for parquet files")
    ap.add_argument("--list-shards", action="store_true", help="print shards and exit")
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        "--shard",
        help="top-level directory name, _root_files, or a nested dir like CMIP/NCAR",
    )
    g.add_argument(
        "--shard-index",
        type=int,
        help="index into --list-shards (default: $SLURM_ARRAY_TASK_ID)",
    )
    ap.add_argument(
        "--split",
        action="append",
        default=[],
        metavar="DIR",
        help="replace this top-level dir by its subdirs in the shard list "
        "(repeatable, e.g. --split CMIP); use the same value for every task",
    )
    ap.add_argument("--batch-rows", type=int, default=100_000)
    args = ap.parse_args(argv)

    shards = list_shards(args.root, args.split)
    if args.list_shards:
        for s in shards:
            print(s)
        return 0
    if not args.out:
        ap.error("--out is required unless --list-shards")

    shard = args.shard
    idx = args.shard_index
    if shard is None and idx is None and "SLURM_ARRAY_TASK_ID" in os.environ:
        idx = int(os.environ["SLURM_ARRAY_TASK_ID"])
    if idx is not None:
        if not 0 <= idx < len(shards):
            ap.error(f"shard index {idx} out of range (0-{len(shards) - 1})")
        shard = shards[idx]
    elif shard is not None and "/" not in shard.strip("/") and shard not in shards:
        ap.error(f"unknown shard {shard!r}; see --list-shards")

    t0 = time.monotonic()
    res = scan(args.root, args.out, shard=shard, batch_rows=args.batch_rows)
    dt = time.monotonic() - t0
    print(
        f"shard={shard or 'all'} rows={res['n_rows']} errors={res['n_errors']} "
        f"elapsed={dt:.1f}s peak_rss={peak_rss_mb():.0f}MB -> {res['holdings']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
