"""Download files into the staging tree (never into the live store).

Items come from a repairs manifest (``redownload`` actions) or a product fetch
manifest; each has ``rel_path``, ``url``, ``size_bytes`` and optionally
``checksum``. Target: ``<staging_root>/<rel_path>``. Downloads go to
``<dst>.part`` and resume with an HTTP Range request. A finished file is
checked before it's renamed to ``dst``: size, then the checksum if there is
one (ESGF), otherwise size alone (servers that publish no checksums), then an
optional content ``check``. sha256 is always recorded. A file that fails is
kept as ``<dst>.bad`` for inspection, never installed. If the item has
``remote_mtime`` the file gets it as its mtime.

An existing ``dst`` is never overwritten: it's re-verified and reported.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from whoi_cmip_reorg_code.checksums import sha256_file
from whoi_cmip_reorg_code.manifest import FetchManifest, RepairManifest

CHUNK = 8 * 1024 * 1024

# The live data tree. Staging may sit elsewhere under /proj/cmip6 (e.g.
# /proj/cmip6/tmp/staging, same filesystem so installs can hard-link), but
# never inside this, nor above it.
DEFAULT_PROTECTED = "/proj/cmip6/data"

# opener(url, start) -> (chunks, resumed). ``resumed`` False means the server
# ignored the Range request and is sending the whole file from byte 0.
Opener = Callable[[str, int], tuple[Iterator[bytes], bool]]


class Item(Protocol):
    """What ``download_one`` needs (``RepairAction``, ``FetchItem``)."""

    rel_path: str | None
    url: str | None
    size_bytes: int | None
    checksum: str | None
    checksum_type: str | None


# check(path, item) -> problem or None (e.g. ``products.check_fetched``).
Check = Callable[[Path, Item], str | None]

RESULT_STATUSES = (
    "would_download",
    "would_resume",
    "present_ok",
    "present_bad",
    "downloaded",
    "checksum_mismatch",
    "size_mismatch",
    "content_bad",
    "unverifiable",
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
        if root in p.parents:
            raise ValueError(f"staging root {root} contains protected {p}")


def _safe_join(root: Path, rel: str) -> Path:
    pp = PurePosixPath(rel)
    if pp.is_absolute() or ".." in pp.parts:
        raise ValueError(f"bad rel_path {rel!r}")
    return root / pp


@dataclass
class Result:
    drs_rel_path: str  # rel_path of the item (name kept for log compatibility)
    dst: str
    status: str
    size_bytes: int | None = None
    digest: str | None = None
    error: str | None = None


def _verify(
    path: Path, a: Item, check: Check | None = None
) -> tuple[bool, str, str, str | None]:
    """(ok, status_if_bad, digest, error). Without a checksum, size is the
    upstream check and the digest is sha256 (recorded, not compared)."""
    size = path.stat().st_size
    if a.size_bytes is not None and size != a.size_bytes:
        return False, "size_mismatch", "", f"size {size} != {a.size_bytes}"
    if a.checksum is None and a.size_bytes is None:
        return False, "unverifiable", "", "no checksum and no size"
    algo = (a.checksum_type or "SHA256").lower() if a.checksum else "sha256"
    digest = sha256_file(path) if algo == "sha256" else _digest(path, algo)
    if a.checksum is not None and digest != a.checksum.lower():
        return False, "checksum_mismatch", digest, None
    if check is not None and (problem := check(path, a)):
        return False, "content_bad", digest, problem
    return True, "", digest, None


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)  # ESGF also has MD5 records
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download_one(
    a: Item, dst: Path, opener: Opener, execute: bool, check: Check | None = None
) -> Result:
    part = dst.with_name(dst.name + ".part")
    res = Result(a.rel_path, str(dst), "error", a.size_bytes)
    if dst.exists():
        ok, _, digest, err = _verify(dst, a, check)
        res.status, res.digest = ("present_ok" if ok else "present_bad"), digest
        res.error = err
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
    ok, bad_status, digest, err = _verify(part, a, check)
    res.digest, res.error = digest or None, err
    if not ok:
        part.replace(dst.with_name(dst.name + ".bad"))
        res.status = bad_status
        return res
    if dst.exists():  # appeared meanwhile: never overwrite
        res.status, res.error = "error", "dst appeared during download"
        return res
    if mt := getattr(a, "remote_mtime", None):
        ts = datetime.fromisoformat(mt).timestamp()
        os.utime(part, (ts, ts))
    part.replace(dst)
    res.status = "downloaded"
    return res


def items(manifest: RepairManifest | FetchManifest) -> Sequence[Item]:
    if isinstance(manifest, FetchManifest):
        return manifest.items
    return [a for a in manifest.actions if a.action == "redownload"]


def run(
    manifest: RepairManifest | FetchManifest,
    staging_root: str | os.PathLike,
    opener: Opener,
    execute: bool = False,
    protected: list[str | os.PathLike] = (),
    progress: Callable[[int, int, Result], None] | None = None,
    check: Check | None = None,
) -> list[Result]:
    root = Path(staging_root)
    _check_staging_root(root, [Path(p) for p in protected])
    todo = items(manifest)
    seen: set[str] = set()
    out = []
    for i, a in enumerate(todo):
        if a.rel_path in seen:
            continue  # the same file can be listed for several reasons
        seen.add(a.rel_path)
        r = download_one(a, _safe_join(root, a.rel_path), opener, execute, check)
        out.append(r)
        if progress:
            progress(i, len(todo), r)
    return out
