import polars as pl

from whoi_cmip_reorg_code.manifest import RepairManifest, load
from whoi_cmip_reorg_code.repairs import plan

DRS = "CMIP/NCAR/CESM2/historical/r1i1p1f1"


def _by(actions, action):
    return {
        (a.reason, a.local_path or a.drs_rel_path): a
        for a in actions
        if a.action == action
    }


def test_messy_plan(audit, messy_store):
    m = load(audit / "repairs.yaml", RepairManifest)
    p = {k: str(v) for k, v in messy_store.paths.items()}
    redl = _by(m.actions, "redownload")
    quar = _by(m.actions, "quarantine")
    rep = [
        (a.reason, a.local_path, a.dataset_id)
        for a in m.actions
        if a.action == "report_only"
    ]

    # empty and truncated: on ESGF -> redownload + quarantine
    a = redl[("nc_empty+size_mismatch", p["empty"])]
    assert (
        a.drs_rel_path
        == f"{DRS}/Amon/ts/gn/v20190308/{messy_store.paths['empty'].name}"
    )
    assert a.url.startswith("https://data.example.invalid/")
    assert a.checksum_type == "SHA256"
    assert a.evidence["local_size"] == 0
    assert ("nc_truncated+size_mismatch", p["truncated"]) in redl
    assert ("nc_empty+size_mismatch", p["empty"]) in quar
    assert ("nc_truncated+size_mismatch", p["truncated"]) in quar
    # file missing from a held version
    gap_file = f"{DRS}/Amon/pr/gn/v20190308/pr_Amon_CESM2_historical_r1i1p1f1_gn_190001-194912.nc"
    assert ("missing_local", gap_file) in redl
    assert len(redl) == 3 and len(quar) == 2

    reasons = {r for r, _, _ in rep}
    # text file: not on ESGF -> report only
    assert ("corrupt_not_on_esgf:nc_not_hdf5_or_nc", p["text"]) in {
        (r, lp) for r, lp, _ in rep
    }
    assert ("attr_mismatch", p["mismatch"]) in {(r, lp) for r, lp, _ in rep}
    assert {
        "retracted",
        "superseded",
        "dataset_not_on_esgf",
        "time_gap",
        "time_overlap",
    } <= reasons
    sup = [d for r, _, d in rep if r == "superseded"]
    assert sup == ["CMIP6.CMIP.NCAR.CESM2.historical.r1i1p1f1.Amon.psl.gn.v20190308"]
    # good files produce nothing
    assert not any(a.local_path == p["good_0"] for a in m.actions)


def test_summary_markdown(audit):
    md = (audit / "repairs.md").read_text()
    assert md.startswith("# Repair plan repairs")
    assert "| redownload | missing_local | 1 |" in md


def test_retracted_is_not_redownloaded(audit):
    esgf = audit / "esgf"
    cmp_ = pl.read_parquet(esgf / "esgf_compare.parquet")
    # pretend the retracted tos files are also corrupt
    cmp_ = cmp_.with_columns(
        esgf_status=pl.when(pl.col("variable_id") == "tos")
        .then(pl.lit("size_mismatch"))
        .otherwise("esgf_status")
    )
    actions = plan(
        cmp_,
        pl.read_parquet(esgf / "esgf_files.parquet"),
        pl.read_parquet(esgf / "esgf_missing_local.parquet"),
    )
    tos = [a for a in actions if a.dataset_id and ".tos." in a.dataset_id]
    assert {a.action for a in tos} == {"report_only"}
    assert any(a.reason.startswith("corrupt_not_on_esgf") for a in tos)
