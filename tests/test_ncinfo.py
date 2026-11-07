import check_nc
import polars as pl
import pytest

from whoi_cmip_reorg_code.ncinfo import NCINFO_SCHEMA, check_file, hdf5_eof


@pytest.mark.parametrize(
    "key, status",
    [
        ("good_0", "ok"),
        ("fx", "ok"),
        ("flat", "ok"),
        ("mismatch", "ok"),
        ("empty", "empty"),
        ("truncated", "truncated"),
        ("text", "not_hdf5_or_nc"),
    ],
)
@pytest.mark.parametrize("deep", [False, True])
def test_status(messy_store, key, status, deep):
    row = check_file(messy_store.paths[key], deep=deep)
    assert row["nc_status"] == status, row["nc_error"]
    assert set(row) == set(NCINFO_SCHEMA)


def test_good_file_metadata(messy_store):
    row = check_file(messy_store.paths["good_0"])
    assert row["nc_format"] == "hdf5"
    assert row["hdf5_eof"] == messy_store.paths["good_0"].stat().st_size
    assert row["attr_variable_id"] == "tas"
    assert row["attr_tracking_id"].startswith("hdl:21.14100/")
    assert row["main_var"] == "tas"
    assert row["main_var_units"] == "K"
    assert row["n_time"] == 50 * 12
    assert row["time_calendar"] == "noleap"
    assert row["time_first"].startswith("1850-01")
    assert row["time_last"].startswith("1899-12")
    assert row["attr_mismatch"] is None


def test_fixed_field_has_no_time(messy_store):
    row = check_file(messy_store.paths["fx"])
    assert row["main_var"] == "areacella"
    assert row["n_time"] is None


def test_attr_mismatch(messy_store):
    row = check_file(messy_store.paths["mismatch"])
    assert row["attr_mismatch"] == "variable_id"
    assert "filename=huss attr=hurs" in row["attr_mismatch_detail"]
    assert row["main_var"] == "hurs"


def test_truncated_reports_eof(messy_store):
    row = check_file(messy_store.paths["truncated"])
    assert row["hdf5_eof"] > row["size_bytes"]


def test_missing_file_is_read_error(tmp_path):
    row = check_file(tmp_path / "gone.nc")
    assert row["nc_status"] == "read_error"


def test_hdf5_eof_with_user_block(tmp_path, messy_store):
    # HDF5 files may start with a 512-byte user block; EOF is then offset.
    src = messy_store.paths["good_0"].read_bytes()
    p = tmp_path / "ub.nc"
    # Not a valid HDF5 file with user block (addresses aren't rewritten), but
    # the signature search must find the superblock at 512.
    p.write_bytes(b"\0" * 512 + src)
    fmt, _ = hdf5_eof(p)
    assert fmt == "hdf5"


def test_classic_format(tmp_path):
    import netCDF4

    p = tmp_path / "tas_Amon_CESM2_historical_r1i1p1f1_gn_185001-185012.nc"
    with netCDF4.Dataset(p, "w", format="NETCDF3_CLASSIC") as ds:
        ds.createDimension("time", None)
        v = ds.createVariable("tas", "f4", ("time",))
        v[:] = [1.0, 2.0]
        ds.variable_id = "tas"
    row = check_file(p, deep=True)
    assert (row["nc_status"], row["nc_format"]) == ("ok", "classic")


def test_cli(messy_store, messy_scan, tmp_path):
    out = tmp_path / "ncinfo"
    assert (
        check_nc.main(["--scan", str(messy_scan), "--out", str(out), "--shard", "all"])
        == 0
    )
    df = pl.read_parquet(out / "ncinfo_all.parquet")
    # symlinks and non-.nc files are not checked
    assert messy_store.rel("broken_link") not in df["rel_path"].to_list()
    assert messy_store.rel("readme") not in df["rel_path"].to_list()
    st = dict(zip(df["rel_path"], df["nc_status"]))
    assert st[messy_store.rel("empty")] == "empty"
    assert st[messy_store.rel("truncated")] == "truncated"
    assert st[messy_store.rel("text")] == "not_hdf5_or_nc"
    assert st[messy_store.rel("good_1")] == "ok"
    assert df.schema == pl.Schema(NCINFO_SCHEMA)


def test_cli_prefix_and_limit(messy_scan, tmp_path):
    out = tmp_path / "ncinfo"
    prefix = "CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas"
    check_nc.main(
        [
            "--scan",
            str(messy_scan),
            "--out",
            str(out),
            "--shard-index",
            "0",
            "--prefix",
            prefix,
            "--limit",
            "2",
        ]
    )
    (f,) = out.glob("ncinfo_*.parquet")
    df = pl.read_parquet(f)
    assert df.height == 2
    assert all(r.startswith(prefix + "/") for r in df["rel_path"])
