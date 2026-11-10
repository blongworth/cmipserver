import download_esgf
import polars as pl
import pytest
from conftest import fname

from whoi_cmip_reorg_code.download import run
from whoi_cmip_reorg_code.manifest import RepairManifest, load


class FakeServer:
    """Serves payloads by filename. ``cut`` makes the next N responses stop
    after that many bytes (a dropped connection); ``no_range`` ignores Range."""

    def __init__(self, payloads, cut=None, no_range=False):
        self.payloads = payloads
        self.cut = cut
        self.no_range = no_range
        self.calls = []

    def __call__(self, url, start):
        self.calls.append((url.rsplit("/", 1)[-1], start))
        data = self.payloads[url.rsplit("/", 1)[-1]]
        resumed = bool(start) and not self.no_range
        body = data[start:] if resumed else data

        def gen():
            if self.cut is not None:
                n, self.cut = self.cut, None
                yield body[:n]
                raise ConnectionError("connection reset")
            yield from (body[i : i + 1000] for i in range(0, len(body), 1000))

        return gen(), resumed


@pytest.fixture
def repairs(audit):
    return load(audit / "repairs.yaml", RepairManifest)


def test_dry_run_writes_nothing(audit, fake_esgf, tmp_path):
    stage = tmp_path / "staging"
    srv = FakeServer(fake_esgf.payloads)
    log = tmp_path / "log.parquet"
    rc = download_esgf.main(
        [
            "--repairs",
            str(audit / "repairs.yaml"),
            "--staging-root",
            str(stage),
            "--log",
            str(log),
        ],
        opener=srv,
    )
    assert rc == 0
    assert not stage.exists()
    assert srv.calls == []
    assert set(pl.read_parquet(log)["status"]) == {"would_download"}


def test_execute_downloads_and_verifies(audit, fake_esgf, tmp_path, messy_store):
    stage = tmp_path / "staging"
    rc = download_esgf.main(
        [
            "--repairs",
            str(audit / "repairs.yaml"),
            "--staging-root",
            str(stage),
            "--execute",
        ],
        opener=FakeServer(fake_esgf.payloads),
    )
    assert rc == 0
    got = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*.nc"))
    assert len(got) == 3
    mid = fname("pr", "Amon", "190001-194912")
    assert any(g.endswith(f"/pr/gn/v20190308/{mid}") for g in got)
    assert not list(stage.rglob("*.part"))
    # the live store is untouched
    assert messy_store.paths["empty"].stat().st_size == 0
    # re-run: everything present and verified, nothing fetched
    srv = FakeServer(fake_esgf.payloads)
    rc = download_esgf.main(
        [
            "--repairs",
            str(audit / "repairs.yaml"),
            "--staging-root",
            str(stage),
            "--execute",
        ],
        opener=srv,
    )
    assert rc == 0 and srv.calls == []


@pytest.mark.parametrize("no_range", [False, True])
def test_resume_after_interruption(repairs, fake_esgf, tmp_path, no_range):
    stage = tmp_path / "staging"
    srv = FakeServer(fake_esgf.payloads, cut=500, no_range=no_range)
    res = run(repairs, stage, srv, execute=True)
    assert [r.status for r in res].count("error") == 1
    assert len(list(stage.rglob("*.part"))) == 1
    res = run(repairs, stage, srv, execute=True)
    statuses = sorted(r.status for r in res)
    assert statuses.count("downloaded") == 1 and statuses.count("present_ok") == 2
    assert not list(stage.rglob("*.part"))
    resumed = [s for _, s in srv.calls if s]
    assert resumed == [500]


def test_bad_checksum_is_not_installed(repairs, fake_esgf, tmp_path):
    payloads = {k: b"X" + v[1:] for k, v in fake_esgf.payloads.items()}
    stage = tmp_path / "staging"
    res = run(repairs, stage, FakeServer(payloads), execute=True)
    assert {r.status for r in res} == {"checksum_mismatch"}
    assert not list(stage.rglob("*.nc"))
    assert len(list(stage.rglob("*.nc.bad"))) == 3


def test_refuses_staging_inside_store(repairs, messy_store, fake_esgf):
    with pytest.raises(ValueError, match="protected"):
        run(
            repairs,
            messy_store.root / "staging",
            FakeServer({}),
            protected=[messy_store.root],
        )


@pytest.mark.parametrize(
    "staging, ok",
    [
        ("tmp/staging", True),  # beside data/, same filesystem: allowed
        ("data/staging", False),  # inside the live tree
        ("data", False),
        (".", False),  # above data/: rel paths could land in it
    ],
)
def test_staging_beside_protected_data(tmp_path, repairs, staging, ok):
    store = tmp_path / "cmip6"
    (store / "data").mkdir(parents=True)
    root = store / staging

    def go():
        return run(repairs, root, FakeServer({}), protected=[store / "data"])

    if ok:
        assert {r.status for r in go()} == {"would_download"}
    else:
        with pytest.raises(ValueError, match="protected"):
            go()
