"""Headless-browser tests of the dataset browser (site/browse.qmd, site/js/).

Needs a rendered site (`uv run quarto render site --no-execute`) and Google
Chrome or Chromium; skipped otherwise. No extra Python dependency: a local
server serves site/_site with synthetic data/ files, plus a same-origin probe
page that loads the browser in an iframe, drives it, and writes the results
into its DOM, which Chrome's --dump-dom returns.
"""

from __future__ import annotations

import html
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site" / "_site"
PAGE = os.environ.get("BROWSE_PAGE", "browse.html")
CONF = ROOT / "infra" / "apache" / "cmip.conf"


def _chrome() -> str | None:
    for c in (
        os.environ.get("CHROME"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ):
        if c and Path(c).exists():
            return c
    return None


CHROME = _chrome()
MISSING = CHROME is None or not (SITE / PAGE).exists()
# CI sets REQUIRE_BROWSER=1 so a missing Chrome or site fails instead of skipping.
REQUIRED = bool(os.environ.get("REQUIRE_BROWSER"))
pytestmark = pytest.mark.skipif(
    MISSING and not REQUIRED,
    reason="needs Chrome and a rendered site (quarto render site --no-execute)",
)


def test_browser_and_site_available():
    assert CHROME, "no Chrome or Chromium found (set CHROME=/path/to/chrome)"
    assert (SITE / PAGE).exists(), f"{SITE / PAGE} missing: render the site first"


CONFIG = {
    "site_url": "https://cmip.example",
    "github_repo": "owner/repo",
    "help_email": "help@example.org",
    "catalog_path": "/proj/cmip6/catalog/live/whoi_cmip6.json",
    "catalog_version": "2026-11-23",
    "vast_prefix": "/proj/cmip6/data",
    "https_prefix": "https://cmip.example/download",
    "globus_url": "",
    "audience": "internal",
    "wget_max_files": 500,
}


def _cmip(src, exp, var, table="Amon", member="r1i1p1f1", n=2):
    did = f"CMIP6.CMIP.INST.{src}.{exp}.{member}.{table}.{var}.gn.1"
    return {
        "dataset_id": did,
        "collection": "cmip6",
        "activity_id": "CMIP",
        "institution_id": "INST",
        "source_id": src,
        "experiment_id": exp,
        "member_id": member,
        "table_id": table,
        "variable_id": var,
        "grid_label": "gn",
        "product": None,
        "frequency": None,
        "level": None,
        "version": "1",
        "time_start": "185001",
        "time_end": "201412",
        "n_files": n,
        "size_gb": 1.5,
        "relative_path": f"cmip6/CMIP/INST/{src}/{exp}/{member}/{table}/{var}/gn/1",
        "file_list": "files/cmip6.json",
    }


SUMMARY = [
    _cmip("ModelA", "historical", "tas"),
    _cmip("ModelA", "historical", "tos", table="Omon"),
    _cmip("ModelA", "piControl", "tas"),
    _cmip("ModelB", "historical", "tas"),
    _cmip("ModelB", "historical", "pr"),
    {
        # a generic product: several variables in one dataset
        "dataset_id": "ocean_reanalysis.glorys.day.so-thetao-zos.12v1",
        "collection": "ocean_reanalysis",
        **dict.fromkeys(
            ["activity_id", "institution_id", "source_id", "experiment_id"], None
        ),
        **dict.fromkeys(["member_id", "table_id", "grid_label", "level"], None),
        "product": "glorys",
        "frequency": "day",
        "variable_id": "so,thetao,zos",
        "version": "12v1",
        "time_start": "1993-01-01",
        "time_end": "1993-01-02",
        "n_files": 2,
        "size_gb": 3.0,
        "relative_path": "ocean_reanalysis/glorys",
        "file_list": "files/glorys.json",
    },
]
FILES = {
    "files/cmip6.json": {
        d["dataset_id"]: [f"{d['variable_id']}_{i}.nc" for i in range(d["n_files"])]
        for d in SUMMARY
        if d["collection"] == "cmip6"
    },
    "files/glorys.json": {
        SUMMARY[-1]["dataset_id"]: ["g_19930101.nc", "g_19930102.nc"]
    },
}
VOCAB = [
    {
        "collection": "cmip6",
        "product": None,
        "table_id": "Amon",
        "level": None,
        "variable_id": "tas",
        "nc_name": "tas",
        "long_name": "Near-Surface Air Temperature",
        "standard_name": "air_temperature",
        "units": "K",
        "description": None,
    },
    {
        "collection": "cmip6",
        "product": None,
        "table_id": "Omon",
        "level": None,
        "variable_id": "tos",
        "nc_name": "tos",
        "long_name": "Sea Surface Temperature",
        "standard_name": "sea_surface_temperature",
        "units": "degC",
        "description": None,
    },
    *[
        {
            "collection": "ocean_reanalysis",
            "product": "glorys",
            "table_id": None,
            "level": None,
            "variable_id": "so,thetao,zos",
            "nc_name": nc,
            "long_name": ln,
            "standard_name": sn,
            "units": u,
            "description": None,
        }
        for nc, ln, sn, u in [
            ("so", "Salinity", "sea_water_salinity", "1e-3"),
            ("thetao", "Temperature", "sea_water_potential_temperature", "degC"),
            ("zos", "Sea surface height", "sea_surface_height_above_geoid", "m"),
        ]
    ],
]
STATS = {
    "n_datasets": len(SUMMARY),
    "n_files": sum(d["n_files"] for d in SUMMARY),
    "size_tb": 0.01,
    "catalogs": [{"name": "whoi_cmip6", "n_datasets": 5, "synthetic": True}],
}


def _data(vocab=VOCAB) -> dict[str, bytes]:
    files = {
        "catalog_summary.json": SUMMARY,
        "variables.json": vocab,
        "catalog_stats.json": STATS,
        "site_config.json": CONFIG,
        **FILES,
    }
    return {k: json.dumps(v).encode() for k, v in files.items()}


class _Server:
    """site/_site with data/ replaced; /__probe.html from memory; optional CSP."""

    def __init__(self, data: dict[str, bytes], csp: str | None = None):
        site, probe = SITE, self

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(site), **k)

            def log_message(self, *a):
                pass

            def end_headers(self):
                if csp:
                    self.send_header("Content-Security-Policy", csp)
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def do_GET(self):
                path = self.path.split("?")[0]
                body = None
                if path == "/__probe.html":
                    body, ctype = probe.probe.encode(), "text/html"
                elif path.startswith("/data/"):
                    body = data.get(path[len("/data/") :], None)
                    ctype = "application/json"
                    if body is None:
                        self.send_error(404)
                        return
                if body is None:
                    return super().do_GET()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.probe = ""
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def _dump(url: str) -> tuple[str, str]:
    """DOM after scripts ran, and Chrome's console log."""
    p = subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--enable-logging=stderr",
            "--v=0",
            "--virtual-time-budget=20000",
            "--dump-dom",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,  # Chrome's exit code is not the result; the DOM is
    )
    return p.stdout, p.stderr


PROBE = """<!doctype html><html><body><pre id="out">pending</pre>
<iframe id="f" src="/%(page)s%(query)s" width="1400" height="1000"></iframe>
<script>
const out = (x) => { document.getElementById("out").textContent = JSON.stringify(x); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 10000) {
  for (let t = 0; t < ms; t += 50) { try { const v = fn(); if (v) return v; } catch (e) {} await sleep(50); }
  throw new Error("timeout: " + fn);
}
const f = document.getElementById("f");
f.addEventListener("load", async () => {
  try {
    const d = f.contentDocument, w = f.contentWindow;
    await until(() => d.querySelector("#browse[data-ready]"));
    await sleep(300);
    const $ = (s) => d.querySelector(s), $$ = (s) => [...d.querySelectorAll(s)];
    const count = () => $("#browse-count strong").textContent;
    const facet = (k) => $$(`[data-facet="${k}"] li`).filter((li) => li.querySelector("input"))
      .map((li) => [li.querySelector(".facet-value").textContent, +li.querySelector(".facet-count").textContent,
                    li.querySelector("input").checked]);
    const tick = async (k, v) => {
      const li = $$(`[data-facet="${k}"] li`).find((li) => li.querySelector(".facet-value")?.textContent === v);
      li.querySelector("input").click(); await sleep(200);
    };
    const urlsTxt = async () => {
      await sleep(300);
      const a = $("#gd-urls-download");
      return await (await fetch(a.href)).text();
    };
    %(steps)s
  } catch (e) { out({error: String(e)}); }
});
</script></body></html>"""


def run_probe(steps: str, query: str = "", data=None) -> dict:
    srv = _Server(data or _data())
    try:
        srv.probe = PROBE % {"page": PAGE, "query": query, "steps": steps}
        dom, _ = _dump(f"{srv.url}/__probe.html")
    finally:
        srv.close()
    m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.DOTALL)
    assert m, dom[:500]
    res = json.loads(html.unescape(m.group(1)))
    assert "error" not in res, res
    return res


def test_url_state_filters_and_warns():
    res = run_probe(
        'out({count: count(), url: w.location.search, warn: $("#browse-warning").textContent,'
        ' chips: $$(".browse-chip").map((c) => c.textContent)});',
        query="?source_id=ModelA&variable_id=tas&source_id=Nope",
    )
    assert res["count"] == "2 datasets"
    assert "source_id=Nope" in res["warn"]
    assert "source_id=ModelA" in res["url"] and "Nope" not in res["url"]
    assert any("Variable: tas" in c for c in res["chips"])


def test_facet_counts_are_or_within_and_across():
    res = run_probe(
        "const a = {exp: facet('experiment_id'), src: facet('source_id')};"
        "await tick('source_id', 'ModelB');"
        "out({...a, exp2: facet('experiment_id'), src2: facet('source_id'), count: count()});"
    )
    assert ["historical", 4, False] in res["exp"] and ["piControl", 1, False] in res[
        "exp"
    ]
    # picking ModelB: experiment counts narrow, source counts stay (OR within)
    assert ["historical", 2, False] in res["exp2"]
    assert ["ModelA", 3, False] in res["src2"] and ["ModelB", 2, True] in res["src2"]
    assert res["count"] == "2 datasets"


def test_multi_variable_dataset_and_vocabulary():
    res = run_probe(
        "const v = facet('variable_id');"
        "const q = facet('standard_name');"
        "const desc = $$(\"[data-facet='variable_id'] .facet-desc\").map((e) => e.textContent);"
        "await tick('variable_id', 'zos');"
        "out({v, q, desc, count: count()});"
    )
    names = [x[0] for x in res["v"]]
    assert {"so", "thetao", "zos", "tas"} <= set(names)
    assert "so,thetao,zos" not in names
    assert ["sea_surface_temperature", 1, False] in res["q"]
    assert "Sea surface height [m]" in res["desc"]
    assert res["count"] == "1 dataset"
    # a Variables-page link with the catalog's joined variable_id
    res = run_probe("out({count: count()});", query="?variable_id=so,thetao,zos")
    assert res["count"] == "1 dataset"


def test_search_matches_every_word_and_long_names():
    res = run_probe(
        "const s = $('#browse-search');"
        "s.value = 'sea surface'; s.dispatchEvent(new Event('input')); await sleep(600);"
        "const a = count();"
        "s.value = 'modelb historical'; s.dispatchEvent(new Event('input')); await sleep(600);"
        "out({a, b: count(), url: w.location.search});"
    )
    # "sea surface": tos (Sea Surface Temperature) and glorys (Sea surface height)
    assert res["a"] == "2 datasets"
    assert res["b"] == "2 datasets"
    assert "search=modelb+historical" in res["url"]


def test_basket_survives_filters_and_feeds_get_data():
    res = run_probe(
        "await tick('source_id', 'ModelB');"
        "$$('.tabulator-row')[0].click(); await sleep(300);"
        "const picked = $('#browse-bar').textContent;"
        "await tick('source_id', 'ModelB'); await tick('experiment_id', 'piControl');"
        "const bar = $('#browse-bar').textContent;"
        "const py = $('#gd-python pre').textContent;"
        "out({picked, bar, py, count: count(), urls: await urlsTxt()});"
    )
    assert "1 dataset picked" in res["picked"] and "1 dataset picked" in res["bar"]
    assert res["count"] == "1 dataset"  # piControl only: the pick is not shown
    key = re.search(r'"(CMIP6\.[^"]+)"', res["py"]).group(1)
    assert key.startswith("CMIP6.CMIP.INST.ModelB.historical")
    assert res["urls"] == "".join(
        f"https://cmip.example/download/{d['relative_path']}/{f}\n"
        for d in SUMMARY
        if d["dataset_id"] == key
        for f in FILES["files/cmip6.json"][key]
    )


def test_no_vocabulary_falls_back_to_variable_ids():
    res = run_probe(
        "out({v: facet('variable_id').map((x) => x[0]),"
        " q: $(\"[data-facet='standard_name']\").hidden});",
        data=_data(vocab=[]),
    )
    assert {"tas", "zos"} <= set(res["v"])
    assert res["q"] is True


def test_loads_under_production_csp():
    csp = re.search(r'Content-Security-Policy "([^"]+)"', CONF.read_text()).group(1)
    srv = _Server(_data(), csp=csp)
    try:
        dom, log = _dump(f"{srv.url}/{PAGE}")
    finally:
        srv.close()
    assert 'data-ready="true"' in dom
    assert "Content Security Policy" not in log
