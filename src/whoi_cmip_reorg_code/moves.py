"""Plan move manifests (no file changes).

``plan_reorg``: non-DRS files -> ``<drs_root>/<expected_rel_path>``.
Placeholders for activity/institution are filled from the file's global
attributes (ncinfo) when they hold a single value; otherwise the file is
reported as unmappable. Corrupt files are left for the repair path.

``plan_repair_moves``: for each staged redownload, the bad original (if any)
-> ``<quarantine_root>/<path relative to store_root>``, then the staged file
-> ``<drs_root>/<drs_rel_path>``.

``plan_install_moves``: each staged file of a product fetch manifest
``<staging_root>/<rel_path>`` -> ``<store_root>/<rel_path>``. An existing
destination is a collision: reported, never planned (replacement is a
separate, reviewed quarantine-and-install).
"""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.checksums import sha256_file
from whoi_cmip_reorg_code.manifest import FetchManifest, Move, RepairManifest

REPORT_SCHEMA = {"src": pl.String, "dst": pl.String, "status": pl.String}
_BAD_NC = ("empty", "not_hdf5_or_nc", "truncated", "header_error", "read_error")


def _single(v: str | None) -> str | None:
    return v if v and " " not in v.strip() else None


def plan_reorg(
    holdings: pl.DataFrame,
    drs_root: str,
    ncinfo: pl.DataFrame | None = None,
) -> tuple[list[Move], pl.DataFrame]:
    """Return (moves, report). ``report`` has every candidate with ``status``:
    planned / unmappable / corrupt / collision / duplicate_dst."""
    drs_root = drs_root.rstrip("/")
    files = holdings.filter(
        pl.col("file_type") == "file",
        pl.col("suffix") == ".nc",
        pl.col("drs_status") != "ok",
    )
    if ncinfo is not None:
        files = files.join(
            ncinfo.select(
                "path", "nc_status", "attr_activity_id", "attr_institution_id"
            ),
            on="path",
            how="left",
        )
    else:
        files = files.with_columns(
            nc_status=pl.lit(None, pl.String),
            attr_activity_id=pl.lit(None, pl.String),
            attr_institution_id=pl.lit(None, pl.String),
        )
    existing = set(holdings["path"].to_list())
    rows = []
    for r in files.sort("path").iter_rows(named=True):
        exp = r["expected_rel_path"]
        dst = None
        if exp:
            act = r["activity_id"] or _single(r["attr_activity_id"])
            inst = r["institution_id"] or _single(r["attr_institution_id"])
            if act:
                exp = exp.replace("{activity_id}", act)
            if inst:
                exp = exp.replace("{institution_id}", inst)
            if "{" not in exp:
                dst = f"{drs_root}/{exp}"
        if dst is None:
            status = "unmappable"
        elif r["nc_status"] in _BAD_NC:
            status = "corrupt"
        elif dst == r["path"]:
            continue
        elif dst in existing:
            status = "collision"
        else:
            status = "planned"
        rows.append({"src": r["path"], "dst": dst, "status": status})
    report = pl.DataFrame(rows, schema=REPORT_SCHEMA)
    dup = (
        report.filter(status="planned")
        .group_by("dst")
        .len()
        .filter(pl.col("len") > 1)["dst"]
    )
    report = report.with_columns(
        status=pl.when(
            pl.col("dst").is_in(dup.implode()) & (pl.col("status") == "planned")
        )
        .then(pl.lit("duplicate_dst"))
        .otherwise("status")
    )
    moves = [
        Move(src=r["src"], dst=r["dst"], kind="reorg")
        for r in report.filter(status="planned").iter_rows(named=True)
    ]
    return moves, report


def plan_repair_moves(
    repairs: RepairManifest,
    staging_root: str,
    quarantine_root: str,
    store_root: str,
    drs_root: str,
    verify: bool = False,
) -> tuple[list[Move], pl.DataFrame]:
    """Quarantine + install moves for staged redownloads.

    Report statuses: planned / not_staged / staged_checksum_mismatch /
    dst_occupied / original_outside_store.
    """
    staging, quarantine = Path(staging_root), Path(quarantine_root)
    store, drs = Path(store_root), Path(drs_root)
    moves: list[Move] = []
    rows = []
    seen: set[str] = set()
    for a in repairs.actions:
        if a.action != "redownload" or a.drs_rel_path in seen:
            continue
        seen.add(a.drs_rel_path)
        staged = staging / a.drs_rel_path
        final = drs / a.drs_rel_path

        def report(status, src=str(staged), dst=str(final)):
            rows.append({"src": src, "dst": dst, "status": status})

        if not staged.exists():
            report("not_staged")
            continue
        if verify and sha256_file(staged) != (a.checksum or "").lower():
            report("staged_checksum_mismatch")
            continue
        orig = a.local_path if a.local_path and os.path.lexists(a.local_path) else None
        if orig is not None and Path(orig).is_relative_to(store):
            q = quarantine / Path(orig).relative_to(store)
        elif orig is not None:
            report("original_outside_store", src=orig, dst=None)
            continue
        if os.path.lexists(final) and str(final) != orig:
            report("dst_occupied")
            continue
        if orig is not None:
            moves.append(Move(src=orig, dst=str(q), kind="quarantine", note=a.reason))
            report("planned", src=orig, dst=str(q))
        moves.append(
            Move(src=str(staged), dst=str(final), kind="install", note=a.reason)
        )
        report("planned")
    return moves, pl.DataFrame(rows, schema=REPORT_SCHEMA)


def plan_install_moves(
    fetch: FetchManifest,
    staging_root: str,
    store_root: str,
    verify: bool = False,
) -> tuple[list[Move], pl.DataFrame]:
    """Install moves for staged product files.

    Report statuses: planned / not_staged / staged_size_mismatch /
    staged_checksum_mismatch / collision.
    """
    staging, store = Path(staging_root), Path(store_root)
    moves: list[Move] = []
    rows = []
    for it in fetch.items:
        staged, final = staging / it.rel_path, store / it.rel_path
        if not staged.exists():
            status = "not_staged"
        elif it.size_bytes is not None and staged.stat().st_size != it.size_bytes:
            status = "staged_size_mismatch"
        elif verify and it.checksum and sha256_file(staged) != it.checksum.lower():
            status = "staged_checksum_mismatch"
        elif os.path.lexists(final):
            status = "collision"
        else:
            status = "planned"
            moves.append(
                Move(
                    src=str(staged),
                    dst=str(final),
                    kind="install",
                    note=f"{fetch.product}: {it.url}",
                )
            )
        rows.append({"src": str(staged), "dst": str(final), "status": status})
    return moves, pl.DataFrame(rows, schema=REPORT_SCHEMA)
