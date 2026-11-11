"""Apply and roll back file moves with an append-only log.

Safety rules:

- Dry run unless ``execute=True``; executing requires an approved manifest
  (``approved_by`` + ``approved_on``).
- Never overwrites: a move is ``link(src, dst)`` then ``unlink(src)``.
  ``link`` fails if ``dst`` exists, so there is no race either. Same
  filesystem only (no copy fallback).
- The whole manifest is checked first, in order (a path vacated by an earlier
  move may be refilled by a later one). Any conflict blocks execution.
- Every step is logged as JSON lines (``start`` before, ``done`` after,
  fsynced). A re-run resumes: finished moves are skipped, and a move that
  was interrupted or failed (e.g. unlink refused, or an NFS retry reporting
  an error for a step that succeeded) is settled from the filesystem. The
  ``start`` entry records the source inode, so a file at ``dst`` is only
  taken as ours if it is that inode; anything else is reported, not touched.
- Rollback replays the log backwards: moves dst -> src (same no-overwrite
  rule), then removes directories the run created, only if empty.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from whoi_cmip_reorg_code.manifest import MoveManifest


class MoveError(RuntimeError):
    pass


@dataclass
class MoveLog:
    path: Path
    run_id: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def write(self, **entry) -> None:
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "user": getpass.getuser(),
            "host": socket.gethostname(),
            **entry,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())


def _same_file(a: str, b: str) -> bool:
    try:
        sa, sb = os.lstat(a), os.lstat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


Ino = tuple[int, int] | None


def _state(entries: list[dict]) -> dict[tuple[str, str], tuple[str, Ino]]:
    """Per (src, dst): last phase ('start', 'fail', 'done', 'unmoved') and the
    (dev, ino) of src from the latest ``start`` (None in older logs)."""
    st: dict[tuple[str, str], tuple[str, Ino]] = {}
    for e in entries:
        if e["op"] == "move":
            k: tuple[str, str] = (e["src"], e["dst"])
            ino: Ino = st.get(k, ("", None))[1]
            if e["phase"] == "start":
                ino = (e["dev"], e["ino"]) if "ino" in e else None
            st[k] = (e["phase"], ino)
        elif e["op"] == "unmove" and e["phase"] == "done":
            st[(e["src"], e["dst"])] = ("unmoved", None)
    return st


def _is_ino(path: str, ino: Ino) -> bool:
    if ino is None:
        return False
    try:
        s = os.lstat(path)
    except OSError:
        return False
    return (s.st_dev, s.st_ino) == ino


@dataclass
class Plan:
    """Result of checking a manifest against the filesystem and the log."""

    todo: list[tuple[int, str, str, str]] = field(default_factory=list)
    finish_unlink: list[tuple[int, str, str]] = field(default_factory=list)
    finish_log: list[tuple[int, str, str]] = field(default_factory=list)
    done: int = 0
    problems: list[str] = field(default_factory=list)


def check(manifest: MoveManifest, log: MoveLog) -> Plan:
    """Classify every move, simulating earlier moves in manifest order."""
    state = _state(log.read())
    p = Plan()
    vacated: set[str] = set()
    filled: set[str] = set()

    def exists(path: str) -> bool:
        if path in filled:
            return True
        if path in vacated:
            return False
        return os.path.lexists(path)

    for i, m in enumerate(manifest.moves):
        s, ino = state.get((m.src, m.dst), (None, None))
        src_on_disk, dst_on_disk = os.path.lexists(m.src), os.path.lexists(m.dst)
        if s == "done":
            p.done += 1
            vacated.add(m.src)
            filled.add(m.dst)
            continue
        if s in ("start", "fail"):
            # interrupted or failed: decide from the filesystem
            if src_on_disk and dst_on_disk:
                if _same_file(m.src, m.dst):
                    p.finish_unlink.append((i, m.src, m.dst))
                else:
                    p.problems.append(
                        f"#{i} dst exists and is not this file "
                        f"(refusing to overwrite): {m.dst}"
                    )
            elif dst_on_disk:
                # src gone: the move happened only if dst is the logged inode
                # (older logs have no inode; trust an interrupted start)
                if _is_ino(m.dst, ino) or (ino is None and s == "start"):
                    p.finish_log.append((i, m.src, m.dst))
                else:
                    p.problems.append(
                        f"#{i} src gone and dst is not the moved file: {m.dst}"
                    )
            elif src_on_disk:
                p.todo.append((i, m.src, m.dst, m.kind))
            else:
                p.problems.append(f"#{i} {s} move, src and dst both missing: {m.src}")
            vacated.add(m.src)
            filled.add(m.dst)
            continue
        if not exists(m.src):
            p.problems.append(f"#{i} src missing: {m.src}")
        elif os.path.isdir(m.src) and not os.path.islink(m.src):
            p.problems.append(f"#{i} src is a directory: {m.src}")
        if exists(m.dst):
            p.problems.append(f"#{i} dst exists (refusing to overwrite): {m.dst}")
        p.todo.append((i, m.src, m.dst, m.kind))
        vacated.add(m.src)
        filled.add(m.dst)
        filled.discard(m.src)
        vacated.discard(m.dst)
    return p


def _mkdirs(path: Path, log: MoveLog, i: int) -> None:
    missing = []
    d = path.parent
    while not d.exists():
        missing.append(d)
        d = d.parent
    for d in reversed(missing):
        os.mkdir(d)
        log.write(op="mkdir", phase="done", seq=i, path=str(d))


def _link_unlink(src: str, dst: str) -> None:
    os.link(src, dst, follow_symlinks=False)
    os.unlink(src)


def apply(
    manifest: MoveManifest,
    log: MoveLog,
    execute: bool = False,
    limit: int | None = None,
    on_move: Callable[[int, str, str], None] | None = None,
) -> Plan:
    """Check, then (with ``execute``) apply the manifest. Stops at the first
    OS error after logging it. Returns the pre-execution ``Plan``."""
    p = check(manifest, log)
    if not execute:
        return p
    if not manifest.approved:
        raise MoveError("manifest is not approved (approved_by/approved_on unset)")
    if p.problems:
        raise MoveError(f"{len(p.problems)} problems; nothing moved")
    for i, src, dst in p.finish_log:
        log.write(op="move", phase="done", seq=i, src=src, dst=dst, note="resumed")
    for i, src, dst in p.finish_unlink:
        os.unlink(src)
        log.write(op="move", phase="done", seq=i, src=src, dst=dst, note="resumed")
    for n, (i, src, dst, kind) in enumerate(p.todo):
        if limit is not None and n >= limit:
            break
        try:
            _mkdirs(Path(dst), log, i)
            st = os.lstat(src)
            log.write(
                op="move",
                phase="start",
                seq=i,
                src=src,
                dst=dst,
                kind=kind,
                dev=st.st_dev,
                ino=st.st_ino,
            )
            _link_unlink(src, dst)
        except OSError as exc:
            log.write(op="move", phase="fail", seq=i, src=src, dst=dst, error=str(exc))
            raise MoveError(f"#{i} {src} -> {dst}: {exc}") from exc
        log.write(op="move", phase="done", seq=i, src=src, dst=dst, kind=kind)
        if on_move:
            on_move(i, src, dst)
    return p


@dataclass
class RollbackPlan:
    unmoves: list[tuple[str, str]] = field(default_factory=list)  # (src, dst)
    rmdirs: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def plan_rollback(log: MoveLog, run_id: str | None = None) -> RollbackPlan:
    """Undo steps, newest first. ``run_id`` limits it to one run."""
    entries = [e for e in log.read() if run_id is None or e["run_id"] == run_id]
    rp = RollbackPlan()
    undone = {
        (e["src"], e["dst"])
        for e in entries
        if e["op"] == "unmove" and e["phase"] == "done"
    }
    removed = {e["path"] for e in entries if e["op"] == "rmdir"}
    inos = {k: ino for k, (_, ino) in _state(entries).items()}
    seen: set = set()
    for e in reversed(entries):
        if e["op"] == "move" and e["phase"] in ("done", "start", "fail"):
            k = (e["src"], e["dst"])
            if k in undone or k in seen:
                continue
            seen.add(k)
            if e["phase"] != "done":
                # interrupted or failed: undo only if dst is the moved file
                src, dst, ino = e["src"], e["dst"], inos.get(k)
                if not os.path.lexists(dst):
                    continue  # never got linked
                ours = _same_file(src, dst) or _is_ino(dst, ino)
                if not ours and not (ino is None and e["phase"] == "start"):
                    continue  # dst belongs to something else; leave it
            rp.unmoves.append(k)
        elif e["op"] == "mkdir" and e["path"] not in removed:
            rp.rmdirs.append(e["path"])
    # check in undo order (a later unmove may refill an earlier src)
    vacated: set[str] = set()
    for src, dst in rp.unmoves:
        if not os.path.lexists(dst) and dst not in vacated:
            rp.problems.append(f"to undo, dst must exist: {dst}")
        if os.path.lexists(src) and src not in vacated and not _same_file(src, dst):
            rp.problems.append(f"to undo, src must be free: {src}")
        vacated.add(dst)
        vacated.discard(src)
    return rp


def rollback(
    log: MoveLog, execute: bool = False, run_id: str | None = None
) -> RollbackPlan:
    rp = plan_rollback(log, run_id)
    if not execute:
        return rp
    if rp.problems:
        raise MoveError(f"{len(rp.problems)} problems; nothing rolled back")
    for src, dst in rp.unmoves:
        log.write(op="unmove", phase="start", src=src, dst=dst)
        try:
            if os.path.lexists(src) and _same_file(src, dst):
                os.unlink(dst)  # interrupted between link and unlink
            else:
                Path(src).parent.mkdir(parents=True, exist_ok=True)
                _link_unlink(dst, src)
        except OSError as exc:
            log.write(op="unmove", phase="fail", src=src, dst=dst, error=str(exc))
            raise MoveError(f"undo {dst} -> {src}: {exc}") from exc
        log.write(op="unmove", phase="done", src=src, dst=dst)
    # deepest first; skip dirs that aren't empty (e.g. someone added files)
    for d in sorted(rp.rmdirs, key=lambda p: p.count("/"), reverse=True):
        try:
            os.rmdir(d)
            log.write(op="rmdir", phase="done", path=d)
        except OSError as exc:
            log.write(op="rmdir", phase="skip", path=d, error=str(exc))
    return rp
