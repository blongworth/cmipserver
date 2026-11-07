import find_gaps
import polars as pl
import pytest

from whoi_cmip_reorg_code import gaps
from whoi_cmip_reorg_code.gaps import core_gaps, partial_coverage, successors
from whoi_cmip_reorg_code.manifest import CoreManifest


def holdings(names, dir_="CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas/gn/v1"):
    """Minimal holdings rows built from filenames (via the real scanner logic)."""
    from whoi_cmip_reorg_code import drs

    rows = []
    for n in names:
        fn = drs.parse_filename(n) or {}
        rows.append(
            {
                "rel_path": f"{dir_}/{n}",
                "name": n,
                "file_type": "file",
                "facet_parse_ok": bool(fn),
                **{
                    k: fn.get(k)
                    for k in (
                        "source_id",
                        "experiment_id",
                        "member_id",
                        "table_id",
                        "variable_id",
                        "grid_label",
                        "time_start",
                        "time_end",
                    )
                },
                "climatology": fn.get("climatology", False),
                "version": "v1",
            }
        )
    return pl.LazyFrame(rows)


def f(tr, var="tas", table="Amon"):
    return (
        f"{var}_{table}_CESM2_historical_r1i1p1f1_gn_{tr}.nc"
        if tr
        else (f"{var}_{table}_CESM2_historical_r1i1p1f1_gn.nc")
    )


@pytest.mark.parametrize(
    "label, table, expected",
    [
        ("1899", "Oyr", {"1900"}),
        ("189912", "Amon", {"190001"}),
        ("189911", "Amon", {"189912"}),
        ("18501231", "day", {"18510101"}),
        # leap year: standard/all_leap -> Feb 29, noleap -> Mar 1
        ("18520228", "day", {"18520229", "18520301"}),
        # 1900 is not a leap year in the standard calendar
        ("19000228", "day", {"19000229", "19000301"}),
        ("19000229", "day", {"19000301", "19000230"}),  # all_leap / 360_day
        ("18500230", "day", {"18500301"}),  # 360_day only
        ("1850123118", "6hrPlev", {"1851010100"}),
        ("185012312230", "3hr", {"185101010130"}),
        ("185001010000", "E1hr", {"185001010100"}),
        ("185013", "Amon", None),
    ],
)
def test_successors(label, table, expected):
    assert successors(label, table) == expected


@pytest.mark.parametrize(
    "ranges, status, n_gaps, n_overlaps, end",
    [
        (["185001-189912", "190001-201412"], "ok", 0, 0, "201412"),
        (["185001-189912", "195001-201412"], "gap", 1, 0, "201412"),
        (["185001-189912", "189001-194912"], "overlap", 0, 1, "194912"),
    ],
)
def test_monthly(ranges, status, n_gaps, n_overlaps, end):
    ds, issues = gaps.find_gaps(holdings([f(r) for r in ranges]))
    (row,) = ds.to_dicts()
    assert (row["gap_status"], row["n_gaps"], row["n_overlaps"]) == (
        status,
        n_gaps,
        n_overlaps,
    )
    assert issues.height == n_gaps + n_overlaps
    assert (row["time_start"], row["time_end"]) == ("185001", end)


def test_gap_issue_detail():
    _, issues = gaps.find_gaps(holdings([f("185001-189912"), f("195001-201412")]))
    (i,) = issues.to_dicts()
    assert (i["kind"], i["prev_end"], i["next_start"], i["expected_start"]) == (
        "gap",
        "189912",
        "195001",
        "190001",
    )


def test_daily_leap_years():
    # noleap model: Feb 28 -> Mar 1 in a leap year is contiguous
    names = [
        f("18500101-18520228", "tos", "Oday"),
        f("18520301-18601231", "tos", "Oday"),
    ]
    ds, _ = gaps.find_gaps(holdings(names))
    assert ds["gap_status"].to_list() == ["ok"]
    # but skipping a whole day is a gap in every calendar
    names = [
        f("18500101-18520228", "tos", "Oday"),
        f("18520302-18601231", "tos", "Oday"),
    ]
    ds, _ = gaps.find_gaps(holdings(names))
    assert ds["gap_status"].to_list() == ["gap"]


def test_duplicate_ranges_in_one_dir():
    # same range twice: e.g. one file renamed with a suffix that still parses
    a = f("185001-201412")
    lf = holdings([a]).collect()
    lf = pl.concat([lf, lf.with_columns(name=pl.lit(a + ".copy"))]).lazy()
    ds, issues = gaps.find_gaps(lf)
    assert ds["gap_status"].to_list() == ["duplicate"]
    assert issues["kind"].to_list() == ["duplicate"]


def test_fixed_and_clim():
    ds, _ = gaps.find_gaps(holdings([f(None, "areacella", "fx")], dir_="fx"))
    assert ds["gap_status"].to_list() == ["fixed"]
    ds, _ = gaps.find_gaps(holdings([f("198101-201012-clim")]))
    assert ds["gap_status"].to_list() == ["clim"]


def test_mixed_time_format():
    ds, _ = gaps.find_gaps(holdings([f("185001-189912"), f("19000101-19491231")]))
    assert ds["gap_status"].to_list() == ["mixed_time_format"]


def test_dirs_are_separate_datasets():
    a = holdings([f("185001-189912")], dir_="a").collect()
    b = holdings([f("195001-201412")], dir_="b").collect()
    ds, _ = gaps.find_gaps(pl.concat([a, b]).lazy())
    assert ds["gap_status"].to_list() == ["ok", "ok"]


def test_messy_store(messy_store, messy_scan):
    lf = pl.scan_parquet(messy_scan / "holdings_all.parquet")
    ds, issues = gaps.find_gaps(lf)
    st = {r["variable_id"]: r["gap_status"] for r in ds.to_dicts() if r["dir"] != "."}
    assert st["tas"] == "ok"
    assert st["pr"] == "gap"
    assert st["tos"] == "overlap"
    assert st["areacella"] == "fixed"
    # two versions are two datasets, each fine on its own
    psl = ds.filter(variable_id="psl")
    assert psl.height == 2 and set(psl["gap_status"]) == {"ok"}
    assert set(issues["kind"]) == {"gap", "overlap"}


def test_partial_coverage():
    ds = pl.DataFrame(
        {
            "source_id": ["S"] * 5,
            "experiment_id": ["hist", "hist", "ssp", "hist", "ssp"],
            "member_id": ["r1", "r1", "r1", "r2", "r2"],
            "table_id": ["Amon"] * 5,
            "variable_id": ["tas", "pr", "tas", "tas", "tas"],
        }
    )
    pc = partial_coverage(ds)
    rows = {(r["kind"], r["scope"], r["item"]): r for r in pc.to_dicts()}
    # r1 has pr only for hist
    assert rows[("missing_experiment", "r1", "Amon.pr")]["missing_in"] == "ssp"
    # in hist, pr exists for r1 but not r2
    assert rows[("missing_member", "hist", "Amon.pr")]["missing_in"] == "r2"
    assert len(rows) == 2


def test_core_gaps(messy_scan):
    core = CoreManifest.model_validate(
        {
            "requirements": [
                {
                    "name": "atm",
                    "facets": {
                        "source_id": "CESM2",
                        "table_id": "Amon",
                        "variable_id": ["tas", "pr", "clt"],
                    },
                }
            ]
        }
    )
    cg = core_gaps(pl.scan_parquet(messy_scan / "holdings_all.parquet"), core)
    got = {r["selector"]: r["present"] for r in cg.to_dicts()}
    assert got == {
        "source_id=CESM2;table_id=Amon;variable_id=tas": True,
        "source_id=CESM2;table_id=Amon;variable_id=pr": True,
        "source_id=CESM2;table_id=Amon;variable_id=clt": False,
    }


def test_cli(messy_scan, tmp_path):
    core = tmp_path / "core.yaml"
    core.write_text(
        "requirements:\n  - name: x\n    facets: {source_id: CESM2, variable_id: tas}\n"
    )
    out = tmp_path / "gaps"
    assert (
        find_gaps.main(
            ["--scan", str(messy_scan), "--out", str(out), "--core", str(core)]
        )
        == 0
    )
    for n in ("gap_datasets", "gap_issues", "partial_coverage", "core_gaps"):
        assert (out / f"{n}.parquet").exists()
