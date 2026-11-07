"""Find missing files from a holdings scan (offline, parquet only).

Writes to --out:
    gap_datasets.parquet       one row per dataset: span, n_files, gap_status
    gap_issues.parquet         one row per gap / overlap / duplicate
    partial_coverage.parquet   uneven experiment/member coverage (report only)
    core_gaps.parquet          with --core: each required combination, present?

Example:
    uv run scripts/find_gaps.py --scan scan/ --out gaps/ --core manifests/core.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.gaps import core_gaps, find_gaps, partial_coverage
from whoi_cmip_reorg_code.manifest import CoreManifest, load


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", required=True, help="dir with holdings_*.parquet")
    ap.add_argument("--out", required=True)
    ap.add_argument("--core", help="core manifest (manifests/core.yaml)")
    args = ap.parse_args(argv)

    files = sorted(Path(args.scan).glob("holdings_*.parquet"))
    if not files:
        ap.error(f"no holdings_*.parquet in {args.scan}")
    lf = pl.scan_parquet(files)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    datasets, issues = find_gaps(lf)
    datasets.write_parquet(out / "gap_datasets.parquet")
    issues.write_parquet(out / "gap_issues.parquet")
    partial_coverage(datasets).write_parquet(out / "partial_coverage.parquet")
    print(
        datasets.group_by("gap_status").len().sort("gap_status"),
        file=sys.stderr,
    )
    if args.core:
        cg = core_gaps(lf, load(args.core, CoreManifest))
        cg.write_parquet(out / "core_gaps.parquet")
        print(
            f"core: {cg['present'].sum()}/{cg.height} combinations present",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
