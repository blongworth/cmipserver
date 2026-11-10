"""Reanalysis products: manifest expansion, remote listing, store status.

A product manifest (``manifests/products/<product>.yaml``) expands into one
expected file per (group, variable, year). ``remote_status`` lists each remote
directory once (Apache autoindex) and HEADs the listed files for exact size
and Last-Modified: autoindex sizes are rounded (``199M``) and servers such as
PSL publish no checksums, so exact size is the download check.
``store_status`` compares the expected files with the store by ``stat`` only
(never a full-store walk); a holdings scan, if given, is searched for copies
elsewhere in the store.

Nothing here writes to the store.
"""

from __future__ import annotations

import html
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

import polars as pl

from whoi_cmip_reorg_code.manifest import FetchItem, ProductManifest

EXPECTED_SCHEMA = {
    "url": pl.String,
    "remote_path": pl.String,
    "rel_path": pl.String,
    "product": pl.String,
    "frequency": pl.String,
    "level": pl.String,
    "variable_id": pl.String,
    "year": pl.Int64,
    "expect_units": pl.String,
}
UPSTREAM_SCHEMA = {
    "in_upstream": pl.Boolean,
    "size_approx": pl.Int64,
    "size_bytes": pl.Int64,
    "remote_mtime": pl.String,
}
STATUS_SCHEMA = {
    **EXPECTED_SCHEMA,
    **UPSTREAM_SCHEMA,
    "local_path": pl.String,
    "local_size": pl.Int64,
    "local_mtime": pl.String,
    "status": pl.String,
    "detail": pl.String,
}

# missing: fetch it. present_differs: report only (replacement is a separate,
# reviewed quarantine-and-install). present_unchecked: --offline, no upstream
# info. extra_local: in the product dir, not in the manifest.
# possible_duplicate: same file name elsewhere in the store (from a scan).
FILE_STATUSES = (
    "missing",
    "present_ok",
    "present_differs",
    "present_unchecked",
    "present_not_upstream",
    "not_upstream",
    "extra_local",
    "possible_duplicate",
)
VERDICTS = ("new", "partial", "complete", "possible_duplicate")


def expected_files(m: ProductManifest) -> pl.DataFrame:
    return pl.DataFrame(m.expand(), schema=EXPECTED_SCHEMA)


# --- remote ------------------------------------------------------------------


class Remote(Protocol):
    def get_text(self, url: str) -> str: ...

    def head(self, url: str) -> tuple[int | None, str | None]:
        """(Content-Length, Last-Modified as ISO 8601 UTC)."""
        ...


def _iso(http_date: str | None) -> str | None:
    if not http_date:
        return None
    return parsedate_to_datetime(http_date).astimezone(UTC).isoformat()


class HttpRemote:
    def __init__(self, timeout: float = 60.0):
        import requests

        self.s = requests.Session()
        self.s.headers["User-Agent"] = "whoi-cmip-reorg-code (product listing)"
        self.timeout = timeout

    def get_text(self, url: str) -> str:
        r = self.s.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.text

    def head(self, url: str) -> tuple[int | None, str | None]:
        r = self.s.head(url, timeout=self.timeout, allow_redirects=True)
        r.raise_for_status()
        n = r.headers.get("Content-Length")
        return (int(n) if n else None), _iso(r.headers.get("Last-Modified"))


_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_HREF = re.compile(r'<a\s+href="([^"]+)"', re.IGNORECASE)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
_SIZE = re.compile(r"^([\d.]+)([KMGT]?)$")
# <pre> style autoindex: <a href="f">f</a>   2022-05-13 12:06  199M
_PRE = re.compile(
    r'<a\s+href="([^"]+)">[^<]*</a>\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(\S+)',
    re.IGNORECASE,
)
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _size(s: str) -> int | None:
    m = _SIZE.match(s.strip())
    return int(float(m[1]) * _UNITS[m[2]]) if m else None


def _is_file(href: str) -> bool:
    return not (href.startswith(("?", "/", "#")) or "://" in href or href.endswith("/"))


def parse_autoindex(page: str) -> list[dict]:
    """Files in an Apache autoindex page: name, mtime (as shown, server local
    time), size_approx (rounded). Directories and sort links are skipped."""
    out = []
    for row in _ROW.findall(page):
        href = _HREF.search(row)
        if not href or not _is_file(href[1]):
            continue
        cells = [
            re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
            for c in _TD.findall(row)
        ]
        mtime = next((c for c in cells if _DATE.fullmatch(c)), None)
        size = next((_size(c) for c in cells if _SIZE.match(c)), None)
        out.append(
            {"name": html.unescape(href[1]), "mtime": mtime, "size_approx": size}
        )
    if not out:
        for href, mtime, size in _PRE.findall(page):
            if _is_file(href):
                out.append(
                    {
                        "name": html.unescape(href),
                        "mtime": mtime,
                        "size_approx": _size(size),
                    }
                )
    return out


def list_https_index(url: str, remote: Remote) -> list[dict]:
    return parse_autoindex(remote.get_text(url))


def remote_status(
    expected: pl.DataFrame,
    base_url: str,
    remote: Remote,
    workers: int = 8,
    progress: Callable[[str], None] | None = None,
) -> pl.DataFrame:
    """``expected`` plus ``UPSTREAM_SCHEMA`` columns. Each remote directory is
    listed once; listed files are HEADed for exact size and mtime."""
    rows = expected.to_dicts()
    listed: dict[str, dict[str, dict]] = {}
    for d in sorted({str(PurePosixPath(r["remote_path"]).parent) for r in rows}):
        if progress:
            progress(f"list {d}/")
        listed[d] = {e["name"]: e for e in list_https_index(f"{base_url}{d}/", remote)}
    for r in rows:
        p = PurePosixPath(r["remote_path"])
        e = listed[str(p.parent)].get(p.name)
        r["in_upstream"] = e is not None
        r["size_approx"] = e["size_approx"] if e else None
        r["size_bytes"] = r["remote_mtime"] = None
    todo = [r for r in rows if r["in_upstream"]]
    if progress:
        progress(f"HEAD {len(todo)} files")
    with ThreadPoolExecutor(workers) as ex:
        for r, (size, mtime) in zip(
            todo, ex.map(lambda r: remote.head(r["url"]), todo)
        ):
            r["size_bytes"], r["remote_mtime"] = size, mtime
    return pl.DataFrame(rows, schema={**EXPECTED_SCHEMA, **UPSTREAM_SCHEMA})


# --- store -------------------------------------------------------------------


def _mtime_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def store_status(
    expected: pl.DataFrame,
    store_root: str | os.PathLike,
    product: str,
    scan: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """One row per expected file (plus extra_local / possible_duplicate rows).

    ``expected`` is ``expected_files`` output, optionally with the
    ``remote_status`` columns; without them (offline) nothing is compared with
    upstream. ``scan``: holdings rows (``path``, ``file_type``) to search for
    same-named files outside ``reanalysis/<product>/``.
    """
    store = Path(store_root)
    pdir = store / "reanalysis" / product
    online = "in_upstream" in expected.columns
    if not online:
        expected = expected.with_columns(
            pl.lit(None, t).alias(c) for c, t in UPSTREAM_SCHEMA.items()
        )
    rows = []
    for r in expected.to_dicts():
        p = store / r["rel_path"]
        try:
            st = p.stat()
        except FileNotFoundError:
            st = None
        r |= {
            "local_path": None,
            "local_size": None,
            "local_mtime": None,
            "detail": None,
        }
        if st is not None:
            r |= {
                "local_path": str(p),
                "local_size": st.st_size,
                "local_mtime": _mtime_iso(st.st_mtime),
            }
        up = r["in_upstream"]
        if st is None:
            r["status"] = "not_upstream" if up is False else "missing"
        elif up is None:
            r["status"] = "present_unchecked"
        elif not up:
            r["status"] = "present_not_upstream"
        else:
            why = []
            if r["size_bytes"] is not None and st.st_size != r["size_bytes"]:
                why.append(f"size local={st.st_size} upstream={r['size_bytes']}")
            if (
                r["remote_mtime"]
                and datetime.fromisoformat(r["remote_mtime"]).timestamp()
                > st.st_mtime + 2
            ):
                why.append(f"upstream newer ({r['remote_mtime']})")
            r["status"] = "present_differs" if why else "present_ok"
            r["detail"] = "; ".join(why) or None
        rows.append(r)

    known = {r["rel_path"] for r in rows}
    blank = {k: None for k in STATUS_SCHEMA}
    if pdir.is_dir():
        for p in sorted(pdir.rglob("*")):
            rel = p.relative_to(store).as_posix()
            if p.is_file() and rel not in known:
                st = p.stat()
                rows.append(
                    blank
                    | {
                        "rel_path": rel,
                        "product": product,
                        "local_path": str(p),
                        "local_size": st.st_size,
                        "local_mtime": _mtime_iso(st.st_mtime),
                        "status": "extra_local",
                    }
                )
    if scan is not None and scan.height:
        names = {
            PurePosixPath(r["rel_path"]).name: r["rel_path"]
            for r in expected.to_dicts()
        }
        pfx = str(pdir) + os.sep
        hits = scan.filter(
            pl.col("file_type") == "file"
            if "file_type" in scan.columns
            else pl.lit(True),
            ~pl.col("path").str.starts_with(pfx),
            pl.col("path").str.extract(r"([^/]+)$").is_in(list(names)),
        )
        for h in hits.sort("path").iter_rows(named=True):
            name = PurePosixPath(h["path"]).name
            rows.append(
                blank
                | {
                    "rel_path": names[name],
                    "product": product,
                    "local_path": h["path"],
                    "local_size": h.get("size_bytes"),
                    "status": "possible_duplicate",
                    "detail": f"same name as {names[name]}",
                }
            )
    return pl.DataFrame(rows, schema=STATUS_SCHEMA)


def verdict(status: pl.DataFrame) -> str:
    """new / partial / complete / possible_duplicate (for the whole product)."""
    s = set(status["status"].to_list())
    present = s & {
        "present_ok",
        "present_differs",
        "present_unchecked",
        "present_not_upstream",
    }
    if "missing" in s and "possible_duplicate" in s:
        return "possible_duplicate"
    if "missing" not in s and present:
        return "complete"
    return "partial" if present else "new"


def fetch_items(status: pl.DataFrame) -> list[FetchItem]:
    """Fetch list: only files missing locally and present upstream."""
    todo = status.filter(pl.col("status") == "missing", pl.col("in_upstream"))
    return [
        FetchItem(
            url=r["url"],
            rel_path=r["rel_path"],
            size_bytes=r["size_bytes"],
            remote_mtime=r["remote_mtime"],
            variable_id=r["variable_id"],
            year=r["year"],
            expect_units=r["expect_units"],
        )
        for r in todo.sort("rel_path").iter_rows(named=True)
    ]


# --- post-download check -----------------------------------------------------


def check_fetched(path: str | os.PathLike, item) -> str | None:
    """Content check for a downloaded file (``download.Check``). Returns the
    problem, or None if the file is a readable NetCDF holding the expected
    variable (and units), with time inside the expected year."""
    import netCDF4

    from whoi_cmip_reorg_code.ncinfo import NC_ERRORS, check_file

    row = check_file(path)
    if row["nc_status"] != "ok":
        return f"nc_status={row['nc_status']}: {row['nc_error']}"
    var = getattr(item, "variable_id", None)
    units = getattr(item, "expect_units", None)
    if var:
        try:
            with netCDF4.Dataset(path, mode="r") as ds:
                if var not in ds.variables:
                    return f"variable {var} not in file ({sorted(ds.variables)[:10]})"
                got = getattr(ds.variables[var], "units", None)
        except NC_ERRORS as exc:
            return f"open: {type(exc).__name__}: {exc}"
        if units is not None and got != units:
            return f"{var} units {got!r}, expected {units!r}"
    year = getattr(item, "year", None)
    if year is not None:
        try:
            y0, y1 = int(row["time_first"][:4]), int(row["time_last"][:4])
        except TypeError, ValueError:
            return f"time not decodable: {row['time_first']!r} {row['time_units']!r}"
        if (y0, y1) != (year, year):
            return f"time {row['time_first']}..{row['time_last']} not within {year}"
    return None
