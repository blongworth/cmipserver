import pytest

from whoi_cmip_reorg_code.drs import drs_check, parse_filename, parse_path_facets

FN = "tas_Amon_CESM2_historical_r1i1p1f1_gn_185001-201412.nc"
DRS = "CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas/gn/v20190308/" + FN


def test_parse_filename_with_time_range():
    d = parse_filename(FN)
    assert d == {
        "variable_id": "tas",
        "table_id": "Amon",
        "source_id": "CESM2",
        "experiment_id": "historical",
        "member_id": "r1i1p1f1",
        "grid_label": "gn",
        "time_start": "185001",
        "time_end": "201412",
        "climatology": False,
    }


def test_parse_filename_fixed_field():
    d = parse_filename("areacella_fx_MPI-ESM1-2-HR_piControl_r1i1p1f1_gn.nc")
    assert d["variable_id"] == "areacella"
    assert d["source_id"] == "MPI-ESM1-2-HR"
    assert d["time_start"] is None and d["time_end"] is None


@pytest.mark.parametrize(
    "name,member,grid,start,end,clim",
    [
        (
            "tos_Omon_CanESM5_dcppA-hindcast_s1960-r1i1p2f1_gr1_196101-197012.nc",
            "s1960-r1i1p2f1",
            "gr1",
            "196101",
            "197012",
            False,
        ),
        (
            "pr_day_EC-Earth3_ssp585_r10i1p1f1_gr_20150101-20151231.nc",
            "r10i1p1f1",
            "gr",
            "20150101",
            "20151231",
            False,
        ),
        (
            "tas_Amon_CESM2_historical_r1i1p1f1_gn_198101-201012-clim.nc",
            "r1i1p1f1",
            "gn",
            "198101",
            "201012",
            True,
        ),
    ],
)
def test_parse_filename_variants(name, member, grid, start, end, clim):
    d = parse_filename(name)
    assert d is not None
    assert (d["member_id"], d["grid_label"]) == (member, grid)
    assert (d["time_start"], d["time_end"], d["climatology"]) == (start, end, clim)


@pytest.mark.parametrize(
    "name",
    [
        "tas_Amon_CESM2_historical_r1i1p1f1_gn_185001-201412.nc4",
        "tas_Amon_CESM2_historical_r1i1p1_gn_185001-201412.nc",  # CMIP5 member
        "tas_Amon_CESM2_historical_r1i1p1f1_185001-201412.nc",  # no grid
        "tas_Amon_CESM2_historical_r1i1p1f1_gn_185001.nc",  # half time range
        "tas.nc",
        "README.txt",
    ],
)
def test_parse_filename_invalid(name):
    assert parse_filename(name) is None


def test_path_facets_full_drs():
    pf = parse_path_facets(DRS, parse_filename(FN))
    assert pf == {
        "version": "v20190308",
        "activity_id": "CMIP",
        "institution_id": "NCAR",
    }


def test_path_facets_version_only():
    pf = parse_path_facets("some/odd/v20200101/tas/" + FN, parse_filename(FN))
    assert pf == {"version": "v20200101", "activity_id": None, "institution_id": None}


def test_path_facets_none():
    assert parse_path_facets("flat/" + FN)["version"] is None


def test_path_facets_mismatch_withholds_activity():
    bad = DRS.replace("/tas/gn/", "/pr/gn/")
    pf = parse_path_facets(bad, parse_filename(FN))
    assert pf["version"] == "v20190308"
    assert pf["activity_id"] is None


@pytest.mark.parametrize(
    "rel,status,depth",
    [
        ("README.txt", "not_nc", 0),
        ("CMIP/x/bad_name.nc", "bad_filename", 2),
        ("flat/" + FN, "wrong_depth", 1),
        ("extra/" + DRS, "wrong_depth", 10),
        ("CMIP6/" + DRS, "ok", 9),
        ("data/CMIP6/" + DRS, "ok", 9),
        (DRS.replace("v20190308", "latest"), "bad_version_dir", 9),
        (DRS.replace("/Amon/", "/Omon/"), "path_filename_mismatch", 9),
        (DRS, "ok", 9),
        (DRS.replace("v20190308", "1"), "ok", 9),
    ],
)
def test_drs_check(rel, status, depth):
    c = drs_check(rel)
    assert c["drs_status"] == status
    assert c["drs_depth"] == depth


def test_expected_rel_path():
    assert drs_check(DRS)["expected_rel_path"] == DRS
    assert (
        drs_check("flat/v20190308/" + FN)["expected_rel_path"]
        == "{activity_id}/{institution_id}/CESM2/historical/r1i1p1f1/Amon/tas/gn/"
        "v20190308/" + FN
    )
    assert drs_check("flat/" + FN)["expected_rel_path"] is None


def test_numeric_version_is_ok_and_noted():
    rel = DRS.replace("v20190308", "1")
    c = drs_check(rel)
    assert c["drs_status"] == "ok"
    assert c["version_scheme"] == "numeric"
    assert c["expected_rel_path"] == rel  # unchanged: nothing to reorganize
    assert drs_check(DRS)["version_scheme"] == "date"
    assert drs_check("README.txt")["version_scheme"] is None
    assert drs_check(DRS.replace("v20190308", "latest"))["version_scheme"] is None


def test_bare_date_version_is_its_own_scheme():
    rel = DRS.replace("v20190308", "20190308")
    c = drs_check(rel)
    assert c["drs_status"] == "ok"
    assert c["version_scheme"] == "bare_date"
    assert drs_check(DRS.replace("v20190308", "12"))["version_scheme"] == "numeric"
