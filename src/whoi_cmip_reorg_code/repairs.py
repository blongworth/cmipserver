"""Turn audit outputs into repair actions with evidence.

Inputs (parquet): ESGF comparison (required: it is the source of truth for
replacements), plus optional ncinfo, checksums, and gap issues.

Rules:

- A local file is **bad** if ``nc_status`` is not ok, its size differs from
  ESGF, or its sha256 differs from ESGF.
- Bad, with a usable ESGF record (HTTP url + checksum, version not retracted):
  ``redownload`` into staging + ``quarantine`` the original.
- Bad, no usable ESGF record: ``report_only`` (``corrupt_not_on_esgf``).
- ESGF file missing from a version we hold: ``redownload``.
- ``report_only`` per dataset: ``retracted``, ``superseded``,
  ``dataset_not_on_esgf``; per file: ``attr_mismatch``; per issue: time
  ``gap``/``overlap``/``duplicate`` from ``find_gaps``.

Nothing here touches files. Downloads and moves read the manifest later.
"""

from __future__ import annotations

import polars as pl

from whoi_cmip_reorg_code.esgf import QUERY_FACETS
from whoi_cmip_reorg_code.manifest import RepairAction

BAD_NC = ("empty", "not_hdf5_or_nc", "truncated", "header_error", "read_error")
DRS_ORDER = (
    "activity_id",
    "institution_id",
    "source_id",
    "experiment_id",
    "member_id",
    "table_id",
    "variable_id",
    "grid_label",
)


def _drs_rel(r: dict) -> str | None:
    parts = [r.get(k) for k in DRS_ORDER] + [r.get("esgf_version"), r.get("name")]
    return "/".join(parts) if all(parts) else None


def _dataset_id(r: dict, version_key: str = "version") -> str:
    parts = [r.get(k) or "?" for k in DRS_ORDER]
    v = r.get(version_key)
    return ".".join(["CMIP6", *parts, *([v] if v else [])])


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def plan(
    esgf_compare: pl.DataFrame,
    esgf_files: pl.DataFrame,
    esgf_missing_local: pl.DataFrame,
    ncinfo: pl.DataFrame | None = None,
    checksums: pl.DataFrame | None = None,
    gap_issues: pl.DataFrame | None = None,
) -> list[RepairAction]:
    """Build the action list (sorted: redownload, quarantine, report_only)."""
    drs_cols = esgf_files.select(
        *QUERY_FACETS,
        pl.col("version").alias("esgf_version"),
        "name",
        "activity_id",
        "institution_id",
        "retracted",
    ).rename({"retracted": "esgf_file_retracted"})
    f = esgf_compare.join(
        drs_cols, on=[*QUERY_FACETS, "esgf_version", "name"], how="left"
    )
    if ncinfo is not None:
        f = f.join(
            ncinfo.select("path", "nc_status", "nc_error", "attr_mismatch_detail"),
            on="path",
            how="left",
        )
    else:
        f = f.with_columns(
            nc_status=pl.lit(None, pl.String),
            nc_error=pl.lit(None, pl.String),
            attr_mismatch_detail=pl.lit(None, pl.String),
        )
    if checksums is not None:
        f = f.join(
            checksums.select("path", "sha256", "checksum_ok"), on="path", how="left"
        )
    else:
        f = f.with_columns(
            sha256=pl.lit(None, pl.String), checksum_ok=pl.lit(None, pl.Boolean)
        )

    actions: list[RepairAction] = []
    for r in f.iter_rows(named=True):
        why = []
        if r["nc_status"] in BAD_NC:
            why.append(f"nc_{r['nc_status']}")
        if r["esgf_status"] == "size_mismatch":
            why.append("size_mismatch")
        if r["checksum_ok"] is False:
            why.append("checksum_mismatch")
        evidence = _clean(
            {
                "nc_status": r["nc_status"],
                "nc_error": r["nc_error"],
                "esgf_status": r["esgf_status"],
                "local_size": r["size_bytes"],
                "esgf_size": r["esgf_size"],
                "local_sha256": r["sha256"],
                "esgf_checksum": r["esgf_checksum"],
                "esgf_version": r["esgf_version"],
            }
        )
        if why:
            reason = "+".join(why)
            drs_rel = _drs_rel(r)
            usable = (
                r["esgf_url"]
                and r["esgf_checksum"]
                and drs_rel
                and not r["retracted"]
                and not r["esgf_file_retracted"]
            )
            if usable:
                actions.append(
                    RepairAction(
                        action="redownload",
                        reason=reason,
                        dataset_id=_dataset_id(r, "esgf_version"),
                        local_path=r["path"],
                        drs_rel_path=drs_rel,
                        url=r["esgf_url"],
                        checksum=r["esgf_checksum"],
                        checksum_type=r["esgf_checksum_type"],
                        size_bytes=r["esgf_size"],
                        evidence=evidence,
                    )
                )
                actions.append(
                    RepairAction(
                        action="quarantine",
                        reason=reason,
                        dataset_id=_dataset_id(r, "esgf_version"),
                        local_path=r["path"],
                        drs_rel_path=drs_rel,
                        evidence=evidence,
                    )
                )
            else:
                actions.append(
                    RepairAction(
                        action="report_only",
                        reason=f"corrupt_not_on_esgf:{reason}",
                        dataset_id=_dataset_id(r),
                        local_path=r["path"],
                        evidence=evidence,
                    )
                )
        if r["attr_mismatch_detail"]:
            actions.append(
                RepairAction(
                    action="report_only",
                    reason="attr_mismatch",
                    dataset_id=_dataset_id(r),
                    local_path=r["path"],
                    evidence={"detail": r["attr_mismatch_detail"]},
                )
            )

    for r in esgf_missing_local.iter_rows(named=True):
        r = {**r, "esgf_version": r["version"]}
        drs_rel = _drs_rel(r)
        ev = _clean({"esgf_size": r["size_bytes"], "esgf_version": r["version"]})
        if r["url"] and r["checksum"] and drs_rel and not r["retracted"]:
            actions.append(
                RepairAction(
                    action="redownload",
                    reason="missing_local",
                    dataset_id=_dataset_id(r),
                    drs_rel_path=drs_rel,
                    url=r["url"],
                    checksum=r["checksum"],
                    checksum_type=r["checksum_type"],
                    size_bytes=r["size_bytes"],
                    evidence=ev,
                )
            )
        else:
            actions.append(
                RepairAction(
                    action="report_only",
                    reason="missing_local_no_usable_esgf_record",
                    dataset_id=_dataset_id(r),
                    evidence=_clean({**ev, "name": r["name"]}),
                )
            )

    # dataset-level reports, one per local dataset directory
    ds = (
        f.with_columns(dir=pl.col("rel_path").str.replace(r"/[^/]*$", ""))
        .group_by("dir", *QUERY_FACETS, "version")
        .agg(
            pl.col("activity_id").drop_nulls().first(),
            pl.col("institution_id").drop_nulls().first(),
            pl.col("esgf_status").unique().alias("statuses"),
            pl.col("superseded").any(),
            pl.col("retracted").any(),
            pl.col("latest_version").first(),
            pl.len().alias("n_files"),
        )
        .sort("dir")
    )
    for r in ds.iter_rows(named=True):
        base = {"dir": r["dir"], "n_files": r["n_files"]}
        if r["retracted"]:
            actions.append(
                RepairAction(
                    action="report_only",
                    reason="retracted",
                    dataset_id=_dataset_id(r),
                    evidence=base,
                )
            )
        if r["superseded"]:
            actions.append(
                RepairAction(
                    action="report_only",
                    reason="superseded",
                    dataset_id=_dataset_id(r),
                    evidence={**base, "latest_version": r["latest_version"]},
                )
            )
        if r["statuses"] == ["dataset_not_on_esgf"]:
            actions.append(
                RepairAction(
                    action="report_only",
                    reason="dataset_not_on_esgf",
                    dataset_id=_dataset_id(r),
                    evidence=base,
                )
            )

    if gap_issues is not None:
        for r in gap_issues.iter_rows(named=True):
            actions.append(
                RepairAction(
                    action="report_only",
                    reason=f"time_{r['kind']}",
                    dataset_id=_dataset_id(r),
                    evidence=_clean(
                        {
                            "dir": r["dir"],
                            "prev_name": r["prev_name"],
                            "next_name": r["next_name"],
                            "prev_end": r["prev_end"],
                            "next_start": r["next_start"],
                            "expected_start": r["expected_start"],
                        }
                    ),
                )
            )

    order = {"redownload": 0, "quarantine": 1, "report_only": 2}
    return sorted(
        actions,
        key=lambda a: (
            order[a.action],
            a.reason,
            a.dataset_id or "",
            a.local_path or "",
            a.drs_rel_path or "",
        ),
    )


def summary_markdown(actions: list[RepairAction], title: str) -> str:
    """Human-readable review sheet for the repair manifest."""
    df = pl.DataFrame(
        [
            {
                "action": a.action,
                "reason": a.reason,
                "size_bytes": a.size_bytes or 0,
                "dataset_id": a.dataset_id,
                "path": a.local_path or a.drs_rel_path or "",
            }
            for a in actions
        ],
        schema={
            "action": pl.String,
            "reason": pl.String,
            "size_bytes": pl.Int64,
            "dataset_id": pl.String,
            "path": pl.String,
        },
    )
    lines = [f"# {title}", ""]
    if df.is_empty():
        return "\n".join([*lines, "No actions.", ""])
    counts = (
        df.group_by("action", "reason")
        .agg(n=pl.len(), gb=pl.col("size_bytes").sum() / 1e9)
        .sort("action", "reason")
    )
    lines += [
        "| action | reason | n | download GB |",
        "|---|---|---:|---:|",
        *(
            f"| {r['action']} | {r['reason']} | {r['n']} | {r['gb']:.2f} |"
            for r in counts.iter_rows(named=True)
        ),
        "",
        "Redownloads go to the staging tree. Quarantine and install moves are",
        "planned separately (plan_moves.py) and need written approval.",
        "",
    ]
    for action in ("redownload", "quarantine", "report_only"):
        sub = df.filter(action=action)
        if sub.is_empty():
            continue
        lines += [f"## {action} ({sub.height})", ""]
        for r in sub.head(200).iter_rows(named=True):
            lines.append(f"- `{r['reason']}` {r['dataset_id']}: `{r['path']}`")
        if sub.height > 200:
            lines.append(f"- ... {sub.height - 200} more (see the YAML)")
        lines.append("")
    return "\n".join(lines)
