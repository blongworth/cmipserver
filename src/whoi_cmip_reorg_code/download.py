"""Download repair files into the staging tree (never into the live store).

For each ``redownload`` action: ``<staging_root>/<drs_rel_path>``. Downloads
go to ``<dst>.part`` and resume with an HTTP Range request. A finished file is
size- and checksum-checked before it's renamed to ``dst``. A file that fails
the check is kept as ``<dst>.bad`` for inspection, never installed.

An existing ``dst`` is never overwritten: it's re-verified and reported.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from whoi_cmip_reorg_code.checksums import sha256_file
from whoi_cmip_reorg_code.manifest import RepairAction, RepairManifest

CHUNK = 8 * 1024 * 1024

# opener(url, start) -> (chunks, resumed). ``resumed`` False means the server
# ignored the Range request and is sending the whole file from byte 0.
Opener = Callable[[str, int], tuple[Iterator[bytes], bool]]

RESULT_STATUSES = (
    "would_download",
    "would_resume",
    "present_ok",
    "present_bad",
    "downloaded",
    "checksum_mismatch",
    "size_mismatch",
    "error",
)


def requests_opener(timeout: float = 300.0) -> Opener:
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = "whoi-cmip-reorg-code (repair download)"

    def open_(url: str, start: int) -> tuple[Iterator[bytes], bool]:
        headers = {"Range": f"bytes={start}-"} if start else {}
        r = s.get(url, headers=headers, stream=True, timeout=timeout)
        r.raise_for_status()
        return r.iter_content(CHUNK), start > 0 and r.status_code == 206

    return open_


def _check_staging_root(root: Path, protected: list[Path]) -> None:
    root = root.resolve()
    for p in protected:
        p = p.resolve()
        if root == p or p in root.parents:
            raise ValueError(f"staging root {root} is inside protected {p}")


def _safe_join(root: Path, rel: str) -> Path:
    pp = PurePosixPath(rel)
    if pp.is_absolute() or ".." in pp.parts:
        raise ValueError(f"bad drs_rel_path {rel!r}")
    return root / pp


@dataclass
class Result:
    drs_rel_path: str
    dst: str
    status: str
    size_bytes: int | None = None
    digest: str | None = None
    error: str | None = None


def _verify(path: Path, a: RepairAction) -> tuple[bool, str, str]:
    """(ok, status_if_bad, sha256)."""
    size = path.stat().st_size
    if a.size_bytes is not None and size != a.size_bytes:
        return False, "size_mismatch", ""
    algo = (a.checksum_type or "SHA256").lower()
    digest = sha256_file(path) if algo == "sha256" else _digest(path, algo)
    return digest == a.checksum.lower(), "checksum_mismatch", digest


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)  # ESGF also has MD5 records
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download_one(a: RepairAction, dst: Path, opener: Opener, execute: bool) -> Result:
    part = dst.with_name(dst.name + ".part")
    res = Result(a.drs_rel_path, str(dst), "error", a.size_bytes)
    if dst.exists():
        ok, _, digest = _verify(dst, a)
        res.status, res.digest = ("present_ok" if ok else "present_bad"), digest
        return res
    start = part.stat().st_size if part.exists() else 0
    if not execute:
        res.status = "would_resume" if start else "would_download"
        return res
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        chunks, resumed = opener(a.url, start)
        mode = "ab" if (start and resumed) else "wb"
        with open(part, mode) as f:
            f.writelines(chunks)
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:  # includes requests' errors; keep .part to resume
        res.error = f"{type(exc).__name__}: {exc}"
        return res
    ok, bad_status, digest = _verify(part, a)
    res.digest = digest or None
    if not ok:
        part.replace(dst.with_name(dst.name + ".bad"))
        res.status = bad_status
        return res
    if dst.exists():  # appeared meanwhile: never overwrite
        res.status, res.error = "error", "dst appeared during download"
        return res
    part.replace(dst)
    res.status = "downloaded"
    return res


def run(
    manifest: RepairManifest,
    staging_root: str | os.PathLike,
    opener: Opener,
    execute: bool = False,
    protected: list[str | os.PathLike] = (),
    progress: Callable[[int, int, Result], None] | None = None,
) -> list[Result]:
    root = Path(staging_root)
    _check_staging_root(root, [Path(p) for p in protected])
    todo = [a for a in manifest.actions if a.action == "redownload"]
    seen: set[str] = set()
    out = []
    for i, a in enumerate(todo):
        if a.drs_rel_path in seen:
            continue  # the same file can be listed for several reasons
        seen.add(a.drs_rel_path)
        r = download_one(a, _safe_join(root, a.drs_rel_path), opener, execute)
        out.append(r)
        if progress:
            progress(i, len(todo), r)
    return out
