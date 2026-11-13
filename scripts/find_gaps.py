"""Find missing files from a holdings scan (offline, parquet only).

Writes to --out:
    gap_datasets.parquet       one row per dataset: span, n_files, gap_status
    gap_issues.parquet         one row per gap / overlap / duplicate
    partial_coverage.parquet   uneven experiment/member coverage (report only)
    core_gaps.parquet          with --core: each required combination, present?

With --ncinfo instead of --scan (non-CMIP products, no CMIP filenames), gaps
come from the file headers (``gaps.header_gaps``). A dataset is a directory
(year dirs folded to ``*``) plus the file's data variables:
    header_gap_datasets.parquet
    header_gap_issues.parquet

Examples:
    uv run scripts/find_gaps.py --scan scan/ --out gaps/ --core manifests/core.yaml
    uv run scripts/find_gaps.py --ncinfo ncinfo/ --out gaps/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.gaps import (
    core_gaps,
    dataset_dir_expr,
    find_gaps,
    header_gaps,
    partial_coverage,
)
from whoi_cmip_reorg_code.manifest import CoreManifest, load


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--scan", help="dir with holdings_*.parquet")
    src.add_argument("--ncinfo", help="dir with ncinfo_*.parquet (header gaps)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--core", help="core manifest (manifests/core.yaml)")
    args = ap.parse_args(argv)

    if args.ncinfo:
        if args.core:
            ap.error("--core needs --scan")
        return _header(ap, Path(args.ncinfo), Path(args.out))

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


def _header(ap: argparse.ArgumentParser, ncinfo: Path, out: Path) -> int:
    files = sorted(ncinfo.glob("ncinfo_*.parquet"))
    if not files:
        ap.error(f"no ncinfo_*.parquet in {ncinfo}")
    lf = pl.scan_parquet(files).with_columns(
        dir=dataset_dir_expr(), vars=pl.col("data_vars").list.join(",")
    )
    datasets, issues = header_gaps(lf, ["dir", "vars"])
    out.mkdir(parents=True, exist_ok=True)
    datasets.write_parquet(out / "header_gap_datasets.parquet")
    issues.write_parquet(out / "header_gap_issues.parquet")
    print(datasets.group_by("gap_status").len().sort("gap_status"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
