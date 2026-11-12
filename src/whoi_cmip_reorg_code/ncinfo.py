"""Read-only NetCDF health check and header metadata.

``check_file`` classifies one file and reads its global attributes, main
variable, and time axis. It never raises for a bad file; the problem goes into
``nc_status`` and ``nc_error``. Files are opened read-only (``mode="r"``).

``nc_status`` values, first failing check wins:

- ``empty``: zero bytes.
- ``not_hdf5_or_nc``: no HDF5 or netCDF classic signature at the start.
- ``truncated``: HDF5 superblock says the file ends past its actual size
  (or, with ``deep=True``, reading the last time step fails with an HDF5
  read error on an otherwise readable header).
- ``header_error``: has a signature but netCDF4 can't open it.
- ``read_error``: opens, but the ``deep`` read of the main variable fails, or
  the file can't be read at all (permissions, I/O error).
- ``ok``.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code import drs

# Files are opened read-only. HDF5 file locking fails on VAST/NFS mounts and
# makes good files report ``header_error``. Must be set before HDF5 loads
# (netCDF4 is imported lazily below).
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

NC_STATUSES = (
    "empty",
    "not_hdf5_or_nc",
    "truncated",
    "header_error",
    "read_error",
    "ok",
)

HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
CLASSIC_MAGICS = (b"CDF\x01", b"CDF\x02", b"CDF\x05")
# HDF5 allows a user block, so the superblock may sit at 0, 512, 1024, ...
_HDF5_SEARCH = (0, 512, 1024, 2048, 4096)

# Global attrs compared with filename facets: attr name -> filename facet.
ATTR_FACETS = {
    "variable_id": "variable_id",
    "table_id": "table_id",
    "source_id": "source_id",
    "experiment_id": "experiment_id",
    "variant_label": "member_id",
    "grid_label": "grid_label",
}
GLOBAL_ATTRS = (
    "tracking_id",
    "activity_id",
    "institution_id",
    "version",
    "further_info_url",
    "variable_id",
    "table_id",
    "source_id",
    "experiment_id",
    "variant_label",
    "sub_experiment_id",
    "grid_label",
    "license",
)

NCINFO_SCHEMA = {
    "path": pl.String,
    "rel_path": pl.String,
    "size_bytes": pl.Int64,
    "nc_status": pl.String,
    "nc_error": pl.String,
    "nc_format": pl.String,
    "hdf5_eof": pl.Int64,
    **{f"attr_{a}": pl.String for a in GLOBAL_ATTRS},
    "main_var": pl.String,
    "main_var_units": pl.String,
    "n_time": pl.Int64,
    "time_units": pl.String,
    "time_calendar": pl.String,
    "time_first": pl.String,
    "time_last": pl.String,
    "attr_mismatch": pl.String,
    "attr_mismatch_detail": pl.String,
}

_BOUNDS_SUFFIXES = ("_bnds", "_bounds")

# What netCDF4/HDF5/cftime raise on damaged or odd files. One bad file must
# never stop a shard, so all of these become a status, not a crash.
NC_ERRORS = (
    OSError,
    RuntimeError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    UnicodeDecodeError,
)


def hdf5_eof(path: str | os.PathLike) -> tuple[str | None, int | None]:
    """Return (format, eof) from the file signature.

    ``format`` is ``"hdf5"``, ``"classic"``, or None. ``eof`` is the
    end-of-file address from the HDF5 superblock (absolute, including any user
    block), or None if not HDF5 or the superblock can't be parsed.
    """
    with open(path, "rb") as f:
        head = f.read(4)
        if head in CLASSIC_MAGICS:
            return "classic", None
        for base in _HDF5_SEARCH:
            f.seek(base)
            if f.read(8) != HDF5_MAGIC:
                continue
            sb = f.read(64)
            if len(sb) < 1:
                return "hdf5", None
            version = sb[0]
            if version in (0, 1):
                # ver, freespace ver, root sym ver, reserved, shared hdr ver,
                # size of offsets, size of lengths, reserved
                if len(sb) < 8:
                    return "hdf5", None
                so = sb[5]
                # leaf K(2) internal K(2) flags(4) [+ v1: indexed K(2) + rsv(2)]
                off = 8 + 8 + (4 if version == 1 else 0)
                # base addr, freespace addr, EOF addr
                addrs = _read_addrs(sb, off, so, 3)
                if addrs is None:
                    return "hdf5", None
                base_addr, _, eof = addrs
            elif version in (2, 3):
                # ver, size of offsets, size of lengths, flags
                so = sb[1]
                # base addr, superblock ext addr, EOF addr
                addrs = _read_addrs(sb, 4, so, 3)
                if addrs is None:
                    return "hdf5", None
                base_addr, _, eof = addrs
            else:
                return "hdf5", None
            # The EOF address is relative to the base address (the superblock
            # position when there's a user block, else 0).
            return "hdf5", base_addr + eof
    return None, None


def _read_addrs(buf: bytes, off: int, size: int, n: int) -> tuple[int, ...] | None:
    fmt = {4: "<I", 8: "<Q"}.get(size)
    if fmt is None or len(buf) < off + size * n:
        return None
    return tuple(struct.unpack_from(fmt, buf, off + i * size)[0] for i in range(n))


def _main_var(ds, fn_var: str | None) -> str | None:
    """Pick the main data variable: ``variable_id`` attr, then the filename's
    variable, then the non-coordinate, non-bounds variable with most dims."""
    names = list(ds.variables)
    attr = getattr(ds, "variable_id", None)
    for cand in (attr, fn_var):
        if isinstance(cand, str) and cand in ds.variables:
            return cand
    data = [
        n for n in names if n not in ds.dimensions and not n.endswith(_BOUNDS_SUFFIXES)
    ]
    if not data:
        return None
    return max(data, key=lambda n: (len(ds.variables[n].dimensions), -names.index(n)))


def _attr_str(ds, name: str) -> str | None:
    v = getattr(ds, name, None)
    if v is None:
        return None
    return v if isinstance(v, str) else str(v)


def _time_info(ds) -> dict:
    out = {
        "n_time": None,
        "time_units": None,
        "time_calendar": None,
        "time_first": None,
        "time_last": None,
    }
    if "time" not in ds.variables:
        return out
    t = ds.variables["time"]
    out["n_time"] = int(t.shape[0]) if t.ndim else 1
    out["time_units"] = getattr(t, "units", None)
    out["time_calendar"] = getattr(t, "calendar", None)
    if not out["n_time"]:
        return out
    import cftime

    t.set_auto_mask(False)
    vals = [t[0], t[-1]] if t.ndim else [t[...], t[...]]
    for key, v in zip(("time_first", "time_last"), vals):
        v = float(v)
        if out["time_units"]:
            try:
                d = cftime.num2date(
                    v, out["time_units"], out["time_calendar"] or "standard"
                )
                out[key] = d.isoformat()
                continue
            except ValueError, TypeError:
                pass
        out[key] = repr(v)
    return out


def _attr_mismatch(attrs: dict, fn: dict | None) -> tuple[str | None, str | None]:
    if fn is None:
        return None, None
    bad, detail = [], []
    for attr, facet in ATTR_FACETS.items():
        a = attrs.get(f"attr_{attr}")
        f = fn[facet]
        if facet == "member_id" and f and "-" in f:
            f = f.split("-", 1)[1]  # sub-experiment prefix is not in variant_label
        if a is not None and a != f:
            bad.append(facet)
            detail.append(f"{facet}: filename={f} attr={a}")
    if not bad:
        return None, None
    return ",".join(bad), "; ".join(detail)


def _deep_read(ds, var: str) -> None:
    v = ds.variables[var]
    if v.ndim == 0:
        v[...]
    elif "time" in v.dimensions and v.shape[v.dimensions.index("time")] > 0:
        idx = [slice(None)] * v.ndim
        idx[v.dimensions.index("time")] = -1
        v[tuple(idx)]
    else:
        v[(-1,) * v.ndim]


def check_file(
    path: str | os.PathLike,
    rel_path: str | None = None,
    size_bytes: int | None = None,
    deep: bool = False,
) -> dict:
    """Check one NetCDF file. Never raises for a bad file."""
    import netCDF4

    path = os.fspath(path)
    row: dict = {k: None for k in NCINFO_SCHEMA}
    row["path"] = path
    row["rel_path"] = rel_path if rel_path is not None else os.path.basename(path)
    fn = drs.parse_filename(os.path.basename(path))

    def done(status: str, error: str | None = None) -> dict:
        row["nc_status"] = status
        row["nc_error"] = error
        return row

    try:
        size = os.stat(path).st_size
    except OSError as exc:
        return done("read_error", f"stat: {exc.strerror or exc}")
    row["size_bytes"] = size if size_bytes is None else size_bytes
    if size == 0:
        return done("empty")
    try:
        fmt, eof = hdf5_eof(path)
    except OSError as exc:
        return done("read_error", f"read: {exc.strerror or exc}")
    row["nc_format"] = fmt
    row["hdf5_eof"] = eof
    if fmt is None:
        return done("not_hdf5_or_nc")
    if eof is not None and eof > size:
        return done("truncated", f"HDF5 EOF address {eof} > file size {size}")

    try:
        ds = netCDF4.Dataset(path, mode="r")
    except NC_ERRORS as exc:
        return done("header_error", f"{type(exc).__name__}: {exc}")
    try:
        for a in GLOBAL_ATTRS:
            row[f"attr_{a}"] = _attr_str(ds, a)
        var = _main_var(ds, fn["variable_id"] if fn else None)
        row["main_var"] = var
        if var is not None:
            row["main_var_units"] = getattr(ds.variables[var], "units", None)
        try:
            row.update(_time_info(ds))
        except NC_ERRORS as exc:
            return done("read_error", f"time: {type(exc).__name__}: {exc}")
        row["attr_mismatch"], row["attr_mismatch_detail"] = _attr_mismatch(row, fn)
        if deep and var is not None:
            try:
                _deep_read(ds, var)
            except NC_ERRORS as exc:
                msg = f"{type(exc).__name__}: {exc}"
                status = "truncated" if "HDF" in msg else "read_error"
                return done(status, f"deep read of {var}: {msg}")
    finally:
        ds.close()
    return done("ok")


def nc_candidates(holdings: pl.LazyFrame, prefix: str | None = None) -> pl.LazyFrame:
    """Real ``.nc`` files from a holdings scan (no symlinks), optionally under
    a ``rel_path`` prefix."""
    lf = holdings.filter(pl.col("file_type") == "file", pl.col("suffix") == ".nc")
    if prefix:
        p = prefix.strip("/") + "/"
        lf = lf.filter(pl.col("rel_path").str.starts_with(p))
    return lf.select("path", "rel_path", "size_bytes").sort("rel_path")


def check_files(
    files: pl.DataFrame,
    out_path: str | os.PathLike,
    deep: bool = False,
    batch_rows: int = 10_000,
) -> dict:
    """Run ``check_file`` over ``files`` (path, rel_path, size_bytes) and write
    ``out_path`` parquet. Returns counts by status."""
    from whoi_cmip_reorg_code.scan import BatchWriter

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w = BatchWriter(out_path, NCINFO_SCHEMA, batch_rows)
    counts: dict[str, int] = {}
    for r in files.iter_rows(named=True):
        row = check_file(r["path"], r["rel_path"], r["size_bytes"], deep=deep)
        counts[row["nc_status"]] = counts.get(row["nc_status"], 0) + 1
        w.add(row)
    w.close()
    return {"out": out_path, "n_rows": w.n_rows, "counts": counts}
