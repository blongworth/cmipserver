"""Site deploy: production settings, publish_site.sh and the VM's pull_site.sh.

The shell scripts run against temp dirs (CMIP_SITE_SRC / CMIP_SITE_DEST), so
nothing here touches VAST or /srv.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import make_sample_catalog as msc
import pytest
import summarize_catalog
import write_site_config
import yaml

ROOT = Path(__file__).parent.parent
SITE = ROOT / "site"
PUBLISH = ROOT / "scripts" / "publish_site.sh"
PULL = ROOT / "infra" / "vm" / "pull_site.sh"
SMOKE = ROOT / "infra" / "vm" / "smoke_test.sh"
FIXTURES = Path(__file__).parent / "fixtures"

needs_shell = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("bash", "rsync", "python3")),
    reason="needs bash, rsync, python3",
)


# --- production settings -------------------------------------------------------


def test_apply_overrides_keeps_comments_and_order():
    text = "# head\na: 1  # note\nb: x\nc: >-\n  long\n"
    out = write_site_config.apply_overrides(text, {"b": "http://h/data", "a": 2})
    assert out == '# head\na: 2\nb: "http://h/data"\nc: >-\n  long\n'
    assert yaml.safe_load(out)["b"] == "http://h/data"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [({"zz": 1}, KeyError), ({"c": "x"}, ValueError), ({"a": [1]}, TypeError)],
)
def test_apply_overrides_rejects(overrides, error):
    with pytest.raises(error):
        write_site_config.apply_overrides("a: 1\nc: >-\n  long\n", overrides)


def test_production_values_apply_to_variables():
    prod = yaml.safe_load((SITE / "_production.yml").read_text())
    text = write_site_config.apply_overrides(
        (SITE / "_variables.yml").read_text(),
        {**prod, "catalog_version": "2026-11-13"},
    )
    variables = yaml.safe_load(text)
    cfg = write_site_config.site_config(variables)
    assert cfg["site_url"] == prod["site_url"]
    assert cfg["catalog_version"] == "2026-11-13"
    assert variables["acknowledgment"].startswith("CMIP6 model output")
    assert "https_prefix" not in write_site_config.placeholders(variables)


def test_production_profile_matches_production_values():
    prod = yaml.safe_load((SITE / "_production.yml").read_text())
    profile = yaml.safe_load((SITE / "_quarto-production.yml").read_text())
    assert profile["website"]["site-url"] == prod["site_url"]
    assert prod["https_prefix"] == prod["site_url"] + "/data"


def test_enabled_catalogs_follow_manifest():
    got = summarize_catalog.enabled_catalogs(
        str(ROOT / "manifests/catalogs.yaml"), "/L"
    )
    assert got[:2] == ["/L/whoi_cmip6.csv", "/L/whoi_20crv3.csv"]


def test_summarize_catalog_dir(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    assert msc.main(["--out", str(tmp_path / "sample")]) == 0
    shutil.copy(tmp_path / "sample" / "sample_catalog.csv", live / "whoi_cmip6.csv")
    shutil.copy(FIXTURES / "whoi_20crv3.csv", live / "whoi_20crv3.csv")
    manifest = str(ROOT / "manifests/catalogs.yaml")
    argv = ["--catalog-dir", str(live), "--products", manifest]
    assert summarize_catalog.main([*argv, "--out", str(tmp_path / "out")]) == 0
    stats = json.loads((tmp_path / "out" / "catalog_stats.json").read_text())
    assert [c["name"] for c in stats["catalogs"]] == ["whoi_cmip6", "whoi_20crv3"]
    (live / "whoi_20crv3.csv").unlink()
    with pytest.raises(SystemExit):
        summarize_catalog.main([*argv, "--out", str(tmp_path / "out2")])
    with pytest.raises(SystemExit):
        summarize_catalog.main(["--catalog-dir", str(live), "--out", str(tmp_path)])


# --- publish / pull --------------------------------------------------------------


def fake_site(path: Path, n_datasets: int = 2) -> Path:
    """A rendered site with everything smoke_test.sh checks."""
    for page in [
        "index.html",
        "browse.html",
        "dataset.html",
        "catalog.html",
        "holdings.html",
        "about.html",
        "access.html",
        "robots.txt",
    ]:
        (path / page).parent.mkdir(parents=True, exist_ok=True)
        (path / page).write_text(f"<html>{page}</html>\n")
    (path / "search.json").write_text("[]\n")
    data = path / "data"
    data.mkdir()
    cfg = {"audience": "internal", "catalog_version": "2026-11-12"}
    (data / "site_config.json").write_text(json.dumps(cfg))
    (data / "catalog_summary.json").write_text(json.dumps([{}] * n_datasets))
    (data / "catalog_stats.json").write_text(json.dumps({"n_datasets": n_datasets}))
    (data / "products.json").write_text("{}")
    shutil.copytree(SITE / "vendor", path / "vendor")
    return path


def run(cmd: list, env: dict | None = None, ok: bool = True):
    r = subprocess.run(
        [str(c) for c in cmd],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


@pytest.fixture
def deploy(tmp_path):
    vast = tmp_path / "vast"
    srv = tmp_path / "srv"
    vast.mkdir()
    (srv / "releases").mkdir(parents=True)
    env = {"CMIP_SITE_SRC": str(vast), "CMIP_SITE_DEST": str(srv)}

    def publish(site: Path, *extra) -> str:
        r = run([PUBLISH, "--src", site, "--dest", vast, "--publish", *extra])
        stamp = (vast / "LATEST").read_text().strip()
        assert f"LATEST={stamp}" in r.stdout
        return stamp

    def pull(*args, ok=True, **extra):
        return run([PULL, *args], {**env, **extra}, ok=ok)

    def live() -> str:
        return os.readlink(srv / "current").removeprefix("releases/")

    return vast, srv, publish, pull, live


def fresh_stamp(vast: Path) -> None:
    """publish_site.sh stamps to the second; make the next stamp differ."""
    latest = vast / "LATEST"
    while (
        latest.exists()
        and time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) <= latest.read_text().strip()
    ):
        time.sleep(0.2)


@needs_shell
def test_publish_dry_run_writes_nothing(tmp_path):
    site = fake_site(tmp_path / "_site")
    dest = tmp_path / "dest"
    dest.mkdir()
    r = run([PUBLISH, "--src", site, "--dest", dest])
    assert "dry run" in r.stdout
    assert list(dest.iterdir()) == []


@needs_shell
def test_publish_refuses_store_and_unrendered(tmp_path):
    site = fake_site(tmp_path / "_site")
    r = run([PUBLISH, "--src", site, "--dest", "/proj/cmip6/site"], ok=False)
    assert r.returncode != 0
    assert "outside /proj/cmip6" in r.stderr
    r = run([PUBLISH, "--src", tmp_path, "--dest", tmp_path], ok=False)
    assert r.returncode != 0


@needs_shell
def test_publish_writes_complete_release(tmp_path, deploy):
    vast, _, publish, _, _ = deploy
    stamp = publish(fake_site(tmp_path / "_site"), "--commit", "abc123")
    rel = vast / "releases" / stamp
    ok = dict(line.split("=", 1) for line in (rel / "BUILD_OK").read_text().split())
    assert ok["stamp"] == stamp
    assert ok["commit"] == "abc123"
    assert ok["catalog_version"] == "2026-11-12"
    n_files = sum(1 for p in (rel / "site").rglob("*") if p.is_file())
    assert int(ok["files"]) == n_files
    assert len((rel / "SHA256SUMS").read_text().splitlines()) == n_files
    build = json.loads((rel / "site" / "build.json").read_text())
    assert build == {
        "stamp": stamp,
        "commit": "abc123",
        "catalog_version": "2026-11-12",
    }
    assert not list((vast / "releases").glob(".tmp-*"))


@needs_shell
def test_smoke_test_catches_empty_summary(tmp_path, deploy):
    vast, _, publish, _, _ = deploy
    stamp = publish(fake_site(tmp_path / "_site"))
    rel = vast / "releases" / stamp
    run([SMOKE, rel])
    (rel / "site" / "data" / "catalog_summary.json").write_text("[]")
    r = run([SMOKE, rel], ok=False)
    assert r.returncode != 0
    assert "no rows" in r.stderr


@needs_shell
def test_pull_rollback_resume(tmp_path, deploy):
    vast, srv, publish, pull, live = deploy
    first = publish(fake_site(tmp_path / "a"))
    pull()
    assert live() == first
    assert (srv / "current" / "site" / "index.html").exists()
    assert pull().stdout == ""  # nothing new: silent

    fresh_stamp(vast)
    second = publish(fake_site(tmp_path / "b", n_datasets=3))
    pull()
    assert live() == second

    out = pull("--rollback", first).stdout
    assert live() == first
    assert "HOLD set" in out
    assert "not switching" in pull().stdout
    assert live() == first
    pull("--resume")
    assert live() == second
    assert not (srv / "HOLD").exists()
    status = pull("--status").stdout
    assert f"current: {second}" in status
    assert f"* {second}" in status


@needs_shell
def test_pull_ignores_release_without_build_ok(tmp_path, deploy):
    vast, _, publish, pull, live = deploy
    first = publish(fake_site(tmp_path / "a"))
    pull()
    fresh_stamp(vast)
    second = publish(fake_site(tmp_path / "b"))
    (vast / "releases" / second / "BUILD_OK").unlink()
    assert "no BUILD_OK; ignored" in pull().stdout
    assert live() == first


@needs_shell
def test_pull_rejects_tampered_release_and_keeps_site(tmp_path, deploy):
    vast, srv, publish, pull, live = deploy
    first = publish(fake_site(tmp_path / "a"))
    pull()
    fresh_stamp(vast)
    second = publish(fake_site(tmp_path / "b"))
    (vast / "releases" / second / "site" / "index.html").write_text("changed")
    r = pull(ok=False)
    assert r.returncode != 0
    assert "checksum mismatch" in r.stderr
    assert live() == first
    assert (srv / "FAILED").read_text().strip() == second
    assert not (srv / "releases" / second).exists()
    # not retried every 5 minutes, and does not fail the timer again
    assert "failed its checks earlier" in pull().stdout


@needs_shell
def test_pull_keeps_newest_and_live(tmp_path, deploy):
    vast, srv, publish, pull, live = deploy
    stamps = []
    for i in range(3):
        fresh_stamp(vast)
        stamps.append(publish(fake_site(tmp_path / f"s{i}")))
        pull(CMIP_SITE_KEEP="2")
    kept = sorted(p.name for p in (srv / "releases").iterdir())
    assert kept == stamps[1:]
    assert live() == stamps[-1]
