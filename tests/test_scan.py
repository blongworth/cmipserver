import os

import polars as pl
import pytest
import scan_holdings
import summarize_scan

from whoi_cmip_reorg_code.scan import ROOT_FILES_SHARD, list_shards, scan

FN = "tas_Amon_CESM2_historical_r1i1p1f1_gn_185001-201412.nc"
DRS_DIR = "CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas/gn/v20190308"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "cmip6"
    good = root / DRS_DIR
    good.mkdir(parents=True)
    (good / FN).write_bytes(b"x" * 1000)
    # fixed field, flat layout
    flat = root / "misc"
    flat.mkdir()
    (flat / "areacella_fx_CESM2_historical_r1i1p1f1_gn.nc").write_bytes(b"y" * 10)
    (flat / "notes.txt").write_text("hello")
    (flat / "weird_name.nc").write_bytes(b"z")
    # hard link to the good file
    os.link(good / FN, flat / ("hl_" + FN))
    # good and broken symlinks
    (flat / "link.nc").symlink_to(good / FN)
    (flat / "broken.nc").symlink_to(root / "does_not_exist.nc")
    # symlinked directory: must not be followed
    (flat / "linkdir").symlink_to(root / "CMIP")
    # unreadable directory
    locked = root / "locked"
    locked.mkdir()
    (locked / "hidden.nc").write_bytes(b"h")
    locked.chmod(0)
    # file at root
    (root / "README").write_text("top")
    yield root
    locked.chmod(0o755)


def _rows(df, name):
    return df.filter(pl.col("name") == name).to_dicts()


def test_list_shards(tree):
    assert list_shards(tree) == ["CMIP", "locked", "misc", ROOT_FILES_SHARD]


def test_scan_all(tree, tmp_path):
    res = scan(tree, tmp_path / "out", batch_rows=2)  # small batches: many parts
    df = pl.read_parquet(res["holdings"])
    err = pl.read_parquet(res["errors"])
    assert not (tmp_path / "out" / ".parts_holdings_all").exists()

    names = set(df["name"])
    assert names == {
        FN,
        "areacella_fx_CESM2_historical_r1i1p1f1_gn.nc",
        "notes.txt",
        "weird_name.nc",
        "hl_" + FN,
        "link.nc",
        "broken.nc",
        "linkdir",
        "README",
    }
    # symlinked dir not followed: FN appears only via its real path + hard link
    assert df.filter(pl.col("name") == FN).height == 1
    assert df.schema["mtime"] == pl.Datetime("ns", "UTC")

    (good,) = _rows(df, FN)
    assert good["drs_status"] == "ok"
    assert good["facet_parse_ok"]
    assert good["activity_id"] == "CMIP" and good["institution_id"] == "NCAR"
    assert good["version"] == "v20190308"
    assert good["size_bytes"] == 1000
    assert good["nlink"] == 2
    assert good["top_dir"] == "CMIP"
    st = (tree / DRS_DIR / FN).stat()
    assert (good["uid"], good["gid"]) == (st.st_uid, st.st_gid)

    (hl,) = _rows(df, "hl_" + FN)
    assert hl["inode"] == good["inode"] and hl["dev"] == good["dev"]
    assert hl["drs_status"] == "bad_filename"

    (fx,) = _rows(df, "areacella_fx_CESM2_historical_r1i1p1f1_gn.nc")
    assert fx["facet_parse_ok"] and fx["time_start"] is None
    assert fx["drs_status"] == "wrong_depth"

    (lnk,) = _rows(df, "link.nc")
    assert lnk["is_symlink"] and lnk["link_resolves"]
    assert lnk["link_target"] == str(tree / DRS_DIR / FN)
    (brk,) = _rows(df, "broken.nc")
    assert brk["is_symlink"] and brk["link_resolves"] is False
    (ld,) = _rows(df, "linkdir")
    assert ld["is_symlink"] and ld["drs_status"] == "not_nc"

    assert _rows(df, "notes.txt")[0]["drs_status"] == "not_nc"
    assert _rows(df, "weird_name.nc")[0]["drs_status"] == "bad_filename"
    assert _rows(df, "README")[0]["top_dir"] == "."
    regular = df.filter(~pl.col("is_symlink"))
    assert regular["link_target"].is_null().all()

    if os.geteuid() != 0:
        assert err.select("rel_path", "op").to_dicts() == [
            {"rel_path": "locked", "op": "scandir"}
        ]
        assert "hidden.nc" not in names


def test_scan_shards_cover_tree(tree, tmp_path):
    out = tmp_path / "out"
    full = pl.read_parquet(scan(tree, tmp_path / "full")["holdings"])
    for s in list_shards(tree):
        scan(tree, out, shard=s)
    parts = pl.read_parquet(sorted(out.glob("holdings_*.parquet")))
    assert sorted(parts["rel_path"]) == sorted(full["rel_path"])
    root_only = pl.read_parquet(out / f"holdings_{ROOT_FILES_SHARD}.parquet")
    assert root_only["rel_path"].to_list() == ["README"]


def test_scan_shard_not_a_dir(tree, tmp_path):
    with pytest.raises(ValueError):
        scan(tree, tmp_path / "out", shard="README")


def test_cli_and_summary(tree, tmp_path, monkeypatch, capsys):
    scan_dir, inv = tmp_path / "scan", tmp_path / "inv"
    assert scan_holdings.main(["--root", str(tree), "--list-shards"]) == 0
    assert capsys.readouterr().out.split() == list_shards(tree)

    monkeypatch.setenv("SLURM_ARRAY_TASK_ID", "0")
    scan_holdings.main(["--root", str(tree), "--out", str(scan_dir)])
    assert (scan_dir / "holdings_CMIP.parquet").exists()
    monkeypatch.delenv("SLURM_ARRAY_TASK_ID")
    for s in ["misc", ROOT_FILES_SHARD]:
        scan_holdings.main(["--root", str(tree), "--out", str(scan_dir), "--shard", s])
    scan_holdings.main(
        ["--root", str(tree), "--out", str(scan_dir), "--shard-index", "1"]
    )

    assert (
        summarize_scan.main(["--scan", str(scan_dir), "--out", str(inv), "--csv"]) == 0
    )
    tot = pl.read_parquet(inv / "totals.parquet").row(0, named=True)
    assert tot["n_symlinks"] == 3
    assert tot["n_broken_symlinks"] == 1
    assert tot["n_files"] == 6  # FN, hard link, fx, notes, weird, README
    assert tot["n_nc"] == 4
    assert tot["n_nc_parsed"] == 2  # FN, fx
    assert tot["n_unique_inodes"] == 5
    assert tot["unique_inode_apparent_tb"] < tot["apparent_tb"]
    assert tot["n_datasets"] == 2

    ds = pl.read_parquet(inv / "datasets.parquet")
    tas = ds.filter(pl.col("variable_id") == "tas").row(0, named=True)
    # the hard link and link.nc have non-CMIP names, so only FN is in the dataset
    assert tas["n_files"] == 1
    assert tas["time_start"] == "185001" and tas["time_end"] == "201412"
    assert (inv / "datasets.csv").exists()

    conf = pl.read_parquet(inv / "conformance.parquet")
    assert set(conf["drs_status"]) == {"ok", "not_nc", "bad_filename", "wrong_depth"}
    unp = pl.read_parquet(inv / "unparsed_by_dir.parquet")
    assert unp.filter(pl.col("dir") == "misc")["n"].item() == 4
    for f in ["multi_version", "conformance_samples", "unparsed_samples", "errors"]:
        assert (inv / f"{f}.parquet").exists()


def test_scan_nested_shard(tree, tmp_path):
    res = scan(tree, tmp_path / "out", shard="CMIP/NCAR/CESM2/")
    assert res["holdings"].name == "holdings_CMIP__NCAR__CESM2.parquet"
    df = pl.read_parquet(res["holdings"])
    # rel_path stays relative to the root, so the DRS check still works
    assert df["rel_path"].to_list() == [f"{DRS_DIR}/{FN}"]
    assert df["drs_status"].to_list() == ["ok"]
    assert df["top_dir"].to_list() == ["CMIP"]
    for bad in ["../x", "/abs", "CMIP/NCAR/nope"]:
        with pytest.raises(ValueError):
            scan(tree, tmp_path / "out", shard=bad)


def test_cli_nested_shard(tree, tmp_path):
    out = tmp_path / "out"
    args = ["--root", str(tree), "--out", str(out), "--shard", "CMIP/NCAR"]
    assert scan_holdings.main(args) == 0
    assert (out / "holdings_CMIP__NCAR.parquet").exists()


def test_list_shards_split(tree):
    shards = list_shards(tree, split=["CMIP"])
    assert "CMIP" not in shards
    assert "CMIP/NCAR" in shards
    assert shards[-1] == ROOT_FILES_SHARD


def test_split_shards_cover_tree(tree, tmp_path):
    out = tmp_path / "out"
    full = pl.read_parquet(scan(tree, tmp_path / "full")["holdings"])
    for s in list_shards(tree, split=["CMIP"]):
        scan(tree, out, shard=s)
    parts = pl.read_parquet(sorted(out.glob("holdings_*.parquet")))
    assert sorted(parts["rel_path"]) == sorted(full["rel_path"])


def test_split_loose_files_get_own_shard(tree, tmp_path):
    (tree / "CMIP" / "stray.txt").write_text("x")
    shards = list_shards(tree, split=["CMIP"])
    assert "CMIP/_root_files" in shards
    res = scan(tree, tmp_path / "out", shard="CMIP/_root_files")
    assert pl.read_parquet(res["holdings"])["rel_path"].to_list() == ["CMIP/stray.txt"]
    out = tmp_path / "all"
    full = pl.read_parquet(scan(tree, tmp_path / "full")["holdings"])
    for s in shards:
        scan(tree, out, shard=s)
    parts = pl.read_parquet(sorted(out.glob("holdings_*.parquet")))
    assert sorted(parts["rel_path"]) == sorted(full["rel_path"])


def test_split_unknown_dir(tree):
    with pytest.raises(ValueError):
        list_shards(tree, split=["nope"])


def test_split_nested(tree, tmp_path):
    shards = list_shards(tree, split=["CMIP", "CMIP/NCAR"])
    assert "CMIP/NCAR" not in shards
    assert "CMIP/NCAR/CESM2" in shards
    out = tmp_path / "out"
    full = pl.read_parquet(scan(tree, tmp_path / "full")["holdings"])
    for s in shards:
        scan(tree, out, shard=s)
    parts = pl.read_parquet(sorted(out.glob("holdings_*.parquet")))
    assert sorted(parts["rel_path"]) == sorted(full["rel_path"])
