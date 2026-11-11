"""Build intake-esm catalogs for CMIP6 and reanalysis holdings (path-only).

Opens no data files. With --manifest each product uses its builder: cmip6
(DRS paths) or reanalysis (paths matched against the product manifest named
by ``spec``).

Writes <out>/<output>.csv, <output>.json (temp file then rename) and
<name>_invalid.parquet (non-DRS or excluded files, for review). Run
check_catalog.py on the result before publishing it.

Examples:
    # from manifests/catalogs.yaml (every enabled product, or --product)
    uv run scripts/build_catalog.py --manifest manifests/catalogs.yaml --out catalog/next

    # ladder: one subtree of the DRS root, excluding files check_nc flagged
    uv run scripts/build_catalog.py --drs-root /proj/cmip6/data/cmip6 --name whoi_cmip6 \
        --root /proj/cmip6/data/cmip6/CMIP/NCAR/CESM2 --ncinfo ncinfo/ --out runs/x/catalog
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog import cmip6, reanalysis
from whoi_cmip_reorg_code.catalog.io import write_catalog
from whoi_cmip_reorg_code.manifest import CatalogsManifest, ProductManifest, load


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", help="catalogs.yaml")
    src.add_argument("--drs-root", help="DRS root (holds activity_id dirs)")
    ap.add_argument("--product", action="append", help="with --manifest: only these")
    ap.add_argument("--name", default="whoi_cmip6", help="with --drs-root")
    ap.add_argument("--root", action="append", help="crawl only these subtrees")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ncinfo", help="dir of ncinfo_*.parquet: exclude nc_status != ok")
    ap.add_argument("--exclude-pattern", action="append", default=[], help="glob")
    ap.add_argument("--jobs", type=int, default=-1, help="joblib workers")
    args = ap.parse_args(argv)

    if args.manifest:
        prods = [
            p
            for p in load(args.manifest, CatalogsManifest).products
            if p.enabled and (not args.product or p.name in args.product)
        ]
        here = Path(args.manifest).parent
        jobs = [
            (
                p.output,  # file stem and catalog id; p.name only selects --product
                p.root,
                p.description or p.name,
                p.builder,
                p.spec and here / p.spec,
            )
            for p in prods
        ]
    else:
        jobs = [(args.name, args.drs_root, "WHOI CMIP6 holdings", "cmip6", None)]
    exclude = None
    if args.ncinfo:
        nci = pl.read_parquet(sorted(Path(args.ncinfo).glob("ncinfo_*.parquet")))
        exclude = set(nci.filter(pl.col("nc_status") != "ok")["path"].to_list())

    rc = 0
    for name, root, desc, builder, spec in jobs:
        t0 = time.monotonic()
        kw = {
            "jobs": args.jobs,
            "exclude_patterns": args.exclude_pattern,
            "exclude_paths": exclude,
        }
        try:
            if builder == "reanalysis":
                if args.root:
                    raise ValueError("--root applies to cmip6 products only")
                if spec is None:  # the manifest validator guarantees it
                    raise ValueError(f"{name}: reanalysis product has no spec")
                df, invalid = reanalysis.build(root, load(spec, ProductManifest), **kw)
            else:
                df, invalid = cmip6.build(args.root or [root], root, **kw)
        except (OSError, ValueError) as exc:
            # one failed product keeps its last good catalog; report and go on
            print(f"{name}: FAILED: {exc}", file=sys.stderr)
            rc = 1
            continue
        _, js = write_catalog(df, args.out, name, desc)
        invalid.write_parquet(Path(args.out) / f"{name}_invalid.parquet")
        n_ds = df.select("master_id", "version").n_unique() if df.height else 0
        print(
            f"{name}: {df.height} files, {n_ds} datasets, {invalid.height} invalid, "
            f"{time.monotonic() - t0:.0f}s -> {js}",
            file=sys.stderr,
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
