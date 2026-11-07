"""Shared fixtures.

``messy_store`` is a small CMIP6-like tree with the problems the audit scripts
must find. It's session-scoped: tests must treat it as read-only (copy it if
they need to change it).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

DRS = "CMIP/NCAR/CESM2/historical/r1i1p1f1"
V = "v20190308"
SRC = {
    "source_id": "CESM2",
    "experiment_id": "historical",
    "variant_label": "r1i1p1f1",
    "grid_label": "gn",
}


def fname(var: str, table: str, tr: str | None, source="CESM2", grid="gn") -> str:
    base = f"{var}_{table}_{source}_historical_r1i1p1f1_{grid}"
    return f"{base}_{tr}.nc" if tr else f"{base}.nc"


def write_nc(
    path: Path,
    var: str,
    table: str,
    tr: str | None,
    attrs: dict | None = None,
    data_var: str | None = None,
) -> Path:
    """Tiny synthetic CMIP-like NetCDF4 file. ``tr`` is a YYYYMM-YYYYMM range
    (monthly, noleap) or None for a fixed field. ``attrs`` override globals;
    ``data_var`` overrides the variable name inside the file."""
    data_var = data_var or var
    lat = np.array([-45.0, 45.0])
    lon = np.array([0.0, 180.0])
    gattrs = {
        **SRC,
        "variable_id": var,
        "table_id": table,
        "tracking_id": f"hdl:21.14100/{path.stem}",
        "license": "CC BY 4.0 (synthetic test data)",
        "title": "SYNTHETIC test data",
        **(attrs or {}),
    }
    if tr is None:
        ds = xr.Dataset(
            {data_var: (("lat", "lon"), np.ones((2, 2), "float32"), {"units": "m2"})},
            coords={"lat": lat, "lon": lon},
            attrs=gattrs,
        )
    else:
        a, b = tr.split("-")
        y0, m0, y1, m1 = int(a[:4]), int(a[4:6]), int(b[:4]), int(b[4:6])
        n = (y1 - y0) * 12 + (m1 - m0) + 1
        t = (y0 - 1850) * 365.0 + (m0 - 1) * 30.0 + np.arange(n) * 30.4 + 15
        ds = xr.Dataset(
            {
                data_var: (
                    ("time", "lat", "lon"),
                    np.full((n, 2, 2), 280.0, "float32"),
                    {"units": "K"},
                )
            },
            coords={
                "time": (
                    "time",
                    t,
                    {"units": "days since 1850-01-01", "calendar": "noleap"},
                ),
                "lat": lat,
                "lon": lon,
            },
            attrs=gattrs,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path, engine="netcdf4")
    return path


@dataclass
class MessyStore:
    root: Path
    paths: dict[str, Path] = field(default_factory=dict)

    def rel(self, key: str) -> str:
        return self.paths[key].relative_to(self.root).as_posix()


def build_messy_store(root: Path) -> MessyStore:
    s = MessyStore(root)
    d = root / DRS
    p = s.paths

    def drs(table: str, var: str, version: str = V) -> Path:
        return d / table / var / "gn" / version

    # good, contiguous monthly series in three files
    for i, tr in enumerate(["185001-189912", "190001-194912", "195001-201412"]):
        p[f"good_{i}"] = write_nc(
            drs("Amon", "tas") / fname("tas", "Amon", tr), "tas", "Amon", tr
        )
    # time gap: 1900-1949 missing
    for i, tr in enumerate(["185001-189912", "195001-201412"]):
        p[f"gap_{i}"] = write_nc(
            drs("Amon", "pr") / fname("pr", "Amon", tr), "pr", "Amon", tr
        )
    # overlap: 1890-1899 in both
    for i, tr in enumerate(["185001-189912", "189001-194912"]):
        p[f"overlap_{i}"] = write_nc(
            drs("Omon", "tos") / fname("tos", "Omon", tr), "tos", "Omon", tr
        )
    # two versions of one dataset
    tr = "185001-201412"
    p["v1"] = write_nc(drs("Amon", "psl") / fname("psl", "Amon", tr), "psl", "Amon", tr)
    p["v2"] = write_nc(
        drs("Amon", "psl", "v20200101") / fname("psl", "Amon", tr), "psl", "Amon", tr
    )
    # fixed field
    p["fx"] = write_nc(
        d / "fx" / "areacella" / "gn" / V / fname("areacella", "fx", None),
        "areacella",
        "fx",
        None,
    )
    # zero-byte file
    p["empty"] = drs("Amon", "ts") / fname("ts", "Amon", tr)
    p["empty"].parent.mkdir(parents=True)
    p["empty"].write_bytes(b"")
    # truncated: a valid file cut in half
    p["truncated"] = write_nc(
        drs("Amon", "hfls") / fname("hfls", "Amon", tr), "hfls", "Amon", tr
    )
    data = p["truncated"].read_bytes()
    p["truncated"].write_bytes(data[: len(data) // 2])
    # text file named .nc (e.g. a saved HTML error page)
    p["text"] = drs("Amon", "hfss") / fname("hfss", "Amon", tr)
    p["text"].parent.mkdir(parents=True)
    p["text"].write_text("<html><body>503 Service Unavailable</body></html>\n")
    # filename says huss, attrs and variable say hurs
    p["mismatch"] = write_nc(
        drs("Amon", "huss") / fname("huss", "Amon", tr),
        "huss",
        "Amon",
        tr,
        attrs={"variable_id": "hurs"},
        data_var="hurs",
    )
    # non-DRS layout (valid file)
    flat = root / "misc" / "old_downloads"
    p["flat"] = write_nc(
        flat / fname("tas", "Amon", "185001-194912", "GFDL-ESM4", "gr1"),
        "tas",
        "Amon",
        "185001-194912",
        attrs={"source_id": "GFDL-ESM4", "grid_label": "gr1"},
    )
    # broken symlink
    p["broken_link"] = root / "misc" / "broken.nc"
    p["broken_link"].symlink_to(root / "misc" / "does_not_exist.nc")
    # non-NetCDF file
    p["readme"] = root / "misc" / "README.txt"
    p["readme"].write_text("notes\n")
    return s


@pytest.fixture(scope="session")
def messy_store(tmp_path_factory) -> MessyStore:
    """Read-only messy tree; see ``build_messy_store``."""
    return build_messy_store(tmp_path_factory.mktemp("messy") / "cmip6")


@pytest.fixture
def messy_copy(messy_store, tmp_path) -> MessyStore:
    """A private, writable copy of ``messy_store``."""
    root = tmp_path / "cmip6"
    shutil.copytree(messy_store.root, root, symlinks=True)
    return MessyStore(
        root,
        {
            k: root / v.relative_to(messy_store.root)
            for k, v in messy_store.paths.items()
        },
    )


@pytest.fixture(scope="session")
def messy_scan(messy_store, tmp_path_factory) -> Path:
    """``scan`` output dir (holdings_all.parquet) for ``messy_store``."""
    from whoi_cmip_reorg_code.scan import scan

    out = tmp_path_factory.mktemp("messy_scan")
    scan(messy_store.root, out)
    return out


# --- fake ESGF index ------------------------------------------------------------

ESGF_URL = "https://esgf.example.invalid/esg-search/search"
DATA_URL = "https://data.example.invalid/thredds/fileServer"


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeIndex:
    """In-memory stand-in for the ESGF search API (classic Solr JSON).

    Docs are shaped like real esg-search records (list-valued facets,
    ``url`` as ``"<url>|<mime>|<service>"``). Matching: ``type`` plus every
    facet param must be in the doc's list. Pages by ``offset``/``limit``.
    """

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs
        self.calls: list[dict] = []
        self.payloads: dict[str, bytes] = {}

    def get(self, url: str, params: dict) -> dict:
        self.calls.append(dict(params))
        hits = [
            d
            for d in self.docs
            if d["type"] == params["type"]
            and all(
                params[k] in d[k]
                for k in (
                    "source_id",
                    "experiment_id",
                    "member_id",
                    "table_id",
                    "variable_id",
                    "grid_label",
                )
            )
        ]
        off, lim = int(params["offset"]), int(params["limit"])
        return {
            "response": {
                "numFound": len(hits),
                "start": off,
                "docs": hits[off : off + lim],
            }
        }


def esgf_docs(
    source: str,
    table: str,
    var: str,
    version: str,
    files: list[tuple[str, int, str]],
    grid: str = "gn",
    latest: bool = True,
    retracted: bool = False,
    institution: str = "NCAR",
) -> list[dict]:
    """One Dataset doc plus File docs (name, size, sha256) for one version."""
    v = version.removeprefix("v")
    facets = {
        "activity_id": ["CMIP"],
        "institution_id": [institution],
        "source_id": [source],
        "experiment_id": ["historical"],
        "member_id": ["r1i1p1f1"],
        "table_id": [table],
        "variable_id": [var],
        "grid_label": [grid],
    }
    master = (
        f"CMIP6.CMIP.{institution}.{source}.historical.r1i1p1f1.{table}.{var}.{grid}"
    )
    inst = f"{master}.{version}"
    node = "data.example.invalid"
    common = {
        "latest": latest,
        "retracted": retracted,
        "replica": False,
        "data_node": node,
        **facets,
    }
    docs = [
        {
            "type": "Dataset",
            "id": f"{inst}|{node}",
            "instance_id": inst,
            "master_id": master,
            "version": v,
            **common,
        }
    ]
    for name, size, digest in files:
        drs = inst.replace(".", "/").removeprefix("CMIP6/")
        docs.append(
            {
                "type": "File",
                "id": f"{inst}.{name}|{node}",
                "instance_id": f"{inst}.{name}",
                "dataset_id": f"{inst}|{node}",
                "title": name,
                "size": size,
                "checksum": [digest],
                "checksum_type": ["SHA256"],
                "tracking_id": [f"hdl:21.14100/{name}"],
                "url": [
                    f"{DATA_URL}/{drs}/{name}|application/netcdf|HTTPServer",
                    f"gsiftp://{node}/{name}|application/gridftp|GridFTP",
                ],
                **common,
            }
        )
    return docs


@pytest.fixture(scope="session")
def fake_esgf(messy_store) -> FakeIndex:
    """ESGF view of messy_store:

    - tas: 3 files, sizes and checksums match           -> match
    - pr: ESGF has the 1900-1949 file we lack            -> missing locally
    - psl: v20200101 is latest                           -> v20190308 superseded
    - ts (empty) and hfls (truncated): full-size records -> size_mismatch
    - tos: version retracted
    - GFDL-ESM4 tas (flat dir, no version): v20190726    -> match by name+size
    - areacella, hfss, huss: not on ESGF
    Replacement payloads (bytes served for download tests) are in ``payloads``.
    """
    p = messy_store.paths

    def real(key):
        return (p[key].name, p[key].stat().st_size, sha256(p[key]))

    docs = []
    docs += esgf_docs(
        "CESM2",
        "Amon",
        "tas",
        "v20190308",
        [real("good_0"), real("good_1"), real("good_2")],
    )
    mid = fname("pr", "Amon", "190001-194912")
    idx = FakeIndex([])
    good_mid = p["good_1"].read_bytes()  # any valid NetCDF bytes will do
    idx.payloads[mid] = good_mid
    docs += esgf_docs(
        "CESM2",
        "Amon",
        "pr",
        "v20190308",
        [real("gap_0"), (mid, len(good_mid), _sha(good_mid)), real("gap_1")],
    )
    docs += esgf_docs("CESM2", "Amon", "psl", "v20190308", [real("v1")], latest=False)
    docs += esgf_docs("CESM2", "Amon", "psl", "v20200101", [real("v2")])
    for key in ("empty", "truncated"):
        payload = p["good_0"].read_bytes()
        idx.payloads[p[key].name] = payload
        var = p[key].name.split("_")[0]
        docs += esgf_docs(
            "CESM2",
            "Amon",
            var,
            "v20190308",
            [(p[key].name, len(payload), _sha(payload))],
        )
    docs += esgf_docs(
        "CESM2",
        "Omon",
        "tos",
        "v20190308",
        [real("overlap_0"), real("overlap_1")],
        retracted=True,
    )
    docs += esgf_docs(
        "GFDL-ESM4",
        "Amon",
        "tas",
        "v20190726",
        [real("flat")],
        grid="gr1",
        institution="NOAA-GFDL",
    )
    idx.docs = docs
    return idx


def _sha(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="session")
def audit(messy_store, messy_scan, fake_esgf, tmp_path_factory) -> Path:
    """Run check_nc, check_esgf (fake index), find_gaps and plan_repairs on
    messy_store. Returns the output dir (repairs.yaml inside)."""
    import check_esgf
    import check_nc
    import find_gaps
    import plan_repairs

    out = tmp_path_factory.mktemp("audit")
    scan = str(messy_scan)
    check_nc.main(["--scan", scan, "--out", str(out / "ncinfo"), "--shard", "all"])
    check_esgf.main(
        [
            "--scan",
            scan,
            "--out",
            str(out / "esgf"),
            "--index-url",
            ESGF_URL,
            "--min-interval",
            "0",
        ],
        fetcher=fake_esgf,
    )
    find_gaps.main(["--scan", scan, "--out", str(out / "gaps")])
    plan_repairs.main(
        [
            "--esgf",
            str(out / "esgf"),
            "--ncinfo",
            str(out / "ncinfo"),
            "--gaps",
            str(out / "gaps"),
            "--out",
            str(out / "repairs.yaml"),
        ]
    )
    return out
