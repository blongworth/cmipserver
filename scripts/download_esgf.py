"""Download redownload actions from a repairs manifest into the staging tree.

DRY RUN by default: prints what it would fetch. --execute downloads. Runs on
the DTN (or any node with outbound HTTPS) as the service account. Files land
in <staging-root>/<drs_rel_path>, are size- and checksum-verified, and are
never written into the live store; installing them is a reviewed move
(plan_moves.py --repairs, then apply_moves.py).

Example:
    uv run scripts/download_esgf.py --repairs manifests/repairs/2026-11-20.yaml \
        --staging-root <STAGING_ROOT> --log runs/dl.parquet            # dry run
    uv run scripts/download_esgf.py ... --execute
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict

import polars as pl

from whoi_cmip_reorg_code.catalog_schema import DEFAULT_DATA_ROOT
from whoi_cmip_reorg_code.download import Opener, requests_opener, run
from whoi_cmip_reorg_code.manifest import RepairManifest, load


def main(argv: list[str] | None = None, opener: Opener | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repairs", required=True, help="repairs YAML")
    ap.add_argument("--staging-root", required=True, help="no default: confirm path")
    ap.add_argument("--log", help="write per-file results to this parquet")
    ap.add_argument("--execute", action="store_true", help="actually download")
    ap.add_argument(
        "--protect",
        action="append",
        default=[DEFAULT_DATA_ROOT],
        help=f"refuse a staging root inside this (default {DEFAULT_DATA_ROOT})",
    )
    args = ap.parse_args(argv)

    m = load(args.repairs, RepairManifest)

    def progress(i, n, r):
        if args.execute:
            print(f"[{i + 1}/{n}] {r.status} {r.drs_rel_path}", file=sys.stderr)

    try:
        results = run(
            m,
            args.staging_root,
            opener or requests_opener(),
            execute=args.execute,
            protected=args.protect,
            progress=progress,
        )
    except ValueError as exc:
        ap.error(str(exc))
    df = pl.DataFrame(
        [asdict(r) for r in results],
        schema={
            "drs_rel_path": pl.String,
            "dst": pl.String,
            "status": pl.String,
            "size_bytes": pl.Int64,
            "digest": pl.String,
            "error": pl.String,
        },
    )
    if args.log:
        df.write_parquet(args.log)
    summ = df.group_by("status").agg(n=pl.len(), gb=pl.col("size_bytes").sum() / 1e9)
    print(("EXECUTE" if args.execute else "DRY RUN") + f" -> {args.staging_root}")
    print(summ.sort("status"))
    bad = df.filter(
        pl.col("status").is_in(
            ["checksum_mismatch", "size_mismatch", "present_bad", "error"]
        )
    )
    return 1 if (args.execute and bad.height) else 0


if __name__ == "__main__":
    sys.exit(main())
