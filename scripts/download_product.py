"""Download a product fetch manifest into the staging tree.

DRY RUN by default: prints what it would fetch. --execute downloads. Runs on
an HPC login node, or as a Slurm job over 5 GB (slurm/download_product.sbatch).
Files land in <staging-root>/<rel_path> (reanalysis/<product>/...), never in
the live store. Each file is checked:
size against the upstream listing (or checksum, if the manifest has one),
then readable NetCDF with the expected variable (and units, if given) and
time inside the file's year. Failures are kept as .bad. sha256 is logged.
Installing is a reviewed move (plan_moves.py install, then apply_moves.py).

Example:
    uv run scripts/download_product.py --fetch manifests/fetch/2026-11-20-20crv3.yaml \\
        --staging-root <STAGING_ROOT> --log runs/x/download.parquet    # dry run
    uv run scripts/download_product.py ... --execute
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict

import polars as pl

from whoi_cmip_reorg_code.download import (
    DEFAULT_PROTECTED,
    Opener,
    requests_opener,
    run,
)
from whoi_cmip_reorg_code.manifest import FetchManifest, load
from whoi_cmip_reorg_code.products import check_fetched

LOG_SCHEMA = {
    "drs_rel_path": pl.String,
    "dst": pl.String,
    "status": pl.String,
    "size_bytes": pl.Int64,
    "digest": pl.String,
    "error": pl.String,
}
# Downloads bigger than this go through Slurm (slurm/download_product.sbatch).
SLURM_GB = 5.0
BAD = [
    "checksum_mismatch",
    "size_mismatch",
    "content_bad",
    "unverifiable",
    "present_bad",
    "error",
]


def main(argv: list[str] | None = None, opener: Opener | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", required=True, help="fetch manifest (plan_product.py)")
    ap.add_argument("--staging-root", required=True, help="no default: confirm path")
    ap.add_argument("--log", help="write per-file results to this parquet")
    ap.add_argument("--execute", action="store_true", help="actually download")
    ap.add_argument(
        "--protect",
        action="append",
        default=[DEFAULT_PROTECTED],
        help=f"refuse a staging root inside or above this (default {DEFAULT_PROTECTED})",
    )
    args = ap.parse_args(argv)

    m = load(args.fetch, FetchManifest)
    total_gb = sum(i.size_bytes or 0 for i in m.items) / 1e9
    if total_gb > SLURM_GB and "SLURM_JOB_ID" not in os.environ:
        print(
            f"NOTE: {total_gb:.1f} GB > {SLURM_GB:g} GB: run the --execute step as a "
            "Slurm job (slurm/download_product.sbatch), not on a login node.",
            file=sys.stderr,
        )

    def progress(i, n, r):
        if args.execute:
            msg = f"[{i + 1}/{n}] {r.status} {r.drs_rel_path}"
            print(msg + (f" ({r.error})" if r.error else ""), file=sys.stderr)

    try:
        results = run(
            m,
            args.staging_root,
            opener or requests_opener(),
            execute=args.execute,
            protected=args.protect,
            progress=progress,
            check=check_fetched,
        )
    except ValueError as exc:
        ap.error(str(exc))
    df = pl.DataFrame([asdict(r) for r in results], schema=LOG_SCHEMA).rename(
        {"drs_rel_path": "rel_path"}
    )
    if args.log:
        df.write_parquet(args.log)
    summ = df.group_by("status").agg(n=pl.len(), gb=pl.col("size_bytes").sum() / 1e9)
    print(("EXECUTE" if args.execute else "DRY RUN") + f" -> {args.staging_root}")
    print(summ.sort("status"))
    bad = df.filter(pl.col("status").is_in(BAD))
    return 1 if (args.execute and bad.height) else 0


if __name__ == "__main__":
    sys.exit(main())
