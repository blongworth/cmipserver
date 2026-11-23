"""Read variable attributes from one sampled file per catalog key.

For the store vocabulary (``whoi_cmip_reorg_code.vocab``). Reads one catalog
CSV, picks one file per (key, frequency) (``vocab.sample_files``), opens each
read-only and writes <out>/var_attrs_<catalog>.parquet. Only headers are
read; nothing on the store is changed.

Cached by key: if the output exists, rows for samples still in the catalog
(same key, frequency and path) are kept and only new samples are opened.
--rescan opens every sample again.

On HPC, run as a Slurm job (one array task per catalog;
slurm/scan_var_attrs.sbatch), not on the login node.

Example:
    uv run scripts/scan_var_attrs.py --catalog /proj/cmip6/catalog/live/whoi_era5.csv \
        --out runs/vocab
    # on a mac with the store mounted
    uv run scripts/scan_var_attrs.py --catalog ~/mnt/cmip6/catalog/live/whoi_20crv3.csv \
        --replace-prefix /proj/cmip6 ~/mnt/cmip6 --out runs/vocab
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA
from whoi_cmip_reorg_code.vocab import (
    KEY_COLS,
    VAR_ATTRS_SCHEMA,
    sample_files,
    scan_samples,
)

SAMPLE_COLS = [*KEY_COLS, "frequency", "path"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalog", required=True, help="catalog CSV")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument(
        "--replace-prefix",
        nargs=2,
        metavar=("OLD", "NEW"),
        help="open paths starting OLD under NEW instead (a local mount)",
    )
    ap.add_argument("--rescan", action="store_true", help="ignore the cached rows")
    ap.add_argument("--limit", type=int, help="open at most this many new samples")
    args = ap.parse_args(argv)

    name = Path(args.catalog).stem
    df = pl.read_csv(args.catalog, schema_overrides=CATALOG_SCHEMA)
    samples = sample_files(name, df)
    out = Path(args.out) / f"var_attrs_{name}.parquet"

    kept = pl.DataFrame(schema=VAR_ATTRS_SCHEMA)
    if out.exists() and not args.rescan:
        kept = pl.read_parquet(out).join(
            samples.select(SAMPLE_COLS), on=SAMPLE_COLS, how="semi", nulls_equal=True
        )
    todo = samples.join(
        kept.select(SAMPLE_COLS).unique(), on=SAMPLE_COLS, how="anti", nulls_equal=True
    )
    if args.limit is not None:
        todo = todo.head(args.limit)
    n_cached = kept.select(SAMPLE_COLS).n_unique() if kept.height else 0
    print(
        f"{name}: {samples.height} samples, {n_cached} cached, {todo.height} to open",
        file=sys.stderr,
    )
    t0 = time.monotonic()
    new = scan_samples(
        todo, tuple(args.replace_prefix) if args.replace_prefix else None
    )
    res = pl.concat([kept, new]).sort(SAMPLE_COLS, nulls_last=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp")
    res.write_parquet(tmp)
    tmp.replace(out)
    n_err = new.filter(pl.col("nc_status") != "ok").height
    print(
        f"{name}: opened {todo.height} in {time.monotonic() - t0:.0f}s, "
        f"{n_err} not ok; wrote {out} ({res.height} rows)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
