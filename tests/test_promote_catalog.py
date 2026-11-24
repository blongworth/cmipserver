import json
import os

import promote_catalog as pc
import pytest

NAME = "whoi_x"
OK_REPORT = [
    {"check": c, "ok": True, "detail": ""}
    for c in ("columns", "types", "unique_paths", "intake_open", "to_dataset_dict")
]


def write_catalog(d, n_datasets, tag="a"):
    d.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"ds{i},v1,/p/{tag}{i}.nc" for i in range(n_datasets))
    (d / f"{NAME}.csv").write_text(f"master_id,version,path\n{rows}\n")
    (d / f"{NAME}_invalid.parquet").write_bytes(b"PAR1" + tag.encode())
    (d / f"{NAME}.json").write_text(
        json.dumps({"catalog_file": f"{NAME}.csv", "tag": tag})
    )


def write_report(path, results=OK_REPORT, older_than=None):
    path.write_text(json.dumps(results))
    if older_than is not None:
        t = older_than.stat().st_mtime - 60
        os.utime(path, (t, t))
    return path


@pytest.fixture
def dirs(tmp_path):
    nxt, live = tmp_path / "next", tmp_path / "live"
    write_catalog(nxt, 100, "new")
    write_catalog(live, 100, "old")
    return nxt, live, write_report(tmp_path / "check.json")


def test_dry_run_changes_nothing(dirs):
    nxt, live, report = dirs
    before = (live / f"{NAME}.json").read_text()
    log = pc.promote(NAME, report, nxt, live)
    assert "dry run" in log[-1]
    assert (live / f"{NAME}.json").read_text() == before


def test_execute_copies_json_last_and_leaves_no_temp(dirs, monkeypatch):
    nxt, live, report = dirs
    order = []
    real = pc.copy_in
    monkeypatch.setattr(pc, "copy_in", lambda s, d: (order.append(d.name), real(s, d)))
    pc.promote(NAME, report, nxt, live, execute=True)
    assert order == [f"{NAME}.csv", f"{NAME}_invalid.parquet", f"{NAME}.json"]
    for suffix in pc.SUFFIXES:
        assert (live / f"{NAME}{suffix}").read_bytes() == (
            nxt / f"{NAME}{suffix}"
        ).read_bytes()
    assert not [p for p in live.iterdir() if p.name.startswith(".")]
    # second run: identical, nothing to do
    assert "already identical" in pc.promote(NAME, report, nxt, live, execute=True)[-1]


def test_new_catalog_without_live_copy(tmp_path):
    nxt, live = tmp_path / "next", tmp_path / "live"
    write_catalog(nxt, 3)
    live.mkdir()
    log = pc.promote(NAME, write_report(tmp_path / "r.json"), nxt, live, execute=True)
    assert "new catalog" in log[0]
    assert (live / f"{NAME}.json").exists()


@pytest.mark.parametrize(
    "report_body, match",
    [
        (
            [{**OK_REPORT[0], "ok": False, "detail": "bad cols"}, *OK_REPORT[1:]],
            "bad cols",
        ),
        (OK_REPORT[:3], "missing checks"),
        ([], "not a check_catalog"),
    ],
)
def test_refuses_bad_reports(dirs, report_body, match):
    nxt, live, report = dirs
    write_report(report, report_body)
    with pytest.raises(pc.Refused, match=match):
        pc.promote(NAME, report, nxt, live, execute=True)
    assert "old" in (live / f"{NAME}.json").read_text()


def test_refuses_missing_or_stale_report(dirs):
    nxt, live, report = dirs
    with pytest.raises(pc.Refused, match="no check report"):
        pc.promote(NAME, report.with_name("nope.json"), nxt, live)
    write_report(report, older_than=nxt / f"{NAME}.csv")
    with pytest.raises(pc.Refused, match="older than"):
        pc.promote(NAME, report, nxt, live)


def test_refuses_dataset_drop_unless_allowed(tmp_path):
    nxt, live = tmp_path / "next", tmp_path / "live"
    write_catalog(live, 100, "old")
    write_catalog(nxt, 90, "new")
    report = write_report(tmp_path / "r.json")
    with pytest.raises(pc.Refused, match="100 -> 90 datasets"):
        pc.promote(NAME, report, nxt, live)
    assert (
        pc.main(
            [
                "--name",
                NAME,
                "--report",
                str(report),
                "--next",
                str(nxt),
                "--live",
                str(live),
            ]
        )
        == 1
    )
    assert (
        pc.main(
            [
                "--name",
                NAME,
                "--report",
                str(report),
                "--next",
                str(nxt),
                "--live",
                str(live),
                "--allow-drop",
            ]
        )
        == 0
    )


def test_refuses_missing_files_and_absolute_catalog_file(dirs):
    nxt, live, report = dirs
    (nxt / f"{NAME}.json").write_text(json.dumps({"catalog_file": f"/abs/{NAME}.csv"}))
    write_report(report)
    with pytest.raises(pc.Refused, match="expected whoi_x.csv"):
        pc.promote(NAME, report, nxt, live)
    (nxt / f"{NAME}_invalid.parquet").unlink()
    with pytest.raises(pc.Refused, match="missing in"):
        pc.promote(NAME, report, nxt, live)
