"""CMIP6 filename and DRS path parsing.

Minimal first version: parse facets from filenames and paths, and check how a
file's location compares with the CMIP6 DRS that ecgtools expects:

    <activity_id>/<institution_id>/<source_id>/<experiment_id>/<member_id>/
    <table_id>/<variable_id>/<grid_label>/<version>/<filename>

Anything that does not match returns ``None`` or a partial result. Nothing is
guessed.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

FILENAME_FACETS = (
    "variable_id",
    "table_id",
    "source_id",
    "experiment_id",
    "member_id",
    "grid_label",
)

# Directory levels below the mip_era (``CMIP6``) directory, in DRS order.
DRS_DIR_FACETS = (
    "activity_id",
    "institution_id",
    "source_id",
    "experiment_id",
    "member_id",
    "table_id",
    "variable_id",
    "grid_label",
    "version",
)
DRS_DEPTH = len(DRS_DIR_FACETS)

FILENAME_RE = re.compile(
    r"^(?P<variable_id>[A-Za-z0-9]+)"
    r"_(?P<table_id>[A-Za-z0-9]+)"
    r"_(?P<source_id>[A-Za-z0-9-]+)"
    r"_(?P<experiment_id>[A-Za-z0-9-]+)"
    r"_(?P<member_id>(?:[A-Za-z0-9]+-)?r\d+i\d+p\d+f\d+)"
    r"_(?P<grid_label>g[A-Za-z0-9]+)"
    r"(?:_(?P<time_start>\d+)-(?P<time_end>\d+)(?P<clim>-clim)?)?"
    r"\.nc$"
)
# DRS version dirs are ``vYYYYMMDD``. The WHOI store also uses plain numeric
# dirs (``.../gn/1/``). Both count as a valid version dir (so they are not
# reorganized); ``version_scheme`` records which one a path uses.
VERSION_RE = re.compile(r"^(v\d{8}|\d+)$")
MIP_ERA = "cmip6"

DRS_STATUSES = (
    "not_nc",
    "bad_filename",
    "wrong_depth",
    "bad_version_dir",
    "path_filename_mismatch",
    "ok",
)


def version_scheme(version: str | None) -> str | None:
    """``"date"`` for ``vYYYYMMDD``, ``"bare_date"`` for ``YYYYMMDD`` (no ``v``),
    ``"numeric"`` for other digit-only dirs such as ``1``, else None."""
    if version is None or not VERSION_RE.match(version):
        return None
    if version.startswith("v"):
        return "date"
    return "bare_date" if len(version) == 8 else "numeric"


def parse_filename(name: str) -> dict[str, str | None] | None:
    """Parse CMIP6 facets from a filename, or return None if it doesn't match.

    ``time_start``/``time_end`` are the raw strings from the time range (None for
    fixed fields). ``-clim`` filenames set ``climatology`` to True.
    """
    m = FILENAME_RE.match(name)
    if m is None:
        return None
    d = m.groupdict()
    clim = d.pop("clim")
    d["climatology"] = clim is not None
    return d


def _drs_dirs(rel_path: str) -> list[str]:
    """Directory components below the DRS anchor.

    The anchor is just after the last ``CMIP6`` component (case-insensitive) if
    the path has one, otherwise the scan root (assumed to be the mip_era dir).
    """
    parts = PurePosixPath(rel_path).parts[:-1]
    lowered = [p.lower() for p in parts]
    if MIP_ERA in lowered:
        anchor = len(lowered) - 1 - lowered[::-1].index(MIP_ERA)
        return list(parts[anchor + 1 :])
    return list(parts)


def parse_path_facets(
    rel_path: str, filename_facets: dict | None = None
) -> dict[str, str | None]:
    """Facets recoverable from the directory path.

    ``version`` is the deepest version directory (``vYYYYMMDD`` or numeric), if any. ``activity_id``
    and ``institution_id`` are only returned when the nine directories directly
    above the file look like a DRS path: version dir is ``vYYYYMMDD`` and (if
    filename facets are given) the six shared facets agree with the filename.
    """
    out: dict[str, str | None] = {
        "version": None,
        "activity_id": None,
        "institution_id": None,
    }
    dirs = PurePosixPath(rel_path).parts[:-1]
    for p in reversed(dirs):
        if VERSION_RE.match(p):
            out["version"] = p
            break
    if len(dirs) < DRS_DEPTH:
        return out
    tail = dict(zip(DRS_DIR_FACETS, dirs[-DRS_DEPTH:]))
    if not VERSION_RE.match(tail["version"]):
        return out
    if filename_facets is not None and any(
        tail[k] != filename_facets[k] for k in FILENAME_FACETS
    ):
        return out
    out["activity_id"] = tail["activity_id"]
    out["institution_id"] = tail["institution_id"]
    return out


def drs_check(rel_path: str) -> dict:
    """Classify a path against the CMIP6 DRS.

    Returns ``drs_status`` (first failing check, see ``DRS_STATUSES``),
    ``drs_depth`` (directory levels below the DRS anchor), and
    ``expected_rel_path``: the DRS path (relative to the mip_era dir) derived
    from the filename plus the version found. Unknown activity/institution are
    left as ``{activity_id}``/``{institution_id}`` placeholders; None if the
    filename doesn't parse or no version directory was found.
    """
    path = PurePosixPath(rel_path)
    name = path.name
    dirs = _drs_dirs(rel_path)
    fn = parse_filename(name)
    pf = parse_path_facets(rel_path, fn)

    expected = None
    if fn is not None and pf["version"] is not None:
        expected = "/".join(
            [
                pf["activity_id"] or "{activity_id}",
                pf["institution_id"] or "{institution_id}",
                *(fn[k] for k in DRS_DIR_FACETS[2:-1]),
                pf["version"],
                name,
            ]
        )

    if path.suffix != ".nc":
        status = "not_nc"
    elif fn is None:
        status = "bad_filename"
    elif len(dirs) != DRS_DEPTH:
        status = "wrong_depth"
    else:
        d = dict(zip(DRS_DIR_FACETS, dirs))
        if not VERSION_RE.match(d["version"]):
            status = "bad_version_dir"
        elif any(d[k] != fn[k] for k in FILENAME_FACETS):
            status = "path_filename_mismatch"
        else:
            status = "ok"

    return {
        "drs_status": status,
        "version_scheme": version_scheme(pf["version"]),
        "drs_depth": len(dirs),
        "expected_rel_path": expected,
    }
