"""Read-only holdings scanner.

Walks a tree with ``os.scandir`` (never following symlinks) and writes one row
per file or symlink: file stats, link info, CMIP6 facets parsed from the
filename/path, and DRS conformance. OS errors go to a separate errors table.

Rows are flushed in batches to part files, then streamed into a single parquet
file, so memory stays bounded regardless of tree size.
"""

from __future__ import annotations

import os
import shutil
import stat as stat_mod
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code import drs

ROOT_FILES_SHARD = "_root_files"
DEFAULT_BATCH_ROWS = 100_000

HOLDINGS_SCHEMA = {
    "path": pl.String,
    "rel_path": pl.String,
    "top_dir": pl.String,
    "name": pl.String,
    "suffix": pl.String,
    "file_type": pl.String,
    "size_bytes": pl.Int64,
    "alloc_bytes": pl.Int64,
    "mtime_ns": pl.Int64,
    "inode": pl.UInt64,
    "dev": pl.UInt64,
    "nlink": pl.Int64,
    "is_symlink": pl.Boolean,
    "link_target": pl.String,
    "link_resolves": pl.Boolean,
    "variable_id": pl.String,
    "table_id": pl.String,
    "source_id": pl.String,
    "experiment_id": pl.String,
    "member_id": pl.String,
    "grid_label": pl.String,
    "time_start": pl.String,
    "time_end": pl.String,
    "climatology": pl.Boolean,
    "version": pl.String,
    "activity_id": pl.String,
    "institution_id": pl.String,
    "facet_parse_ok": pl.Boolean,
    "drs_status": pl.String,
    "drs_depth": pl.Int32,
    "expected_rel_path": pl.String,
}

ERRORS_SCHEMA = {
    "path": pl.String,
    "rel_path": pl.String,
    "op": pl.String,
    "errno": pl.Int64,
    "error": pl.String,
}

_FACET_COLS = (
    *drs.FILENAME_FACETS,
    "time_start",
    "time_end",
    "climatology",
)


def _norm_root(root: str | os.PathLike) -> str:
    return os.path.abspath(os.fspath(root)).rstrip(os.sep) or os.sep


def _rel(root: str, path: str) -> str:
    return path[len(root) :].lstrip(os.sep)


def list_shards(root: str | os.PathLike) -> list[str]:
    """Sorted top-level directories of ``root``, plus ``_root_files`` if there
    are non-directory entries (files, symlinks) directly under root."""
    root = _norm_root(root)
    dirs, has_files = [], False
    with os.scandir(root) as it:
        for e in it:
            if e.is_dir(follow_symlinks=False):
                dirs.append(e.name)
            else:
                has_files = True
    shards = sorted(dirs)
    if has_files:
        shards.append(ROOT_FILES_SHARD)
    return shards


def _file_type(mode: int) -> str:
    if stat_mod.S_ISREG(mode):
        return "file"
    if stat_mod.S_ISLNK(mode):
        return "symlink"
    if stat_mod.S_ISDIR(mode):
        return "dir"
    return "other"


def make_row(root: str, path: str, st: os.stat_result) -> tuple[dict, list[dict]]:
    """Build a holdings row from an ``lstat`` result. Returns (row, errors)."""
    errors: list[dict] = []
    rel_path = _rel(root, path)
    name = os.path.basename(path)
    parts = rel_path.split(os.sep)
    is_symlink = stat_mod.S_ISLNK(st.st_mode)

    link_target = link_resolves = None
    if is_symlink:
        try:
            link_target = os.readlink(path)
        except OSError as exc:
            errors.append(_err(root, path, "readlink", exc))
        link_resolves = os.path.exists(path)

    fn = drs.parse_filename(name)
    pf = drs.parse_path_facets(rel_path, fn)
    chk = drs.drs_check(rel_path)

    row = {
        "path": path,
        "rel_path": rel_path,
        "top_dir": parts[0] if len(parts) > 1 else ".",
        "name": name,
        "suffix": os.path.splitext(name)[1],
        "file_type": _file_type(st.st_mode),
        "size_bytes": st.st_size,
        "alloc_bytes": getattr(st, "st_blocks", 0) * 512,
        "mtime_ns": st.st_mtime_ns,
        "inode": st.st_ino,
        "dev": st.st_dev,
        "nlink": st.st_nlink,
        "is_symlink": is_symlink,
        "link_target": link_target,
        "link_resolves": link_resolves,
        **{k: (fn[k] if fn else None) for k in _FACET_COLS},
        **pf,
        "facet_parse_ok": fn is not None,
        **chk,
    }
    return row, errors


def _err(root: str, path: str, op: str, exc: OSError) -> dict:
    return {
        "path": path,
        "rel_path": _rel(root, path),
        "op": op,
        "errno": exc.errno,
        "error": exc.strerror or str(exc),
    }


def iter_entries(root: str, shard: str | None) -> Iterator[tuple]:
    """Yield ``("entry", path, lstat_result)`` for each file/symlink and
    ``("error", op, path, exc)`` for each OS failure.

    ``shard=None`` walks everything; a directory name walks that top-level
    directory (or a nested directory, e.g. ``CMIP/NCAR/CESM2``, for small test
    scans; paths stay relative to ``root``); ``_root_files`` yields only non-directory entries of root.
    """
    if shard == ROOT_FILES_SHARD:
        try:
            with os.scandir(root) as it:
                entries = list(it)
        except OSError as exc:
            yield ("error", "scandir", root, exc)
            return
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    continue
                yield ("entry", e.path, e.stat(follow_symlinks=False))
            except OSError as exc:
                yield ("error", "stat", e.path, exc)
        return

    if shard is not None and (os.path.isabs(shard) or ".." in shard.split("/")):
        raise ValueError(f"shard {shard!r} must be a relative path under {root}")
    start = root if shard is None else os.path.join(root, shard.strip("/"))
    if shard is not None and (os.path.islink(start) or not os.path.isdir(start)):
        raise ValueError(f"shard {shard!r} is not a directory under {root}")

    stack = [start]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError as exc:
            yield ("error", "scandir", d, exc)
            continue
        subdirs = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    subdirs.append(e.path)
                    continue
                yield ("entry", e.path, e.stat(follow_symlinks=False))
            except OSError as exc:
                yield ("error", "stat", e.path, exc)
        # Reverse so pop() visits subdirectories in sorted order.
        stack.extend(sorted(subdirs, reverse=True))


def _to_frame(rows: list[dict], schema: dict) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=schema, orient="row")


def _finalize_holdings(lf: pl.LazyFrame) -> pl.LazyFrame:
    return lf.with_columns(
        mtime=pl.from_epoch("mtime_ns", time_unit="ns").dt.replace_time_zone("UTC")
    ).drop("mtime_ns")


@dataclass
class BatchWriter:
    """Accumulate rows, flush to part files, then stream into one parquet."""

    out_path: Path
    schema: dict
    batch_rows: int
    finalize: object = None
    rows: list[dict] = field(default_factory=list)
    n_rows: int = 0
    parts: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.parts_dir = self.out_path.with_name(f".parts_{self.out_path.stem}")
        shutil.rmtree(self.parts_dir, ignore_errors=True)
        self.parts_dir.mkdir(parents=True)

    def add(self, row: dict) -> None:
        self.rows.append(row)
        self.n_rows += 1
        if len(self.rows) >= self.batch_rows:
            self.flush()

    def flush(self) -> None:
        if not self.rows and self.parts:
            return
        part = self.parts_dir / f"part-{len(self.parts):05d}.parquet"
        _to_frame(self.rows, self.schema).write_parquet(part)
        self.parts.append(part)
        self.rows = []

    def close(self) -> None:
        self.flush()
        lf = pl.scan_parquet(self.parts)
        if self.finalize is not None:
            lf = self.finalize(lf)
        tmp = self.out_path.with_name(self.out_path.name + ".tmp")
        lf.sink_parquet(tmp)
        tmp.replace(self.out_path)
        shutil.rmtree(self.parts_dir, ignore_errors=True)


def scan(
    root: str | os.PathLike,
    out_dir: str | os.PathLike,
    shard: str | None = None,
    batch_rows: int = DEFAULT_BATCH_ROWS,
) -> dict:
    """Scan ``root`` (or one shard of it) and write
    ``holdings_<shard>.parquet`` and ``errors_<shard>.parquet`` to ``out_dir``.

    Returns a dict with output paths and row counts.
    """
    root = _norm_root(root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = shard.strip("/").replace("/", "__") if shard else "all"
    holdings_path = out_dir / f"holdings_{tag}.parquet"
    errors_path = out_dir / f"errors_{tag}.parquet"

    hw = BatchWriter(holdings_path, HOLDINGS_SCHEMA, batch_rows, _finalize_holdings)
    ew = BatchWriter(errors_path, ERRORS_SCHEMA, batch_rows)
    for item in iter_entries(root, shard):
        if item[0] == "error":
            _, op, path, exc = item
            ew.add(_err(root, path, op, exc))
            continue
        _, path, st = item
        row, errs = make_row(root, path, st)
        hw.add(row)
        for e in errs:
            ew.add(e)
    hw.close()
    ew.close()
    return {
        "holdings": holdings_path,
        "errors": errors_path,
        "n_rows": hw.n_rows,
        "n_errors": ew.n_rows,
    }


def holdings_tags(scan_dir: str | os.PathLike) -> list[str]:
    """Shard tags of the ``holdings_<tag>.parquet`` files in ``scan_dir``,
    sorted. Downstream array jobs (``check_nc.py``) index into this list."""
    return sorted(
        p.name.removeprefix("holdings_").removesuffix(".parquet")
        for p in Path(scan_dir).glob("holdings_*.parquet")
    )


def pick_tag(
    scan_dir: str | os.PathLike, shard: str | None, shard_index: int | None
) -> str:
    """Resolve ``--shard``/``--shard-index`` (default ``$SLURM_ARRAY_TASK_ID``)
    against ``holdings_tags``. A shard like ``CMIP/NCAR`` maps to tag
    ``CMIP__NCAR``. Raises ValueError if nothing matches."""
    tags = holdings_tags(scan_dir)
    if not tags:
        raise ValueError(f"no holdings_*.parquet in {scan_dir}")
    if shard is None and shard_index is None:
        env = os.environ.get("SLURM_ARRAY_TASK_ID")
        if env is None:
            raise ValueError("give --shard or --shard-index (or run as an array job)")
        shard_index = int(env)
    if shard_index is not None:
        if not 0 <= shard_index < len(tags):
            raise ValueError(
                f"shard index {shard_index} out of range (0-{len(tags) - 1})"
            )
        return tags[shard_index]
    tag = shard.strip("/").replace("/", "__")
    if tag not in tags:
        raise ValueError(f"no holdings_{tag}.parquet in {scan_dir}")
    return tag
