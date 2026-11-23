"""Build the store vocabulary (whoi_cmip_vocabulary.csv) from the catalogs.

Reads the catalog CSVs, the official vocabulary snapshots (vocab/sources/)
and the per-catalog variable attributes from scan_var_attrs.py, and writes
<out>/whoi_cmip_vocabulary.csv (temp file, then rename) and
<out>/whoi_cmip_vocabulary_report.json. Opens no data files.

The report lists, for review: keys never scanned, scan errors, variables
with no long_name or standard_name, official definitions the sampled file
differs from (nc_check), ERA5 variables not found in the ECMWF database,
standard names not in the CF table, and attributes that differ between a
key's sampled files. Nothing is fixed.

Build from the same catalogs as the site (the live ones), so the vocabulary
covers exactly them. Exit 1 if any key has no vocabulary row.

Example:
    L=/proj/cmip6/catalog/live
    uv run scripts/build_vocab.py --catalog $L/whoi_cmip6.csv --catalog $L/whoi_era5.csv \
        --var-attrs runs/vocab --out catalog/next
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code import vocab
from whoi_cmip_reorg_code.catalog_schema import (
    CATALOG_SCHEMA,
    DEFAULT_DATA_ROOT,
    VOCAB_NAME,
)
from whoi_cmip_reorg_code.vocab_sources import read_sources


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalog", action="append", required=True, help="catalog CSV")
    ap.add_argument(
        "--var-attrs",
        help="dir of var_attrs_<catalog>.parquet (scan_var_attrs.py); without "
        "it only official definitions apply",
    )
    ap.add_argument("--sources", default="vocab/sources")
    ap.add_argument("--root", default=DEFAULT_DATA_ROOT, help="data root prefix")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    keys, attrs = [], []
    for path in args.catalog:
        name = Path(path).stem
        df = pl.read_csv(path, schema_overrides=CATALOG_SCHEMA)
        keys.append(vocab.catalog_keys(name, df))
        p = Path(args.var_attrs or "") / f"var_attrs_{name}.parquet"
        if args.var_attrs and p.exists():
            attrs.append(pl.read_parquet(p))
        elif args.var_attrs:
            print(f"{name}: no {p}", file=sys.stderr)
    all_keys = pl.concat(keys)
    var_attrs = (
        pl.concat(attrs) if attrs else pl.DataFrame(schema=vocab.VAR_ATTRS_SCHEMA)
    )
    # only samples of the catalogs given (a stale parquet may hold others)
    var_attrs = var_attrs.join(
        all_keys, on=vocab.KEY_COLS, how="semi", nulls_equal=True
    )
    sources, meta = read_sources(args.sources)
    voc, report = vocab.build_vocabulary(all_keys, var_attrs, sources, meta, args.root)
    report["sources"] = meta
    report["catalogs"] = [Path(p).stem for p in args.catalog]

    out = Path(args.out)
    csv = vocab.write_csv(voc, out / f"{VOCAB_NAME}.csv")
    rep = out / f"{VOCAB_NAME}_report.json"
    rep.write_text(json.dumps(report, indent=2, default=str) + "\n")
    missing = vocab.missing_keys(all_keys, voc)
    print(json.dumps(report["counts"]), file=sys.stderr)
    print(f"wrote {csv} and {rep}", file=sys.stderr)
    if missing.height:
        print(f"FAIL: {missing.height} keys have no vocabulary row", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
