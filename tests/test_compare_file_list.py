import compare_file_list
import polars as pl

OK = "CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas/gn/1/tas_Amon_CESM2_historical_r1i1p1f1_gn_185001-201412.nc"
NEW = OK.replace("tas", "pr")
GONE = OK.replace("historical", "ssp")


def _scan(tmp_path):
    d = tmp_path / "scan"
    d.mkdir()
    pl.DataFrame(
        {
            "rel_path": [OK, NEW, "CMIP/NCAR/notes.json"],
            "size_bytes": [1, 2, 3],
            "file_type": ["file"] * 3,
            "suffix": [".nc", ".nc", ".json"],
            "drs_status": ["ok", "bad_filename", "not_nc"],
        }
    ).write_parquet(d / "holdings_CMIP.parquet")
    return d


def test_compare_and_cli(tmp_path, capsys):
    lst = tmp_path / "list.txt"
    lst.write_text(f"{OK}\n{GONE}\n\n")
    r = compare_file_list.compare(_scan(tmp_path), lst)
    assert r["only_in_scan"]["rel_path"].to_list() == [NEW]
    assert r["only_in_scan"]["drs_status"].to_list() == ["bad_filename"]
    assert r["only_in_list"]["rel_path"].to_list() == [GONE]
    assert r["both"].height == 1  # .json is ignored

    out = tmp_path / "out"
    assert (
        compare_file_list.main(
            ["--scan", str(tmp_path / "scan"), "--list", str(lst), "--out", str(out)]
        )
        == 0
    )
    assert "only in scan: 1" in capsys.readouterr().out
    assert (out / "only_in_scan.parquet").exists()
