"""Compare local holdings with ESGF file records (run on the DTN).

Queries the ESGF search API for every local dataset (all versions), caches
every response in --out/esgf_cache.parquet, and writes to --out:

    esgf_datasets.parquet      one row per ESGF dataset version (latest, retracted)
    esgf_files.parquet         one row per ESGF file (size, checksum, HTTP url)
    esgf_query_errors.parquet  datasets whose query failed (re-run to retry)
    esgf_compare.parquet       one row per local file with esgf_status
    esgf_missing_local.parquet ESGF files absent from a version held locally

--index-url has no default on purpose: confirm the search endpoint (the ESGF
1.5 bridge or an index node) before running. Re-runs reuse the cache, so only
new datasets hit the network; --refresh re-queries everything.

Examples:
    # pilot: one source_id
    uv run scripts/check_esgf.py --scan scan/ --out esgf/ \
        --index-url https://<index-node>/esg-search/search --source-id CESM2

    # recompute the comparison from cached results only (no network)
    uv run scripts/check_esgf.py --scan scan/ --out esgf/ --compare-only
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.esgf import (
    CachedFetcher,
    Fetcher,
    RequestsFetcher,
    compare,
    local_datasets,
    query_datasets,
)


def main(argv: list[str] | None = None, fetcher: Fetcher | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", required=True, help="dir with holdings_*.parquet")
    ap.add_argument("--out", required=True)
    ap.add_argument("--index-url", help="ESGF search endpoint (esg-search/search)")
    ap.add_argument("--source-id", action="append", help="only these (repeatable)")
    ap.add_argument("--limit-datasets", type=int, help="query at most N datasets")
    ap.add_argument(
        "--min-interval", type=float, default=1.0, help="seconds between requests"
    )
    ap.add_argument("--refresh", action="store_true", help="ignore cached responses")
    ap.add_argument(
        "--compare-only", action="store_true", help="skip queries; use saved parquet"
    )
    args = ap.parse_args(argv)

    files = sorted(Path(args.scan).glob("holdings_*.parquet"))
    if not files:
        ap.error(f"no holdings_*.parquet in {args.scan}")
    holdings = pl.scan_parquet(files)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    todo = local_datasets(holdings)
    if args.source_id:
        todo = todo.filter(pl.col("source_id").is_in(args.source_id))
    if args.limit_datasets:
        todo = todo.head(args.limit_datasets)

    if not args.compare_only:
        if not args.index_url:
            ap.error("--index-url is required unless --compare-only")
        t0 = time.monotonic()
        with CachedFetcher(
            fetcher or RequestsFetcher(),
            out / "esgf_cache.parquet",
            min_interval=args.min_interval,
            refresh=args.refresh,
        ) as cf:

            def progress(i: int, facets: dict) -> None:
                if i % 100 == 0:
                    print(f"[{i}/{todo.height}] live={cf.n_live}", file=sys.stderr)

            ds, fs, errs = query_datasets(
                cf, args.index_url, todo.iter_rows(named=True), progress
            )
        ds.write_parquet(out / "esgf_datasets.parquet")
        fs.write_parquet(out / "esgf_files.parquet")
        errs.write_parquet(out / "esgf_query_errors.parquet")
        todo.write_parquet(out / "esgf_queried.parquet")
        print(
            f"queried {todo.height} datasets ({cf.n_live} live requests, "
            f"{time.monotonic() - t0:.0f}s): {ds.height} ESGF versions, "
            f"{fs.height} files, {errs.height} errors",
            file=sys.stderr,
        )

    ds = pl.read_parquet(out / "esgf_datasets.parquet")
    fs = pl.read_parquet(out / "esgf_files.parquet")
    queried = pl.read_parquet(out / "esgf_queried.parquet")
    errs = pl.read_parquet(out / "esgf_query_errors.parquet")
    # failed queries count as not queried, not as "not on ESGF"
    queried = queried.join(errs, on=list(queried.columns), how="anti")
    local, missing = compare(holdings, ds, fs, queried)
    local.write_parquet(out / "esgf_compare.parquet")
    missing.write_parquet(out / "esgf_missing_local.parquet")
    print(local.group_by("esgf_status").len().sort("esgf_status"), file=sys.stderr)
    print(f"missing locally: {missing.height} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
