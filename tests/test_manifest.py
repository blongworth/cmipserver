from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from whoi_cmip_reorg_code.manifest import (
    CatalogsManifest,
    CoreManifest,
    Move,
    MoveManifest,
    RepairAction,
    RepairManifest,
    dump,
    load,
)

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 12, 0)


def test_repo_manifests_valid():
    load(REPO / "manifests/core.yaml", CoreManifest)
    load(REPO / "manifests/catalogs.yaml", CatalogsManifest)


def test_core_expand():
    core = CoreManifest.model_validate(
        {
            "requirements": [
                {
                    "name": "a",
                    "facets": {"source_id": ["X", "Y"], "variable_id": ["tas", "pr"]},
                }
            ]
        }
    )
    assert len(core.requirements[0].expand()) == 4


@pytest.mark.parametrize(
    "req",
    [
        {"name": "a", "facets": {"model": "X"}},  # unknown facet
        {"name": "a", "facets": {}},
        {"name": "a", "facets": {"source_id": []}},
        {"name": "a", "facets": {"source_id": "X"}, "typo": 1},
    ],
)
def test_core_invalid(req):
    with pytest.raises(ValidationError):
        CoreManifest.model_validate({"requirements": [req]})


def test_core_duplicate_names():
    r = {"name": "a", "facets": {"source_id": "X"}}
    with pytest.raises(ValidationError, match="duplicate"):
        CoreManifest.model_validate({"requirements": [r, r]})


def test_repair_action_rules():
    RepairAction(action="report_only", reason="retracted")
    with pytest.raises(ValidationError, match="redownload needs"):
        RepairAction(action="redownload", reason="x", url="https://e/x.nc")
    with pytest.raises(ValidationError, match="local_path"):
        RepairAction(action="quarantine", reason="x")
    with pytest.raises(ValidationError, match="relative"):
        RepairAction(
            action="redownload",
            reason="x",
            url="u",
            checksum="c",
            drs_rel_path="/abs/x.nc",
        )


def _moves(*pairs):
    return [{"src": s, "dst": d, "kind": "reorg"} for s, d in pairs]


def test_move_manifest_rules():
    m = MoveManifest(
        created=NOW, created_by="t", description="d", moves=_moves(("/a/1", "/b/1"))
    )
    assert not m.approved
    m.approved_by, m.approved_on = "Brett", date(2026, 11, 6)
    assert m.approved
    with pytest.raises(ValidationError, match="same dst"):
        MoveManifest(
            created=NOW,
            created_by="t",
            description="d",
            moves=_moves(("/a/1", "/b/1"), ("/a/2", "/b/1")),
        )
    with pytest.raises(ValidationError, match="chained"):
        MoveManifest(
            created=NOW,
            created_by="t",
            description="d",
            moves=_moves(("/a/1", "/b/1"), ("/b/1", "/c/1")),
        )
    with pytest.raises(ValidationError, match="absolute"):
        Move(src="a/1", dst="/b/1", kind="reorg")
    with pytest.raises(ValidationError, match="absolute"):
        Move(src="/a/../1", dst="/b/1", kind="reorg")
    with pytest.raises(ValidationError, match="src == dst"):
        Move(src="/a/1", dst="/a/1", kind="reorg")


def test_roundtrip(tmp_path):
    r = RepairManifest(
        created=NOW,
        created_by="test",
        actions=[
            RepairAction(
                action="redownload",
                reason="size_mismatch",
                url="https://example.org/x.nc",
                checksum="ab",
                checksum_type="SHA256",
                drs_rel_path="CMIP/a/x.nc",
                evidence={"local_size": 1, "esgf_size": 2},
            )
        ],
    )
    p = dump(r, tmp_path / "r.yaml", header="generated\nreview me")
    assert p.read_text().startswith("# generated\n# review me\n")
    assert load(p, RepairManifest) == r


def test_catalogs_invalid():
    with pytest.raises(ValidationError):
        CatalogsManifest.model_validate(
            {
                "products": [
                    {"name": "x", "root": "rel", "builder": "cmip6", "output": "o"}
                ]
            }
        )
