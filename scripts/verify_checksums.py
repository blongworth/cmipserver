"""sha256 for a candidate set of files (read-only; never modifies files).

Step 1, select (login node; parquet only):
    uv run scripts/verify_checksums.py select --scan scan/ \
        --ncinfo ncinfo/ --esgf esgf/esgf_compare.parquet \
        --sample-per-source 20 --out checksums/candidates.parquet

Step 2, hash (Slurm array; task i of N takes every N-th candidate):
    uv run scripts/verify_checksums.py hash \
        --candidates checksums/candidates.parquet --out checksums/ \
        --n-shards 10 --shard-index 3

Output: checksums_<i>of<N>.parquet with sha256, checksum_ok (vs ESGF when
known), error, seconds.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.checksums import select_candidates, shard, verify


def _scan_dir(d: str | None, pattern: str) -> pl.LazyFrame | None:
    if d is None:
        return None
    p = Path(d)
    files = [p] if p.is_file() else sorted(p.glob(pattern))
    if not files:
        raise SystemExit(f"no {pattern} in {d}")
    return pl.scan_parquet(files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--scan", required=True)
    s.add_argument("--ncinfo", help="dir of ncinfo_*.parquet (or one file)")
    s.add_argument("--esgf", help="esgf_compare.parquet")
    s.add_argument("--sample-per-source", type=int, default=20)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--out", required=True)
    h = sub.add_parser("hash")
    h.add_argument("--candidates", required=True)
    h.add_argument("--out", required=True, help="output directory")
    h.add_argument("--n-shards", type=int, default=1)
    h.add_argument("--shard-index", type=int, help="default: $SLURM_ARRAY_TASK_ID")
    args = ap.parse_args(argv)

    if args.cmd == "select":
        c = select_candidates(
            _scan_dir(args.scan, "holdings_*.parquet"),
            _scan_dir(args.ncinfo, "ncinfo_*.parquet"),
            _scan_dir(args.esgf, "esgf_compare.parquet"),
            args.sample_per_source,
            args.seed,
        )
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        c.write_parquet(args.out)
        tb = (c["size_bytes"].sum() or 0) / 1e12
        print(
            f"{c.height} candidates, {tb:.3f} TB to read -> {args.out}", file=sys.stderr
        )
        return 0

    idx = args.shard_index
    if idx is None:
        idx = int(os.environ.get("SLURM_ARRAY_TASK_ID", "0"))
    cands = shard(pl.read_parquet(args.candidates), idx, args.n_shards)
    t0 = time.monotonic()
    res = verify(cands, Path(args.out) / f"checksums_{idx}of{args.n_shards}.parquet")
    dt = time.monotonic() - t0
    gb = (cands["size_bytes"].sum() or 0) / 1e9
    print(
        f"shard {idx}/{args.n_shards}: {res['n_rows']} files, {gb:.1f} GB, "
        f"{dt:.0f}s ({gb / dt if dt else 0:.2f} GB/s), mismatch={res['n_mismatch']} "
        f"errors={res['n_error']} -> {res['out']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
