"""sha256 of a candidate set of files (read-only).

Hashing all of /proj/cmip6 would read the whole store, so ``select_candidates``
picks: files with an ``nc_status`` problem, files whose size differs from
ESGF, and a seeded random sample of healthy files per source_id. The sample
failure rate decides whether a full pass is worth it.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import polars as pl

BUF = 8 * 1024 * 1024

CANDIDATES_SCHEMA = {
    "path": pl.String,
    "rel_path": pl.String,
    "source_id": pl.String,
    "size_bytes": pl.Int64,
    "reason": pl.String,
    "expected_sha256": pl.String,
}
CHECKSUMS_SCHEMA = {
    **CANDIDATES_SCHEMA,
    "sha256": pl.String,
    "checksum_ok": pl.Boolean,
    "error": pl.String,
    "seconds": pl.Float64,
}


def sha256_file(path: str | os.PathLike, bufsize: int = BUF) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def select_candidates(
    holdings: pl.LazyFrame,
    ncinfo: pl.LazyFrame | None = None,
    esgf_compare: pl.LazyFrame | None = None,
    sample_per_source: int = 20,
    seed: int = 0,
) -> pl.DataFrame:
    """Files to hash, one row each, with every ``reason`` that applies
    (``nc_status:<s>``, ``esgf_size_mismatch``, ``sample``)."""
    files = (
        holdings.filter(
            pl.col("file_type") == "file",
            pl.col("suffix") == ".nc",
        )
        .select("path", "rel_path", "source_id", "size_bytes")
        .collect()
    )
    parts = []
    if ncinfo is not None:
        parts.append(
            ncinfo.filter(pl.col("nc_status") != "ok")
            .select("path", reason=pl.concat_str(pl.lit("nc_status:"), "nc_status"))
            .collect()
        )
    if esgf_compare is not None:
        parts.append(
            esgf_compare.filter(pl.col("esgf_status") == "size_mismatch")
            .select("path", reason=pl.lit("esgf_size_mismatch"))
            .collect()
        )
    if sample_per_source > 0:
        parts.append(
            files.filter(pl.col("source_id").is_not_null())
            .sort("path")
            .with_columns(
                _r=pl.col("path").hash(seed).rank("ordinal").over("source_id")
            )
            .filter(pl.col("_r") <= sample_per_source)
            .select("path", reason=pl.lit("sample"))
        )
    if not parts:
        return pl.DataFrame(schema=CANDIDATES_SCHEMA)
    reasons = (
        pl.concat(parts)
        .group_by("path")
        .agg(pl.col("reason").unique().sort().str.join(","))
    )
    expected = pl.DataFrame(schema={"path": pl.String, "expected_sha256": pl.String})
    if esgf_compare is not None:
        expected = (
            esgf_compare.filter(
                pl.col("esgf_checksum_type").str.to_uppercase() == "SHA256"
            )
            .select("path", expected_sha256="esgf_checksum")
            .collect()
        )
    return (
        files.join(reasons, on="path")
        .join(expected, on="path", how="left")
        .select(list(CANDIDATES_SCHEMA))
        .sort("rel_path")
    )


def verify(
    candidates: pl.DataFrame, out_path: str | os.PathLike, batch_rows: int = 1000
) -> dict:
    """Hash every candidate and write ``out_path``. Never modifies files."""
    from whoi_cmip_reorg_code.scan import BatchWriter

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w = BatchWriter(out_path, CHECKSUMS_SCHEMA, batch_rows)
    n_bad = n_err = 0
    for r in candidates.iter_rows(named=True):
        t0 = time.monotonic()
        digest = err = None
        try:
            digest = sha256_file(r["path"])
        except OSError as exc:
            err = f"{type(exc).__name__}: {exc.strerror or exc}"
            n_err += 1
        ok = None
        if digest and r["expected_sha256"]:
            ok = digest == r["expected_sha256"].lower()
            n_bad += not ok
        w.add(
            {
                **r,
                "sha256": digest,
                "checksum_ok": ok,
                "error": err,
                "seconds": time.monotonic() - t0,
            }
        )
    w.close()
    return {"out": out_path, "n_rows": w.n_rows, "n_mismatch": n_bad, "n_error": n_err}


def shard(df: pl.DataFrame, index: int, n: int) -> pl.DataFrame:
    """Rows ``index, index + n, ...`` (round-robin keeps big files spread)."""
    if not 0 <= index < n:
        raise ValueError(f"shard index {index} out of range (0-{n - 1})")
    return df.with_row_index("_i").filter(pl.col("_i") % n == index).drop("_i")
