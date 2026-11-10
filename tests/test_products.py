"""Reanalysis product pipeline: manifest, listing, store status, download,
install, catalog. All offline: a fake remote and opener stand in for PSL."""

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import build_catalog
import download_product
import intake
import numpy as np
import plan_moves
import plan_product
import polars as pl
import pytest
import summarize_catalog
import xarray as xr
import yaml
from pydantic import ValidationError
from test_download import FakeServer

from whoi_cmip_reorg_code import products
from whoi_cmip_reorg_code.catalog import reanalysis
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA
from whoi_cmip_reorg_code.download import run
from whoi_cmip_reorg_code.manifest import (
    FetchItem,
    FetchManifest,
    MoveManifest,
    ProductManifest,
    load,
)
from whoi_cmip_reorg_code.movelog import MoveLog, apply
from whoi_cmip_reorg_code.moves import plan_install_moves

REPO = Path(__file__).resolve().parents[1]
BASE = "https://example.test/20CR/"
MTIME = "2022-05-13T18:06:00+00:00"

SPEC = {
    "schema_version": 1,
    "product": "20crv3",
    "title": "TEST 20CR",
    "version": "v3",
    "source": {"kind": "https_index", "base_url": BASE},
    "years": [2000, 2001],
    "groups": [
        {
            "remote_dir": "2mMO",
            "frequency": "3hr",
            "level": "2m",
            "file_pattern": "{variable}.2m.{year}.nc",
            "variables": ["air", "shum"],
            "expect": {"units": {"air": "degK"}},
        },
        {
            "remote_dir": "prsMO",
            "frequency": "3hr",
            "level": "prs",
            "file_pattern": "{variable}.{year}.nc",
            "variables": ["air"],
        },
        {
            "remote_dir": "Monthlies/2mMO",
            "frequency": "mon",
            "level": "2m",
            "file_pattern": "{variable}.2m.mon.mean.nc",
            "variables": ["air"],
        },
    ],
}

# Real PSL row markup (trimmed), plus a <pre> style index.
PSL_ROWS = """<table id="indexlist">
<tr class="indexhead"><th class="indexcolname"><a href="?C=N;O=D">Name</a></th></tr>
<tr class="even"><td class="indexcolicon"><img src="/icons/back.gif" alt="[Up]" /></td><td class="indexcolname"><a href="/Datasets/20thC_ReanV3/">Parent Directory</a></td><td class="indexcollastmod">&nbsp;</td><td class="indexcolsize">  - </td><td class="indexcoldesc">&nbsp;</td></tr>
<tr class="odd"><td class="indexcolicon"><img src="/icons/ball.gray.gif" alt="[   ]" /></td><td class="indexcolname"><a href="air.2m.1981.nc">air.2m.1981.nc</a></td><td class="indexcollastmod">2022-05-13 12:06  </td><td class="indexcolsize">199M</td><td class="indexcoldesc">&nbsp;</td></tr>
<tr class="even"><td class="indexcolicon"><img src="/icons/folder.gif" alt="[DIR]" /></td><td class="indexcolname"><a href="2mMO/">2mMO/</a></td><td class="indexcollastmod">2023-11-15 14:17  </td><td class="indexcolsize">  - </td><td class="indexcoldesc">&nbsp;</td></tr>
<tr class="odd"><td class="indexcolicon"><img src="/icons/ball.gray.gif" alt="[   ]" /></td><td class="indexcolname"><a href="shum.2m.1981.nc">shum.2m.1981.nc</a></td><td class="indexcollastmod">2022-05-13 12:07  </td><td class="indexcolsize">1.2G</td><td class="indexcoldesc">&nbsp;</td></tr>
</table>"""
PRE_INDEX = """<pre><a href="?C=N;O=D">Name</a>
<a href="../">Parent Directory</a>                             -
<a href="air.2m.1981.nc">air.2m.1981.nc</a>          2022-05-13 12:06  199M
</pre>"""


def write_rean(path: Path, var: str, year: int | None, units="degK") -> Path:
    """Tiny 20CR-like file: 3-hourly in ``year`` (or 12 monthly steps)."""
    if year is None:
        t = np.arange(12) * 730.0
        y0 = 2000
    else:
        t = np.arange(8) * 3.0
        y0 = year
    ds = xr.Dataset(
        {var: (("time", "lat"), np.ones((t.size, 2), "float32"), {"units": units})},
        coords={
            "time": ("time", t, {"units": f"hours since {y0}-01-01 00:00:00"}),
            "lat": ("lat", [-45.0, 45.0]),
        },
        attrs={"title": "SYNTHETIC test data"},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path, engine="netcdf4")
    return path


@pytest.fixture
def spec() -> ProductManifest:
    return ProductManifest.model_validate(SPEC)


@pytest.fixture
def payloads(spec, tmp_path_factory) -> dict[str, bytes]:
    """remote_path -> bytes for every expected file (synthetic NetCDF)."""
    d = tmp_path_factory.mktemp("upstream")
    out = {}
    for r in spec.expand():
        p = write_rean(d / r["remote_path"], r["variable_id"], r["year"])
        out[r["remote_path"]] = p.read_bytes()
    return out


class FakeRemote:
    """Autoindex pages + HEAD for ``files`` (remote_path -> bytes)."""

    def __init__(self, files):
        self.files = files
        self.heads = 0

    def get_text(self, url):
        d = url.removeprefix(BASE).rstrip("/")
        rows = "".join(
            f'<tr><td class="indexcolname"><a href="{Path(k).name}">{Path(k).name}</a>'
            f'</td><td class="indexcollastmod">2022-05-13 12:06  </td>'
            f'<td class="indexcolsize">{len(v) // 1024}K</td></tr>'
            for k, v in self.files.items()
            if str(Path(k).parent) == d
        )
        return f"<table>{rows}</table>"

    def head(self, url):
        self.heads += 1
        return len(self.files[url.removeprefix(BASE)]), MTIME


class PathServer(FakeServer):
    """FakeServer keyed by the path under BASE (names repeat across dirs)."""

    def __call__(self, url, start):
        return super().__call__(url.removeprefix(BASE).replace("/", "|"), start)


def path_server(payloads, **kw):
    srv = PathServer({k.replace("/", "|"): v for k, v in payloads.items()}, **kw)
    return srv


# --- manifest ----------------------------------------------------------------


def test_repo_product_manifest_valid():
    m = load(REPO / "manifests/products/20crv3.yaml", ProductManifest)
    rows = m.expand()
    assert len(rows) == 10
    assert {r["frequency"] for r in rows} == {"mon"}
    assert all(r["year"] is None for r in rows)
    assert rows[0]["url"].startswith("https://downloads.psl.noaa.gov/")


def test_expand(spec):
    rows = spec.expand()
    assert len(rows) == 2 * 2 + 1 * 2 + 1
    r = next(r for r in rows if r["remote_path"] == "prsMO/air.2001.nc")
    assert r["rel_path"] == "reanalysis/20crv3/prsMO/air.2001.nc"
    assert (r["level"], r["year"], r["expect_units"]) == ("prs", 2001, None)
    mon = next(r for r in rows if r["frequency"] == "mon")
    assert (
        mon["year"] is None and mon["url"] == BASE + "Monthlies/2mMO/air.2m.mon.mean.nc"
    )


def test_path_template():
    s = json.loads(json.dumps(SPEC))
    s["groups"] = [
        {**SPEC["groups"][0], "path_template": "{frequency}/{level}/{variable}/{file}"}
    ]
    rows = ProductManifest.model_validate(s).expand()
    assert rows[0]["rel_path"] == "reanalysis/20crv3/3hr/2m/air/air.2m.2000.nc"
    assert rows[0]["url"] == BASE + "2mMO/air.2m.2000.nc"


@pytest.mark.parametrize(
    "patch, msg",
    [
        ({"remote_dir": "../x"}, "relative without"),
        ({"remote_dir": "/abs"}, "relative without"),
        ({"variables": []}, "at least one variable"),
        ({"file_pattern": "air.{year}.nc"}, "needs {variable}"),
        ({"file_pattern": "{variable}.{month}.nc"}, "may only use"),
        ({"years": [2001, 2000]}, "bad year range"),
        ({"level": "2.m"}, "must match"),
        ({"path_template": "../{file}"}, "relative without"),
        ({"path_template": "{remote_dir}/x.nc"}, "same path"),
    ],
)
def test_manifest_errors(patch, msg):
    s = json.loads(json.dumps(SPEC))
    s["groups"][0].update(patch)
    with pytest.raises(ValidationError, match=msg):
        ProductManifest.model_validate(s)


def test_duplicate_dataset_rejected():
    s = json.loads(json.dumps(SPEC))
    # same (mon, 2m, air) from a second dir: two single-file copies
    s["groups"].append({**SPEC["groups"][2], "remote_dir": "Monthlies/2mSI-MO"})
    with pytest.raises(ValidationError, match="twice"):
        ProductManifest.model_validate(s)
    # two eras of one dataset are fine
    s = json.loads(json.dumps(SPEC))
    s["groups"].append(
        {**SPEC["groups"][0], "remote_dir": "2mSI", "years": [1990, 1999]}
    )
    ProductManifest.model_validate(s)


def test_fetch_item_needs_verification():
    with pytest.raises(ValidationError, match="size_bytes or checksum"):
        FetchItem(url=BASE + "a.nc", rel_path="reanalysis/x/a.nc")


def test_catalogs_spec_rule():
    from whoi_cmip_reorg_code.manifest import Product

    base = {"name": "x", "root": "/r", "output": "o"}
    with pytest.raises(ValidationError, match="spec"):
        Product.model_validate({**base, "builder": "reanalysis"})
    with pytest.raises(ValidationError, match="spec"):
        Product.model_validate({**base, "builder": "cmip6", "spec": "p.yaml"})


# --- listing -----------------------------------------------------------------


def test_parse_autoindex_psl():
    got = products.parse_autoindex(PSL_ROWS)
    assert got == [
        {
            "name": "air.2m.1981.nc",
            "mtime": "2022-05-13 12:06",
            "size_approx": 199 * 1024**2,
        },
        {
            "name": "shum.2m.1981.nc",
            "mtime": "2022-05-13 12:07",
            "size_approx": int(1.2 * 1024**3),
        },
    ]


def test_parse_autoindex_pre():
    assert products.parse_autoindex(PRE_INDEX) == [
        {
            "name": "air.2m.1981.nc",
            "mtime": "2022-05-13 12:06",
            "size_approx": 199 * 1024**2,
        }
    ]


def test_remote_status(spec, payloads):
    files = dict(payloads)
    del files["prsMO/air.2001.nc"]
    rem = FakeRemote(files)
    st = products.remote_status(products.expected_files(spec), BASE, rem)
    assert st.filter(~pl.col("in_upstream"))["remote_path"].to_list() == [
        "prsMO/air.2001.nc"
    ]
    assert rem.heads == len(files)
    row = st.filter(remote_path="2mMO/air.2m.2000.nc").row(0, named=True)
    assert row["size_bytes"] == len(payloads["2mMO/air.2m.2000.nc"])
    assert row["remote_mtime"] == MTIME


# --- store status ------------------------------------------------------------


def _store(tmp_path, payloads):
    """Store with: present_ok, present_differs (size), extra_local; and a
    legacy copy elsewhere (for possible_duplicate via a fake scan)."""
    store = tmp_path / "store"
    pdir = store / "reanalysis/20crv3"
    ok = pdir / "2mMO/air.2m.2000.nc"
    ok.parent.mkdir(parents=True)
    ok.write_bytes(payloads["2mMO/air.2m.2000.nc"])
    ts = datetime.fromisoformat(MTIME).timestamp()
    os.utime(ok, (ts, ts))
    (pdir / "2mMO/shum.2m.2000.nc").write_bytes(b"short")
    os.utime(pdir / "2mMO/shum.2m.2000.nc", (ts, ts))
    (pdir / "2mMO/notes.txt").write_text("x")
    legacy = store / "old/20CR/air.2001.nc"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"x")
    return store, legacy


def test_store_status(spec, payloads, tmp_path):
    store, legacy = _store(tmp_path, payloads)
    files = {k: v for k, v in payloads.items() if k != "prsMO/air.2001.nc"}
    exp = products.remote_status(products.expected_files(spec), BASE, FakeRemote(files))
    scan = pl.DataFrame(
        {
            "path": [str(legacy), str(store / "reanalysis/20crv3/2mMO/air.2m.2000.nc")],
            "file_type": ["file", "file"],
        }
    )
    st = products.store_status(exp, store, "20crv3", scan)
    by = dict(zip(st["rel_path"], st["status"]))
    p = "reanalysis/20crv3/"
    assert by[p + "2mMO/air.2m.2000.nc"] == "present_ok"
    assert by[p + "2mMO/shum.2m.2000.nc"] == "present_differs"
    assert by[p + "2mMO/notes.txt"] == "extra_local"
    assert by[p + "2mMO/air.2m.2001.nc"] == "missing"
    # not upstream and not local; the legacy copy is flagged separately
    dup = st.filter(status="possible_duplicate")
    assert dup["local_path"].to_list() == [str(legacy)]
    assert (
        st.filter(rel_path=p + "prsMO/air.2001.nc", status="not_upstream").height == 1
    )
    assert products.verdict(st) == "possible_duplicate"
    items = products.fetch_items(st)
    assert {i.rel_path for i in items} == {
        p + "2mMO/air.2m.2001.nc",
        p + "2mMO/shum.2m.2001.nc",
        p + "prsMO/air.2000.nc",
        p + "Monthlies/2mMO/air.2m.mon.mean.nc",
    }
    assert all(i.size_bytes and i.remote_mtime == MTIME for i in items)


def test_store_status_upstream_newer(spec, payloads, tmp_path):
    store, _ = _store(tmp_path, payloads)
    ok = store / "reanalysis/20crv3/2mMO/air.2m.2000.nc"
    os.utime(ok, (0, 0))
    exp = products.remote_status(
        products.expected_files(spec), BASE, FakeRemote(payloads)
    )
    st = products.store_status(exp, store, "20crv3")
    row = st.filter(rel_path="reanalysis/20crv3/2mMO/air.2m.2000.nc").row(0, named=True)
    assert row["status"] == "present_differs" and "upstream newer" in row["detail"]


def test_verdicts(spec, payloads, tmp_path):
    exp = products.expected_files(spec)
    assert products.verdict(products.store_status(exp, tmp_path, "20crv3")) == "new"
    st = products.store_status(exp, _store(tmp_path, payloads)[0], "20crv3")
    assert set(st["status"]) >= {"present_unchecked", "missing"}  # offline
    assert products.verdict(st) == "partial"
    full = tmp_path / "full"
    for r in spec.expand():
        (full / r["rel_path"]).parent.mkdir(parents=True, exist_ok=True)
        (full / r["rel_path"]).write_bytes(payloads[r["remote_path"]])
    assert products.verdict(products.store_status(exp, full, "20crv3")) == "complete"


# --- download ----------------------------------------------------------------


def _fetch(spec, payloads, tmp_path, store=None) -> FetchManifest:
    exp = products.remote_status(
        products.expected_files(spec), BASE, FakeRemote(payloads)
    )
    st = products.store_status(exp, store or tmp_path / "empty", "20crv3")
    return FetchManifest(
        created=datetime.now(UTC),
        created_by="test",
        product="20crv3",
        spec="x.yaml",
        items=products.fetch_items(st),
    )


def test_download_without_checksum(spec, payloads, tmp_path):
    fm = _fetch(spec, payloads, tmp_path)
    stage = tmp_path / "staging"
    res = run(
        fm, stage, path_server(payloads), execute=True, check=products.check_fetched
    )
    assert {r.status for r in res} == {"downloaded"}
    assert all(r.digest and len(r.digest) == 64 for r in res)
    f = stage / "reanalysis/20crv3/2mMO/air.2m.2000.nc"
    assert f.read_bytes() == payloads["2mMO/air.2m.2000.nc"]
    assert f.stat().st_mtime == datetime.fromisoformat(MTIME).timestamp()
    # re-run: verified, nothing fetched
    srv = path_server(payloads)
    res = run(fm, stage, srv, execute=True, check=products.check_fetched)
    assert {r.status for r in res} == {"present_ok"} and srv.calls == []


def test_download_bad_files_kept_as_bad(spec, payloads, tmp_path):
    fm = _fetch(spec, payloads, tmp_path)
    bad = dict(payloads)
    k_size, k_junk, k_var, k_year = (
        "2mMO/air.2m.2000.nc",
        "2mMO/air.2m.2001.nc",
        "prsMO/air.2000.nc",
        "2mMO/shum.2m.2000.nc",
    )
    bad[k_size] = payloads[k_size][:-10]  # truncated in transit
    bad[k_junk] = b"<html>error</html>".ljust(len(payloads[k_junk]), b" ")
    d = tmp_path / "wrong"
    # same size is not guaranteed for a different file; patch sizes to match
    wrong_var = write_rean(d / "v.nc", "hgt", 2000).read_bytes()
    wrong_year = write_rean(d / "y.nc", "shum", 1999).read_bytes()
    bad[k_var], bad[k_year] = wrong_var, wrong_year
    for it in fm.items:
        rp = it.rel_path.removeprefix("reanalysis/20crv3/")
        if rp in (k_var, k_year):
            it.size_bytes = len(bad[rp])
    stage = tmp_path / "staging"
    res = {
        r.drs_rel_path.removeprefix("reanalysis/20crv3/"): r
        for r in run(
            fm, stage, path_server(bad), execute=True, check=products.check_fetched
        )
    }
    assert res[k_size].status == "size_mismatch"
    assert res[k_junk].status == "content_bad" and "nc_status" in res[k_junk].error
    assert res[k_var].status == "content_bad" and "variable air" in res[k_var].error
    assert (
        res[k_year].status == "content_bad" and "not within 2000" in res[k_year].error
    )
    for k in (k_size, k_junk, k_var, k_year):
        p = stage / "reanalysis/20crv3" / k
        assert not p.exists() and p.with_name(p.name + ".bad").exists()


def test_units_checked_not_converted(tmp_path):
    f = write_rean(tmp_path / "a.nc", "air", 2000, units="K")
    item = FetchItem(
        url=BASE + "a",
        rel_path="a.nc",
        size_bytes=1,
        variable_id="air",
        year=2000,
        expect_units="degK",
    )
    assert "units 'K', expected 'degK'" in products.check_fetched(f, item)
    item.expect_units = "K"
    assert products.check_fetched(f, item) is None


# --- install -----------------------------------------------------------------


def test_install_collision_refused(spec, payloads, tmp_path):
    fm = _fetch(spec, payloads, tmp_path)
    stage, store = tmp_path / "staging", tmp_path / "store"
    run(fm, stage, path_server(payloads), execute=True)
    occupied = store / fm.items[0].rel_path
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"already here")
    (stage / fm.items[1].rel_path).unlink()
    moves, report = plan_install_moves(fm, str(stage), str(store))
    by = dict(zip(report["dst"], report["status"]))
    assert by[str(occupied)] == "collision"
    assert by[str(store / fm.items[1].rel_path)] == "not_staged"
    assert str(occupied) not in {m.dst for m in moves}
    assert len(moves) == len(fm.items) - 2
    assert {m.kind for m in moves} == {"install"}


# --- catalog -----------------------------------------------------------------


def test_reanalysis_build(spec, payloads, tmp_path):
    pdir = tmp_path / "store/reanalysis/20crv3"
    for r in spec.expand():
        p = pdir / r["remote_path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payloads[r["remote_path"]])
    write_rean(pdir / "2mMO/air.2m.1990.nc", "air", 1990)  # year outside manifest
    write_rean(pdir / "2mMO/tmax.2m.2000.nc", "tmax", 2000)  # variable not listed
    df, invalid = reanalysis.build(str(pdir), spec, jobs=1)
    assert df.schema == pl.Schema(CATALOG_SCHEMA)
    assert df.height == len(spec.expand())
    assert sorted(Path(p).name for p in invalid["path"]) == [
        "air.2m.1990.nc",
        "tmax.2m.2000.nc",
    ]
    r = df.filter(pl.col("path").str.ends_with("/prsMO/air.2000.nc")).row(0, named=True)
    assert r["master_id"] == "reanalysis.20crv3.3hr.prs.air"
    assert (r["collection"], r["version"], r["time_range"]) == (
        "reanalysis",
        "v3",
        "2000",
    )
    assert r["source_id"] is None
    mon = df.filter(frequency="mon").row(0, named=True)
    assert mon["time_range"] is None and mon["level"] == "2m"
    # the same variable at two levels is two datasets
    assert df.filter(variable_id="air", frequency="3hr")["master_id"].n_unique() == 2


# --- end to end --------------------------------------------------------------


def test_end_to_end(spec, payloads, tmp_path, capsys):
    """plan -> download -> plan install -> approve -> apply -> catalog ->
    summary; then a second plan finds the product complete."""
    spec_path = tmp_path / "manifests/products/20crv3.yaml"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(yaml.safe_dump(SPEC))
    store, stage = tmp_path / "store", tmp_path / "staging"
    store.mkdir()
    fetch = tmp_path / "fetch.yaml"
    rem = FakeRemote(payloads)
    common = ["--product", str(spec_path), "--store-root", str(store)]

    # offline: store check only, no fetch manifest
    assert (
        plan_product.main([*common, "--out", str(tmp_path / "off"), "--offline"]) == 0
    )
    assert "verdict: new" in capsys.readouterr().out
    assert not fetch.exists()

    # ladder: one group, one variable, one year
    args = [*common, "--out", str(tmp_path / "plan1"), "--fetch-out", str(fetch)]
    assert (
        plan_product.main(
            [*args, "--group", "2mMO", "--variable", "air", "--years", "2000", "2000"],
            remote=rem,
        )
        == 0
    )
    assert len(load(fetch, FetchManifest).items) == 1

    # full plan
    assert (
        plan_product.main(
            [*common, "--out", str(tmp_path / "plan"), "--fetch-out", str(fetch)],
            remote=rem,
        )
        == 0
    )
    assert "verdict: new" in capsys.readouterr().out
    assert (tmp_path / "plan.md").read_text().startswith("# Plan: 20crv3")
    n = len(spec.expand())
    assert len(load(fetch, FetchManifest).items) == n

    dl = ["--fetch", str(fetch), "--staging-root", str(stage), "--protect", str(store)]
    assert (
        download_product.main(
            [*dl, "--log", str(tmp_path / "dry.parquet")], opener=path_server(payloads)
        )
        == 0
    )
    assert not stage.exists()
    assert (
        download_product.main(
            [*dl, "--execute", "--log", str(tmp_path / "dl.parquet")],
            opener=path_server(payloads),
        )
        == 0
    )
    assert set(pl.read_parquet(tmp_path / "dl.parquet")["status"]) == {"downloaded"}

    mpath = tmp_path / "moves.yaml"
    plan_moves.main(
        [
            "install",
            "--fetch",
            str(fetch),
            "--staging-root",
            str(stage),
            "--store-root",
            str(store),
            "--out",
            str(mpath),
        ]
    )
    m = load(mpath, MoveManifest)
    assert not m.approved and len(m.moves) == n
    m.approved_by, m.approved_on = "Brett", date(2026, 11, 10)
    apply(m, MoveLog(tmp_path / "moves.jsonl"), execute=True)
    f = store / "reanalysis/20crv3/2mMO/air.2m.2000.nc"
    assert f.read_bytes() == payloads["2mMO/air.2m.2000.nc"]

    # nothing left to fetch; mtimes survived the install, so present_ok
    assert (
        plan_product.main(
            [*common, "--out", str(tmp_path / "plan2"), "--fetch-out", str(fetch)],
            remote=rem,
        )
        == 0
    )
    assert "verdict: complete" in capsys.readouterr().out
    assert load(fetch, FetchManifest).items == []
    assert set(pl.read_parquet(tmp_path / "plan2.parquet")["status"]) == {"present_ok"}

    cats = tmp_path / "manifests/catalogs.yaml"
    cats.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "products": [
                    {
                        "name": "20crv3",
                        "root": str(store / "reanalysis/20crv3"),
                        "builder": "reanalysis",
                        "spec": "products/20crv3.yaml",
                        "output": "whoi_20crv3",
                    }
                ],
            }
        )
    )
    out = tmp_path / "catalog"
    assert (
        build_catalog.main(["--manifest", str(cats), "--out", str(out), "--jobs", "1"])
        == 0
    )
    cat = intake.open_esm_datastore(str(out / "20crv3.json"))
    assert len(cat.df) == n
    key = "reanalysis.20crv3.3hr.2m.air.v3"
    assert key in cat
    ds = cat[key].to_dask()
    assert ds.sizes["time"] == 16 and ds["air"].attrs["units"] == "degK"

    site = tmp_path / "site_data"
    assert (
        summarize_catalog.main(
            [
                "--catalog",
                str(out / "20crv3.csv"),
                "--out",
                str(site),
                "--root",
                str(store),
            ]
        )
        == 0
    )
    s = json.loads((site / "catalog_summary.json").read_text())
    rec = next(r for r in s if r["dataset_id"] == key)
    assert rec["relative_path"] == "reanalysis/20crv3/2mMO"
    assert (rec["time_start"], rec["time_end"], rec["level"]) == ("2000", "2001", "2m")


def test_repo_catalogs_spec_resolves():
    from whoi_cmip_reorg_code.manifest import CatalogsManifest

    cm = load(REPO / "manifests/catalogs.yaml", CatalogsManifest)
    for p in cm.products:
        if p.builder == "reanalysis":
            load(REPO / "manifests" / p.spec, ProductManifest)
