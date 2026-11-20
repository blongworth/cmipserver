"""Promote one built catalog from catalog/next to catalog/live.

Dry run unless --execute. Refuses unless:
- next holds <name>.csv, <name>_invalid.parquet and <name>.json, and the JSON's
  catalog_file is <name>.csv (relative, so it still works from live);
- the check_catalog.py report (--report) exists, has every check passing, and
  is newer than the files in next (so it checked this build);
- the dataset count did not drop more than --max-drop against the catalog
  already in live (--allow-drop overrides, for a planned removal).

Each file is copied to a hidden temp name in live, then renamed over the old
one: CSV and _invalid.parquet first, JSON last, so a reader that opens the
JSON never finds a missing or half-written CSV. Nothing in next is changed.

Example:
    uv run scripts/check_catalog.py --catalog catalog/next/whoi_era5.json \\
        --previous catalog/live/whoi_era5.json --products manifests/catalogs.yaml \\
        --report runs/check_era5.json
    uv run scripts/promote_catalog.py --name whoi_era5 --report runs/check_era5.json
    uv run scripts/promote_catalog.py --name whoi_era5 --report runs/check_era5.json --execute
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import sys
from pathlib import Path

import polars as pl

CATALOG_ROOT = Path("/proj/cmip6/catalog")
# Copy order: the JSON points at the CSV, so it goes last.
SUFFIXES = (".csv", "_invalid.parquet", ".json")
# A report must include these checks (check_catalog.py run_checks).
REQUIRED_CHECKS = ("columns", "types", "unique_paths", "intake_open", "to_dataset_dict")


class Refused(Exception):
    pass


def files(directory: Path, name: str) -> list[Path]:
    return [directory / f"{name}{s}" for s in SUFFIXES]


def n_datasets(csv: Path) -> int:
    df = pl.read_csv(csv, columns=["master_id", "version"], infer_schema_length=0)
    return df.n_unique() if df.height else 0


def check_next(next_dir: Path, name: str) -> list[Path]:
    src = files(next_dir, name)
    missing = [p.name for p in src if not p.is_file()]
    if missing:
        raise Refused(f"missing in {next_dir}: {', '.join(missing)}")
    desc = json.loads(src[-1].read_text())
    if desc.get("catalog_file") != f"{name}.csv":
        raise Refused(
            f"{src[-1].name}: catalog_file is {desc.get('catalog_file')!r}, "
            f"expected {name}.csv (relative)"
        )
    return src


def check_report(report: Path, src: list[Path]) -> None:
    if not report.is_file():
        raise Refused(f"no check report: {report}")
    results = json.loads(report.read_text())
    if not isinstance(results, list) or not results:
        raise Refused(f"{report}: not a check_catalog.py report")
    failed = [f"{r['check']}: {r.get('detail', '')}" for r in results if not r["ok"]]
    if failed:
        raise Refused(f"{report}: failed checks:\n  " + "\n  ".join(failed))
    names = {r["check"] for r in results}
    absent = [c for c in REQUIRED_CHECKS if c not in names]
    if absent:
        raise Refused(f"{report}: missing checks {absent} (did the check stop early?)")
    newest = max(p.stat().st_mtime for p in src)
    if report.stat().st_mtime < newest:
        raise Refused(f"{report} is older than the catalog in next: re-run the check")


def compare_live(src: list[Path], dst: list[Path], max_drop: float) -> str:
    """Dataset counts next vs live; Refused if the drop exceeds max_drop."""
    if not dst[0].is_file():
        return f"{n_datasets(src[0])} datasets (new catalog; nothing in live)"
    old, new = n_datasets(dst[0]), n_datasets(src[0])
    msg = f"{old} -> {new} datasets"
    if new < (1 - max_drop) * old:
        raise Refused(f"{msg}: drop over {max_drop:.0%} (use --allow-drop if intended)")
    return msg


def copy_in(src: Path, dst: Path) -> None:
    """Copy src to a hidden temp file next to dst, then rename it over dst."""
    tmp = dst.with_name(f".{dst.name}.tmp-{os.getpid()}")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def promote(
    name: str,
    report: Path,
    next_dir: Path,
    live_dir: Path,
    max_drop: float = 0.05,
    execute: bool = False,
) -> list[str]:
    """Return the log lines; raise Refused if any gate fails."""
    src = check_next(next_dir, name)
    check_report(report, src)
    dst = files(live_dir, name)
    log = [f"{name}: {compare_live(src, dst, max_drop)}; check report OK"]
    same = [
        s.is_file() and d.is_file() and filecmp.cmp(s, d, shallow=False)
        for s, d in zip(src, dst, strict=True)
    ]
    if all(same):
        return [*log, f"{name}: live is already identical; nothing to do"]
    if not live_dir.is_dir():
        raise Refused(f"no live directory: {live_dir}")
    for s, d in zip(src, dst, strict=True):
        if execute:
            copy_in(s, d)
        log.append(f"{'copied' if execute else 'would copy'} {s} -> {d}")
    if not execute:
        log.append("dry run: nothing changed (add --execute)")
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", required=True, help="catalog output name, e.g. whoi_era5")
    ap.add_argument(
        "--report", required=True, type=Path, help="check_catalog.py --report JSON"
    )
    ap.add_argument("--next", type=Path, default=CATALOG_ROOT / "next", dest="next_dir")
    ap.add_argument("--live", type=Path, default=CATALOG_ROOT / "live", dest="live_dir")
    ap.add_argument("--max-drop", type=float, default=0.05)
    ap.add_argument(
        "--allow-drop", action="store_true", help="skip the dataset-count gate"
    )
    ap.add_argument("--execute", action="store_true", help="copy (default: dry run)")
    args = ap.parse_args(argv)
    try:
        log = promote(
            args.name,
            args.report,
            args.next_dir,
            args.live_dir,
            1.0 if args.allow_drop else args.max_drop,
            args.execute,
        )
    except Refused as exc:
        print(f"REFUSED {args.name}: {exc}", file=sys.stderr)
        return 1
    print(*log, sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
