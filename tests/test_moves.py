import os
from datetime import UTC, date, datetime
from pathlib import Path

import apply_moves
import download_esgf
import plan_moves
import polars as pl
import pytest
import rollback_moves
from test_download import FakeServer

from whoi_cmip_reorg_code.manifest import Move, MoveManifest, dump, load
from whoi_cmip_reorg_code.movelog import MoveError, MoveLog, apply, rollback
from whoi_cmip_reorg_code.moves import plan_reorg
from whoi_cmip_reorg_code.ncinfo import check_file


def tree_state(root: Path) -> dict:
    """Every dir, file (bytes) and symlink (target) under root."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = Path(dirpath) / n
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir", None)
            else:
                out[rel] = ("file", p.read_bytes())
    return out


def manifest(moves, approved=True):
    return MoveManifest(
        created=datetime.now(UTC),
        created_by="test",
        description="test",
        approved_by="Brett" if approved else None,
        approved_on=date(2026, 11, 6) if approved else None,
        moves=[Move(src=str(s), dst=str(d), kind="reorg") for s, d in moves],
    )


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "store"
    (root / "a").mkdir(parents=True)
    for i in range(3):
        (root / "a" / f"f{i}.nc").write_bytes(bytes([i]) * (100 + i))
    (root / "a" / "link.nc").symlink_to("f0.nc")
    return root


def _moves(root):
    return [
        (root / "a" / "f0.nc", root / "b" / "c" / "f0.nc"),
        (root / "a" / "f1.nc", root / "b" / "f1.nc"),
        (root / "a" / "link.nc", root / "b" / "c" / "link.nc"),
    ]


def test_dry_run_is_noop(tree, tmp_path):
    before = tree_state(tree)
    log = MoveLog(tmp_path / "log.jsonl")
    p = apply(manifest(_moves(tree)), log)
    assert len(p.todo) == 3 and not p.problems
    assert tree_state(tree) == before
    assert not log.path.exists()


def test_execute_requires_approval(tree, tmp_path):
    with pytest.raises(MoveError, match="not approved"):
        apply(manifest(_moves(tree), approved=False), MoveLog(tmp_path / "l"), True)
    assert (tree / "a" / "f0.nc").exists()


def test_execute_and_rollback_roundtrip(tree, tmp_path):
    before = tree_state(tree)
    log = MoveLog(tmp_path / "log.jsonl")
    apply(manifest(_moves(tree)), log, execute=True)
    after = tree_state(tree)
    assert after["b/c/f0.nc"] == before["a/f0.nc"]
    assert after["b/c/link.nc"] == ("link", "f0.nc")  # symlink moved as-is
    assert "a/f0.nc" not in after
    # re-applying is a no-op (all done)
    p = apply(manifest(_moves(tree)), log, execute=True)
    assert p.done == 3 and not p.todo
    rollback(log, execute=True)
    assert tree_state(tree) == before  # dirs b/ and b/c/ removed too


def test_refuses_overwrite(tree, tmp_path):
    (tree / "b").mkdir()
    (tree / "b" / "f1.nc").write_bytes(b"other")
    log = MoveLog(tmp_path / "log.jsonl")
    with pytest.raises(MoveError, match="problems"):
        apply(manifest(_moves(tree)), log, execute=True)
    assert (tree / "a" / "f0.nc").exists()  # nothing moved
    assert (tree / "b" / "f1.nc").read_bytes() == b"other"


def test_missing_src(tree, tmp_path):
    m = manifest([(tree / "a" / "nope.nc", tree / "b" / "x.nc")])
    p = apply(m, MoveLog(tmp_path / "l"))
    assert p.problems == [f"#0 src missing: {tree}/a/nope.nc"]


def test_vacate_then_refill(tree, tmp_path):
    q = tmp_path / "quarantine" / "f0.nc"
    m = MoveManifest(
        created=datetime.now(UTC),
        created_by="t",
        description="t",
        approved_by="B",
        approved_on=date(2026, 11, 6),
        moves=[
            Move(src=str(tree / "a/f0.nc"), dst=str(q), kind="quarantine"),
            Move(src=str(tree / "a/f1.nc"), dst=str(tree / "a/f0.nc"), kind="install"),
        ],
    )
    before = tree_state(tree)
    log = MoveLog(tmp_path / "log.jsonl")
    apply(m, log, execute=True)
    assert (tree / "a/f0.nc").read_bytes() == before["a/f1.nc"][1]
    assert q.read_bytes() == before["a/f0.nc"][1]
    rollback(log, execute=True)
    assert tree_state(tree) == before


def test_resume_after_interruption(tree, tmp_path, monkeypatch):
    log = MoveLog(tmp_path / "log.jsonl")
    m = manifest(_moves(tree))
    real_unlink = os.unlink
    calls = {"n": 0}

    def flaky_unlink(p, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:  # die after linking the 2nd file, before unlink
            raise KeyboardInterrupt
        return real_unlink(p, *a, **k)

    monkeypatch.setattr(os, "unlink", flaky_unlink)
    with pytest.raises(KeyboardInterrupt):
        apply(m, log, execute=True)
    monkeypatch.setattr(os, "unlink", real_unlink)
    # f1 is now at both src and dst (same inode)
    assert (tree / "a/f1.nc").exists() and (tree / "b/f1.nc").exists()
    p = apply(m, log, execute=True)
    assert len(p.finish_unlink) == 1 and p.done == 1 and len(p.todo) == 1
    assert not (tree / "a/f1.nc").exists()
    assert sorted(x.name for x in (tree / "a").iterdir()) == ["f2.nc"]


def test_rollback_dry_run_and_partial(tree, tmp_path):
    before = tree_state(tree)
    log = MoveLog(tmp_path / "log.jsonl")
    apply(manifest(_moves(tree)), log, execute=True, limit=1)
    assert (tree / "b/c/f0.nc").exists() and (tree / "a/f1.nc").exists()
    rp = rollback(log)  # dry run
    assert len(rp.unmoves) == 1 and (tree / "b/c/f0.nc").exists()
    rollback(log, execute=True)
    assert tree_state(tree) == before


def test_cli_dry_run_and_execute(tree, tmp_path, capsys):
    mpath = dump(manifest(_moves(tree), approved=False), tmp_path / "m.yaml")
    log = tmp_path / "log.jsonl"
    assert apply_moves.main(["--manifest", str(mpath), "--log", str(log)]) == 0
    assert "would reorg" in capsys.readouterr().out
    assert (
        apply_moves.main(["--manifest", str(mpath), "--log", str(log), "--execute"])
        == 1
    )  # unapproved
    m = load(mpath, MoveManifest)
    m.approved_by, m.approved_on = "Brett", date(2026, 11, 6)
    dump(m, mpath)
    assert (
        apply_moves.main(["--manifest", str(mpath), "--log", str(log), "--execute"])
        == 0
    )
    assert (tree / "b/f1.nc").exists()
    assert rollback_moves.main(["--log", str(log), "--execute"]) == 0
    assert (tree / "a/f1.nc").exists()


def test_repair_flow_end_to_end(audit, fake_esgf, messy_store, messy_copy, tmp_path):
    """download -> plan_moves repairs -> approve -> apply -> rollback."""
    root = messy_copy.root
    # repairs.yaml points at the shared messy_store; retarget local paths
    from whoi_cmip_reorg_code.manifest import RepairManifest

    rep = load(audit / "repairs.yaml", RepairManifest)
    shared = str(messy_store.root)
    for a in rep.actions:
        if a.local_path:
            a.local_path = a.local_path.replace(shared, str(root))
    rpath = dump(rep, tmp_path / "repairs.yaml")

    stage, quar = tmp_path / "staging", tmp_path / "quarantine"
    assert (
        download_esgf.main(
            ["--repairs", str(rpath), "--staging-root", str(stage), "--execute"],
            opener=FakeServer(fake_esgf.payloads),
        )
        == 0
    )
    before = tree_state(root)
    mpath = tmp_path / "moves.yaml"
    plan_moves.main(
        [
            "repairs",
            "--repairs",
            str(rpath),
            "--staging-root",
            str(stage),
            "--quarantine-root",
            str(quar),
            "--store-root",
            str(root),
            "--drs-root",
            str(root),
            "--verify",
            "--out",
            str(mpath),
        ]
    )
    m = load(mpath, MoveManifest)
    assert not m.approved
    kinds = [mv.kind for mv in m.moves]
    assert kinds.count("quarantine") == 2 and kinds.count("install") == 3
    report = pl.read_parquet(mpath.with_suffix(".parquet"))
    assert set(report["status"]) == {"planned"}

    m.approved_by, m.approved_on = "Brett", date(2026, 11, 6)
    log = MoveLog(tmp_path / "moves.jsonl")
    apply(m, log, execute=True)
    for key in ("empty", "truncated"):
        p = messy_copy.paths[key]
        assert check_file(p)["nc_status"] == "ok"
        q = quar / p.relative_to(root)
        assert q.exists()  # original kept, not deleted
    assert (
        check_file(
            messy_copy.paths["gap_0"].with_name(
                "pr_Amon_CESM2_historical_r1i1p1f1_gn_190001-194912.nc"
            )
        )["nc_status"]
        == "ok"
    )
    rollback(log, execute=True)
    assert tree_state(root) == before


def test_plan_reorg(tmp_path):
    from conftest import write_nc

    from whoi_cmip_reorg_code.scan import scan

    root = tmp_path / "cmip6"
    fn = "tas_Amon_CESM2_historical_r1i1p1f1_gn_185001-185012.nc"
    a = write_nc(root / "old" / "v20190308" / fn, "tas", "Amon", "185001-185012")
    b = write_nc(root / "old2" / "v20190308" / fn, "tas", "Amon", "185001-185012")
    c = write_nc(
        root / "noversion" / fn.replace("tas_", "pr_"), "pr", "Amon", "185001-185012"
    )
    scan(root, tmp_path / "scan")
    h = pl.read_parquet(tmp_path / "scan" / "holdings_all.parquet")
    nci = pl.DataFrame(
        {
            "path": [str(a), str(b)],
            "nc_status": ["ok", "ok"],
            "attr_activity_id": ["CMIP", "CMIP"],
            "attr_institution_id": ["NCAR", "NCAR"],
        }
    )
    moves, report = plan_reorg(h, str(root), nci)
    st = dict(zip(report["src"], report["status"]))
    # a and b both map to the same DRS path: neither is planned
    assert st[str(a)] == st[str(b)] == "duplicate_dst"
    assert st[str(c)] == "unmappable"
    assert moves == []
    moves, report = plan_reorg(h.filter(pl.col("path") != str(b)), str(root), nci)
    (mv,) = moves
    assert (
        mv.dst
        == f"{root}/CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas/gn/v20190308/{fn}"
    )
