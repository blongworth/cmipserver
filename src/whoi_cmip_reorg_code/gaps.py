"""Missing-file detection from filename time ranges (no file opens).

Within one dataset (same directory and filename facets), files are sorted by
start time. Each file must start one time step after the previous one ends.
The step comes from the time-label resolution in the filename:

    YYYY        year           YYYYMMDD       day
    YYYYMM      month          YYYYMMDDhh     hour (step from table_id)
    YYYYMMDDhhmm  minute (step from table_id)

Daily successors are checked against the standard, noleap, all_leap and
360_day calendars. A file that starts on the next day in *any* of them counts
as contiguous, since the calendar isn't known without opening the file.

Fixed fields (no time range) and climatologies (``-clim``) are not checked.

``header_gaps`` does the same from file headers (``ncinfo`` ``time_first`` /
``time_last``) for products whose filenames carry no CMIP time range
(ERA5, ocean reanalyses). It uses the same status vocabulary.
"""

from __future__ import annotations

import re
import statistics
from datetime import timedelta
from itertools import pairwise

import polars as pl

DATASET_COLS = [
    "dir",
    "source_id",
    "experiment_id",
    "member_id",
    "table_id",
    "variable_id",
    "grid_label",
]
GAP_STATUSES = (
    "ok",
    "gap",
    "overlap",
    "duplicate",
    "fixed",
    "clim",
    "mixed_time_format",
    "bad_time_label",
    "mixed_calendar",
    "no_step",
)

ISSUES_SCHEMA = {
    **{c: pl.String for c in DATASET_COLS},
    "kind": pl.String,
    "prev_name": pl.String,
    "next_name": pl.String,
    "prev_end": pl.String,
    "next_start": pl.String,
    "expected_start": pl.String,
}

_CALENDARS = ("standard", "noleap", "all_leap", "360_day")


def _sub_daily_step(table_id: str) -> timedelta | None:
    """Step for hourly/sub-hourly labels, from the table name."""
    m = re.search(r"(\d+)hr", table_id)
    if m:
        return timedelta(hours=int(m.group(1)))
    if "subhr" in table_id:
        return None
    return timedelta(hours=1) if "hr" in table_id else None


def _is_leap(y: int, cal: str) -> bool:
    if cal == "noleap":
        return False
    if cal == "all_leap":
        return True
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _days_in_month(y: int, m: int, cal: str) -> int:
    if cal == "360_day":
        return 30
    if m == 2:
        return 29 if _is_leap(y, cal) else 28
    return 31 if m in (1, 3, 5, 7, 8, 10, 12) else 30


def _next_day(y: int, m: int, d: int, cal: str) -> tuple[int, int, int] | None:
    if not (1 <= m <= 12 and 1 <= d <= _days_in_month(y, m, cal)):
        return None  # e.g. Feb 30 is only valid in 360_day
    if d < _days_in_month(y, m, cal):
        return y, m, d + 1
    return (y, m + 1, 1) if m < 12 else (y + 1, 1, 1)


def successors(label: str, table_id: str) -> set[str] | None:
    """Labels a file may start with right after a file ending at ``label``.
    None if the label can't be interpreted."""
    n = len(label)
    if not label.isdigit():
        return None
    if n == 4:
        return {f"{int(label) + 1:04d}"}
    if n == 6:
        y, m = int(label[:4]), int(label[4:])
        if not 1 <= m <= 12:
            return None
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
        return {f"{y:04d}{m:02d}"}
    y, mo, d = int(label[:4]), int(label[4:6]), int(label[6:8])
    if n == 8:
        out = set()
        for cal in _CALENDARS:
            nx = _next_day(y, mo, d, cal)
            if nx:
                out.add(f"{nx[0]:04d}{nx[1]:02d}{nx[2]:02d}")
        return out or None
    if n in (10, 12):
        step = _sub_daily_step(table_id)
        if step is None:
            return None
        hh = int(label[8:10])
        mm = int(label[10:12]) if n == 12 else 0
        out = set()
        for cal in _CALENDARS:
            if not (1 <= mo <= 12 and 1 <= d <= _days_in_month(y, mo, cal)):
                continue
            # roll over the day with this calendar's month lengths
            mins = hh * 60 + mm + int(step.total_seconds() // 60)
            dd = (y, mo, d)
            while mins >= 24 * 60:
                mins -= 24 * 60
                dd = _next_day(*dd, cal)
            out.add(
                f"{dd[0]:04d}{dd[1]:02d}{dd[2]:02d}{mins // 60:02d}{mins % 60:02d}"[:n]
            )
        return out or None
    return None


def _check_group(table_id: str, files: list[tuple[str, str, str]]) -> tuple[str, list]:
    """files: (name, start, end) sorted by (start, end). Returns (status, issues)."""
    lens = {len(s) for _, s, _ in files} | {len(e) for _, _, e in files}
    if len(lens) > 1:
        return "mixed_time_format", []
    issues = []
    for (pn, ps, pe), (nn, ns, ne) in pairwise(files):
        if (ps, pe) == (ns, ne):
            issues.append(("duplicate", pn, nn, pe, ns, None))
            continue
        succ = successors(pe, table_id)
        if succ is None:
            return "bad_time_label", []
        if ns in succ:
            continue
        exp = min(succ)
        kind = "overlap" if ns < exp else "gap"
        issues.append((kind, pn, nn, pe, ns, exp))
    if any(e < s for _, s, e in files):
        return "bad_time_label", issues
    kinds = {i[0] for i in issues}
    for k in ("gap", "overlap", "duplicate"):
        if k in kinds:
            return k, issues
    return "ok", issues


def find_gaps(holdings: pl.LazyFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return (datasets, issues).

    ``datasets``: one row per dataset with ``n_files``, ``time_start``,
    ``time_end``, ``n_gaps``, ``n_overlaps``, ``n_duplicates``, ``gap_status``.
    ``issues``: one row per gap/overlap/duplicate between consecutive files.
    Only real files whose filename parsed are considered.
    """
    files = (
        holdings.filter(
            pl.col("file_type") == "file",
            pl.col("facet_parse_ok"),
        )
        .with_columns(dir=pl.col("rel_path").str.replace(r"/[^/]*$", ""))
        .with_columns(
            pl.when(pl.col("dir") == pl.col("rel_path"))
            .then(pl.lit("."))
            .otherwise("dir")
            .alias("dir")
        )
        .select(*DATASET_COLS, "name", "time_start", "time_end", "climatology")
        .sort(*DATASET_COLS, "time_start", "time_end", "name", nulls_last=False)
        .collect()
    )
    ds_rows, issue_rows = [], []
    for key, g in files.group_by(DATASET_COLS, maintain_order=True):
        keyd = dict(zip(DATASET_COLS, key))
        starts = g["time_start"].to_list()
        n = g.height
        base = {
            **keyd,
            "n_files": n,
            "time_start": None,
            "time_end": None,
            "n_gaps": 0,
            "n_overlaps": 0,
            "n_duplicates": 0,
        }
        if all(s is None for s in starts):
            status = "fixed" if n == 1 else "duplicate"
            if n > 1:
                base["n_duplicates"] = n - 1
            ds_rows.append({**base, "gap_status": status})
            continue
        if any(s is None for s in starts):
            ds_rows.append({**base, "gap_status": "mixed_time_format"})
            continue
        base["time_start"] = min(starts)
        base["time_end"] = max(g["time_end"].to_list())
        if g["climatology"].any():
            ds_rows.append({**base, "gap_status": "clim"})
            continue
        triples = list(zip(g["name"], starts, g["time_end"]))
        status, issues = _check_group(keyd["table_id"], triples)
        for kind, pn, nn, pe, ns, exp in issues:
            issue_rows.append(
                {
                    **keyd,
                    "kind": kind,
                    "prev_name": pn,
                    "next_name": nn,
                    "prev_end": pe,
                    "next_start": ns,
                    "expected_start": exp,
                }
            )
            base[
                {"gap": "n_gaps", "overlap": "n_overlaps"}.get(kind, "n_duplicates")
            ] += 1
        ds_rows.append({**base, "gap_status": status})
    ds_schema = {
        **{c: pl.String for c in DATASET_COLS},
        "n_files": pl.Int64,
        "time_start": pl.String,
        "time_end": pl.String,
        "n_gaps": pl.Int64,
        "n_overlaps": pl.Int64,
        "n_duplicates": pl.Int64,
        "gap_status": pl.String,
    }
    return (
        pl.DataFrame(ds_rows, schema=ds_schema, orient="row"),
        pl.DataFrame(issue_rows, schema=ISSUES_SCHEMA, orient="row"),
    )


def partial_coverage(datasets: pl.DataFrame) -> pl.DataFrame:
    """Report (not flag) uneven coverage within a source_id.

    ``missing_experiment``: a (member, table, variable) present for some of the
    source's experiments but not others. ``missing_member``: a (table,
    variable) present for some members of a (source, experiment) but not
    others. Columns: source_id, kind, scope, item, present_in, missing_in.
    """
    d = datasets.select(
        "source_id", "experiment_id", "member_id", "table_id", "variable_id"
    ).unique()
    d = d.with_columns(item=pl.concat_str("table_id", "variable_id", separator="."))
    out = []
    # kind, scope columns, axis: an item is compared across the axis values
    # that exist within its scope (e.g. the experiments a member has).
    specs = [
        ("missing_experiment", ["source_id", "member_id"], "experiment_id"),
        ("missing_member", ["source_id", "experiment_id"], "member_id"),
    ]
    for kind, scope_cols, axis in specs:
        universe = d.group_by(scope_cols).agg(pl.col(axis).unique().alias("all_"))
        have = d.group_by([*scope_cols, "item"]).agg(
            pl.col(axis).unique().alias("present_in")
        )
        j = (
            have.join(universe, on=scope_cols)
            .with_columns(
                present_in=pl.col("present_in").list.sort(),
                missing_in=pl.col("all_").list.set_difference("present_in").list.sort(),
            )
            .filter(pl.col("missing_in").list.len() > 0)
        )
        out.append(
            j.select(
                "source_id",
                pl.lit(kind).alias("kind"),
                pl.col(scope_cols[1]).alias("scope"),
                "item",
                pl.col("present_in").list.join(","),
                pl.col("missing_in").list.join(","),
            )
        )
    return pl.concat(out).sort("source_id", "kind", "scope", "item")


def core_gaps(holdings: pl.LazyFrame, core) -> pl.DataFrame:
    """Expand ``core`` (a ``manifest.CoreManifest``) and count matching real
    ``.nc`` files for each combination. ``present`` is n_files > 0.

    Columns: requirement, the facets used by that requirement (as a
    ``key=value;...`` string in ``selector``), n_files, n_datasets, present.
    """
    files = holdings.filter(
        pl.col("file_type") == "file", pl.col("facet_parse_ok")
    ).collect()
    rows = []
    for req in core.requirements:
        for combo in req.expand():
            hit = files.filter(*(pl.col(k) == v for k, v in combo.items()))
            n_ds = (
                hit.select(
                    "source_id",
                    "experiment_id",
                    "member_id",
                    "table_id",
                    "variable_id",
                    "grid_label",
                    "version",
                ).n_unique()
                if hit.height
                else 0
            )
            rows.append(
                {
                    "requirement": req.name,
                    "selector": ";".join(f"{k}={v}" for k, v in combo.items()),
                    "n_files": hit.height,
                    "n_datasets": n_ds,
                    "present": hit.height > 0,
                }
            )
    return pl.DataFrame(
        rows,
        schema={
            "requirement": pl.String,
            "selector": pl.String,
            "n_files": pl.Int64,
            "n_datasets": pl.Int64,
            "present": pl.Boolean,
        },
        orient="row",
    )


# --- header-based gaps (ncinfo time axis) --------------------------------------

HEADER_ISSUES_COLS = {
    "kind": pl.String,
    "prev_name": pl.String,
    "next_name": pl.String,
    "prev_end": pl.String,
    "next_start": pl.String,
    "delta_days": pl.Float64,
    "step_days": pl.Float64,
}
_ISO = re.compile(r"^(-?\d+)-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d)(?::(\d\d))?)?")


def _cf_date(iso: str, calendar: str | None):
    """cftime datetime from an ``isoformat()`` string, or None."""
    import cftime

    m = _ISO.match(iso or "")
    if m is None:
        return None
    y, mo, d, hh, mm, ss = (int(x) if x else 0 for x in m.groups())
    try:
        return cftime.datetime(y, mo, d, hh, mm, ss, calendar=calendar or "standard")
    except ValueError:
        return None


def dataset_dir_expr(col: str = "rel_path") -> pl.Expr:
    """Directory of ``col`` with year-only path components (``1993``) replaced
    by ``*``, so a product split into year dirs is one dataset."""
    return (
        pl.when(pl.col(col).str.contains("/"))
        .then(pl.col(col).str.replace(r"/[^/]*$", ""))
        .otherwise(pl.lit("."))
        # twice: adjacent year dirs share a "/", so one pass misses every other
        .str.replace_all(r"(^|/)\d{4}(/|$)", "${1}*${2}")
        .str.replace_all(r"(^|/)\d{4}(/|$)", "${1}*${2}")
    )


def _days(td) -> float:
    return td.total_seconds() / 86400


def header_gaps(
    ncinfo: pl.LazyFrame | pl.DataFrame,
    group_cols: list[str],
    tolerance: float = 0.5,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Gaps and overlaps from header time axes. Returns (datasets, issues).

    ``ncinfo`` needs ``rel_path``, ``nc_status``, ``n_time``, ``time_first``,
    ``time_last``, ``time_calendar``, ``time_step_days`` and the scalar
    ``group_cols`` (e.g. ``dir`` from ``dataset_dir_expr`` and ``vars``,
    ``data_vars`` joined). Only ``nc_status == ok`` files are used.

    Files in a group are sorted by ``time_first``. The expected step is the
    median within-file step; if every file holds one step, the most common
    spacing of consecutive file starts (ties: the smallest). Between consecutive files ``delta =
    next_first - prev_last``: same span as the previous file is
    ``duplicate``, ``delta < (1 - tolerance) * step`` is ``overlap``,
    ``delta > (1 + tolerance) * step`` is ``gap``. The tolerance absorbs
    month lengths (28-31 days). Dates are compared in the group's calendar;
    a group with more than one calendar is ``mixed_calendar`` (the calendar
    check; ``gregorian``/``standard`` and, from 1583, ``proleptic_gregorian``
    count as one). Files without a time axis make a ``fixed`` group.
    """
    lf = ncinfo.lazy() if isinstance(ncinfo, pl.DataFrame) else ncinfo
    files = (
        lf.filter(pl.col("nc_status") == "ok")
        .with_columns(name=pl.col("rel_path").str.extract(r"([^/]+)$"))
        .select(
            *group_cols,
            "name",
            "n_time",
            "time_first",
            "time_last",
            "time_calendar",
            "time_step_days",
        )
        .sort(*group_cols, "time_first", "time_last", "name", nulls_last=False)
        .collect()
    )
    ds_rows, issue_rows = [], []
    for key, g in files.group_by(group_cols, maintain_order=True):
        keyd = dict(zip(group_cols, key))
        base = {
            **keyd,
            "n_files": g.height,
            "time_start": None,
            "time_end": None,
            "calendar": None,
            "step_days": None,
            "n_gaps": 0,
            "n_overlaps": 0,
            "n_duplicates": 0,
        }
        firsts = g["time_first"].to_list()
        if all(t is None for t in firsts):
            ds_rows.append({**base, "gap_status": "fixed"})
            continue
        if any(t is None for t in firsts):
            ds_rows.append({**base, "gap_status": "mixed_time_format"})
            continue
        base["time_start"] = min(firsts)
        base["time_end"] = max(g["time_last"].to_list())
        cals = {(c or "standard").lower() for c in g["time_calendar"].to_list()}
        cals = {"standard" if c == "gregorian" else c for c in cals}
        # proleptic_gregorian equals standard after the 1582 switch
        if cals == {"standard", "proleptic_gregorian"} and min(firsts) >= "1583":
            cals = {"standard"}
        if len(cals) > 1:
            base["calendar"] = ",".join(sorted(cals))
            ds_rows.append({**base, "gap_status": "mixed_calendar"})
            continue
        cal = cals.pop()
        base["calendar"] = cal
        rows = [
            (n, _cf_date(a, cal), _cf_date(b, cal))
            for n, a, b in zip(g["name"], firsts, g["time_last"])
        ]
        if any(a is None or b is None for _, a, b in rows):
            ds_rows.append({**base, "gap_status": "bad_time_label"})
            continue
        within = [s for s in g["time_step_days"].to_list() if s]
        between = [
            round(_days(b[1] - a[1]), 4) for a, b in pairwise(rows) if b[1] != a[1]
        ]
        if within:
            step = statistics.median(within)
        elif between:
            # most common spacing (ties: the smallest), so a few big gaps
            # don't set the step
            step = min(statistics.multimode(between))
        else:
            step = None
        base["step_days"] = step
        if len(rows) == 1:
            ds_rows.append({**base, "gap_status": "ok"})
            continue
        no_step = False
        for (pn, pa, pb), (nn, na, nb) in pairwise(rows):
            delta = _days(na - pb)
            if (pa, pb) == (na, nb):
                kind = "duplicate"
            elif not step:
                no_step = True
                continue
            elif delta < (1 - tolerance) * step:
                kind = "overlap"
            elif delta > (1 + tolerance) * step:
                kind = "gap"
            else:
                continue
            issue_rows.append(
                {
                    **keyd,
                    "kind": kind,
                    "prev_name": pn,
                    "next_name": nn,
                    "prev_end": pb.isoformat(),
                    "next_start": na.isoformat(),
                    "delta_days": delta,
                    "step_days": step,
                }
            )
            base[
                {"gap": "n_gaps", "overlap": "n_overlaps"}.get(kind, "n_duplicates")
            ] += 1
        status = "no_step" if no_step else "ok"
        for k, c in (("gap", "n_gaps"), ("overlap", "n_overlaps")):
            if base[c]:
                status = k
                break
        else:
            if base["n_duplicates"]:
                status = "duplicate"
        ds_rows.append({**base, "gap_status": status})
    key_schema = {c: files.schema[c] for c in group_cols}
    ds_schema = {
        **key_schema,
        "n_files": pl.Int64,
        "time_start": pl.String,
        "time_end": pl.String,
        "calendar": pl.String,
        "step_days": pl.Float64,
        "n_gaps": pl.Int64,
        "n_overlaps": pl.Int64,
        "n_duplicates": pl.Int64,
        "gap_status": pl.String,
    }
    return (
        pl.DataFrame(ds_rows, schema=ds_schema, orient="row"),
        pl.DataFrame(
            issue_rows, schema={**key_schema, **HEADER_ISSUES_COLS}, orient="row"
        ),
    )
