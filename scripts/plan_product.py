"""Plan adding a reanalysis product: what is upstream, what the store has.

Reads a product manifest, lists the remote (one autoindex page per directory,
then a HEAD per file for exact size and Last-Modified), and stats the expected
paths under <store-root>/reanalysis/<product>/. Touches no data files.

Writes:
    <out>.parquet   one row per expected file (and extra / duplicate rows)
    <out>.md        short report for review
    --fetch-out     fetch manifest of the MISSING files only (not with --offline)

Prints a product verdict: new / partial / complete / possible_duplicate.
Files already present are never planned. present_differs is report-only.
--scan: a holdings scan (dir of holdings_*.parquet) to search for same-named
files elsewhere in the store. Use an existing scan or a scan of reanalysis/
only; never start a full-store scan for this.

Examples:
    # DTN: read-only listing + store check
    uv run scripts/plan_product.py --product manifests/products/20crv3.yaml \\
        --store-root /proj/cmip6/data --out runs/2026-11-20_20crv3/plan \\
        --fetch-out manifests/fetch/2026-11-20-20crv3.yaml

    # ladder L1: one group, one variable, two years
    uv run scripts/plan_product.py --product manifests/products/20crv3.yaml \
        --group 2mMO --variable air --years 2014 2015 \
        --store-root /proj/cmip6/data --out runs/x/plan --fetch-out manifests/fetch/x.yaml

    # no network: store check only
    uv run scripts/plan_product.py --product manifests/products/20crv3.yaml \\
        --store-root /proj/cmip6/data --out runs/x/plan --offline
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code import products
from whoi_cmip_reorg_code.manifest import FetchManifest, ProductManifest, dump, load


def report_md(m: ProductManifest, status: pl.DataFrame, v: str, args) -> str:
    counts = status.group_by("status").agg(
        n=pl.len(), gb=(pl.col("size_bytes").sum() / 1e9).round(2)
    )
    lines = [
        f"# Plan: {m.product} ({m.title})",
        "",
        f"- created: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        f"- manifest: `{args.product}`",
        f"- store: `{args.store_root}/reanalysis/{m.product}/`",
        f"- remote: {'not checked (--offline)' if args.offline else m.source.base_url}",
        f"- **verdict: {v}**",
        "",
        "| status | files | GB (upstream) |",
        "|---|---:|---:|",
        *(
            f"| {r['status']} | {r['n']} | {r['gb'] or ''} |"
            for r in counts.sort("status").iter_rows(named=True)
        ),
        "",
    ]
    for st in ("possible_duplicate", "present_differs", "not_upstream", "extra_local"):
        sub = status.filter(status=st)
        if not sub.height:
            continue
        lines += [f"## {st} ({sub.height})", ""]
        for r in sub.head(50).iter_rows(named=True):
            where = r["local_path"] or r["url"]
            lines.append(f"- `{where}`" + (f": {r['detail']}" if r["detail"] else ""))
        if sub.height > 50:
            lines.append(f"- ... {sub.height - 50} more in the parquet")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None, remote: products.Remote | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--product", required=True, help="manifests/products/<p>.yaml")
    ap.add_argument("--store-root", required=True, help="e.g. /proj/cmip6")
    ap.add_argument("--out", required=True, help="report prefix (.parquet, .md)")
    ap.add_argument("--fetch-out", help="fetch manifest YAML (missing files)")
    ap.add_argument("--scan", help="dir of holdings_*.parquet: look for duplicates")
    ap.add_argument("--offline", action="store_true", help="skip the remote listing")
    ap.add_argument("--workers", type=int, default=8, help="parallel HEAD requests")
    ap.add_argument("--group", action="append", help="only these remote_dirs")
    ap.add_argument("--variable", action="append", help="only these variables")
    ap.add_argument("--years", type=int, nargs=2, metavar=("Y0", "Y1"))
    args = ap.parse_args(argv)
    if args.offline and args.fetch_out:
        ap.error("--fetch-out needs the remote listing (drop --offline)")

    m = load(args.product, ProductManifest)
    exp = products.expected_files(m)
    all_paths = exp["rel_path"].implode()
    if args.group:
        dirs = pl.col("remote_path").str.replace(r"/[^/]+$", "")
        exp = exp.filter(dirs.is_in(args.group))
    if args.variable:
        exp = exp.filter(pl.col("variable_id").is_in(args.variable))
    if args.years:
        exp = exp.filter(
            pl.col("year").is_null() | pl.col("year").is_between(*args.years)
        )
    if not exp.height:
        ap.error("no expected files left after --group/--variable/--years")
    print(f"{m.product}: {exp.height} expected files", file=sys.stderr)
    if not args.offline:
        exp = products.remote_status(
            exp,
            m.source.base_url,
            remote or products.HttpRemote(),
            workers=args.workers,
            progress=lambda s: print(s, file=sys.stderr),
        )
    scan = None
    if args.scan:
        scan = pl.read_parquet(sorted(Path(args.scan).glob("holdings_*.parquet")))
    status = products.store_status(exp, args.store_root, m.product, scan)
    # with filters, files of other manifest entries are not "extra"
    status = status.filter(
        ~((pl.col("status") == "extra_local") & pl.col("rel_path").is_in(all_paths))
    )
    v = products.verdict(status)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    status.write_parquet(out.with_suffix(".parquet"))
    out.with_suffix(".md").write_text(report_md(m, status, v, args))
    print(status.group_by("status").len().sort("status"), file=sys.stderr)

    if args.fetch_out:
        items = products.fetch_items(status)
        fm = FetchManifest(
            created=datetime.now(UTC),
            created_by=f"plan_product.py ({getpass.getuser()})",
            product=m.product,
            spec=str(args.product),
            items=items,
        )
        dump(
            fm,
            args.fetch_out,
            header=(
                "Generated by scripts/plan_product.py: files missing from the store.\n"
                "Download with scripts/download_product.py (dry run by default)."
            ),
        )
        gb = sum(i.size_bytes or 0 for i in items) / 1e9
        print(f"{len(items)} files ({gb:.1f} GB) -> {args.fetch_out}", file=sys.stderr)
    print(f"verdict: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
