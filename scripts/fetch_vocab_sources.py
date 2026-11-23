"""Fetch the official vocabularies and snapshot them in vocab/sources/.

Needs network; run where there is some (not a Slurm job). Fetches the pinned
versions, parses them (``whoi_cmip_reorg_code.vocab_sources``) and prints a
summary. Writes the CSV snapshots and SOURCES.yaml only with --write; commit
them, so the HPC build reads them without network.

Sources:
    cmip6-cmor-tables  PCMDI/cmip6-cmor-tables at --cmor-tag (Tables/*.json)
    ecmwf-param-db     ECMWF parameter database API (all, minus retired)
    cf-standard-names  CF standard name table, --cf-version

Example:
    uv run scripts/fetch_vocab_sources.py                # dry run
    uv run scripts/fetch_vocab_sources.py --write
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import tarfile
from pathlib import Path

import requests
import yaml

from whoi_cmip_reorg_code import vocab_sources as vs

CMOR_REPO = "PCMDI/cmip6-cmor-tables"
CMOR_URL = "https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz"
ECMWF_API = "https://codes.ecmwf.int/parameter-database/api/v1"
CF_URL = "https://cfconventions.org/Data/cf-standard-names/{v}/src/cf-standard-name-table.xml"
TIMEOUT = 120
UA = {"User-Agent": "whoi-cmip-reorg-code vocabulary fetch"}


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, timeout=TIMEOUT, headers=UA, **kw)
    r.raise_for_status()
    return r


def fetch_cmor(tag: str) -> tuple[list[dict], dict]:
    url = CMOR_URL.format(repo=CMOR_REPO, tag=tag)
    rows: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(_get(url).content), mode="r:gz") as tf:
        for m in tf.getmembers():
            name = Path(m.name)
            if not (
                m.isfile()
                and name.parent.name == "Tables"
                and name.suffix == ".json"
                and name.stem.startswith("CMIP6_")
                and name.stem.removeprefix("CMIP6_") not in vs.CMOR_NON_TABLES
            ):
                continue
            f = tf.extractfile(m)
            if f is not None:
                rows += vs.cmor_rows(json.load(f))
    return rows, {"url": url, "version": tag}


def fetch_ecmwf() -> tuple[list[dict], dict]:
    units = {u["id"]: u["name"] for u in _get(f"{ECMWF_API}/unit/?format=json").json()}
    params: list[dict] = []
    url: str | None = f"{ECMWF_API}/param/?format=json&limit=1000"
    while url:
        page = _get(url).json()
        params += page["results"]
        url = page.get("next")
    return vs.ecmwf_rows(params, units), {
        "url": f"{ECMWF_API}/param/",
        "version": dt.date.today().isoformat(),  # the database has no version
    }


def fetch_cf(version: str) -> tuple[list[dict], dict]:
    rows, v = vs.cf_rows(_get(CF_URL.format(v=version)).content)
    # record the numbered URL, not "current"
    return rows, {"url": CF_URL.format(v=v or version), "version": v or version}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="vocab/sources")
    ap.add_argument("--cmor-tag", default="6.9.33")
    ap.add_argument("--cf-version", default="current", help="e.g. 92, or current")
    ap.add_argument(
        "--source",
        action="append",
        choices=list(vs.SOURCE_FILES),
        help="only these (default: all)",
    )
    ap.add_argument("--write", action="store_true", help="write the snapshots")
    args = ap.parse_args(argv)

    fetchers = {
        "cmip6-cmor-tables": lambda: fetch_cmor(args.cmor_tag),
        "ecmwf-param-db": fetch_ecmwf,
        "cf-standard-names": lambda: fetch_cf(args.cf_version),
    }
    out = Path(args.out)
    meta_path = out / vs.SOURCES_YAML
    meta = yaml.safe_load(meta_path.read_text()) if meta_path.exists() else {}
    meta = meta or {}
    for name in args.source or list(fetchers):
        rows, info = fetchers[name]()
        fname, schema = vs.SOURCE_FILES[name]
        df = vs.frame(rows, schema)
        info = {**info, "fetched": dt.date.today().isoformat(), "rows": df.height}
        print(f"{name}: {df.height} rows, version {info['version']}", file=sys.stderr)
        if args.write:
            out.mkdir(parents=True, exist_ok=True)
            tmp = out / f".{fname}.tmp"
            df.write_csv(tmp)
            tmp.replace(out / fname)
            meta[name] = {**info, "file": fname}
    if args.write:
        meta_path.write_text(
            "# Snapshots of the official vocabularies (scripts/fetch_vocab_sources.py).\n"
            + yaml.safe_dump(meta, sort_keys=True)
        )
    else:
        print("dry run: nothing written (--write to write)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
