"""Store vocabulary: one definition per catalogued variable, for every product.

``whoi_cmip_vocabulary.csv`` (``catalog_schema.VOCAB_SCHEMA``) ships next to
the catalogs. Each row describes one NetCDF variable of one catalog key
(catalog, collection, product, table_id, level, variable_id). Definitions
come from the official vocabulary where there is one, else from the files:

- CMIP6: the CMOR tables (``table_id``, ``out_name``); the sampled file is
  only checked against them (``nc_check``).
- ERA5: the ECMWF parameter database, by the file's ``GRIB_paramId``, else
  its ``long_name`` (parameter name), else its NetCDF name (short name);
  lowest parameter id wins a tie (ERA5 uses the original ECMWF ids).
- Everything else: the NetCDF attributes of one sampled file per key and
  frequency (``sample_files`` and ``scan_samples``; ``scripts/scan_var_attrs.py``).

``cf_description`` comes from the CF standard name table. Nothing is fixed:
differences and gaps go to the build report.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code import ncinfo
from whoi_cmip_reorg_code.catalog_schema import (
    DEFAULT_DATA_ROOT,
    VOCAB_KEYS,
    VOCAB_SCHEMA,
)

# Catalog key of a vocabulary entry (VOCAB_KEYS without nc_name).
KEY_COLS = [k for k in VOCAB_KEYS if k != "nc_name"]
# Products with an official vocabulary other than CMOR.
ECMWF_PRODUCTS = ("era5",)

VAR_ATTRS_SCHEMA = {
    **{k: pl.String for k in KEY_COLS},
    "frequency": pl.String,
    "path": pl.String,  # as in the catalog
    "nc_status": pl.String,  # ok | error | missing_var
    "nc_error": pl.String,
    "nc_name": pl.String,
    "dimensions": pl.String,
    **{k: pl.String for k in ncinfo.VAR_ATTRS},
}


def catalog_keys(name: str, df: pl.DataFrame) -> pl.DataFrame:
    """Unique vocabulary keys of one catalog (CSV stem ``name``)."""
    return (
        df.with_columns(catalog=pl.lit(name))
        .select(KEY_COLS)
        .unique()
        .sort(KEY_COLS, nulls_last=True)
    )


def sample_files(name: str, df: pl.DataFrame) -> pl.DataFrame:
    """One file per key and frequency (the first path): what
    ``scan_samples`` opens. CMIP6 ``frequency`` is null (the table sets it)."""
    return (
        df.with_columns(catalog=pl.lit(name))
        .sort("path")
        .group_by([*KEY_COLS, "frequency"], maintain_order=True)
        .agg(pl.col("path").first())
        .sort([*KEY_COLS, "frequency"], nulls_last=True)
    )


def scan_samples(
    samples: pl.DataFrame, replace_prefix: tuple[str, str] | None = None
) -> pl.DataFrame:
    """Open each sample read-only and read its variables' attributes
    (``ncinfo.var_attrs``). CMIP6 reads ``variable_id`` only; other
    collections read every data variable. A file that fails to open gives one
    row with ``nc_status`` ``error``; a CMIP6 file without its variable,
    ``missing_var``. ``replace_prefix`` (old, new) maps catalog paths to a
    mount (``/proj/cmip6`` -> ``~/mnt/cmip6``); ``path`` keeps the catalog's."""
    import netCDF4  # after ncinfo set HDF5_USE_FILE_LOCKING

    rows = []
    for s in samples.iter_rows(named=True):
        base = {k: s[k] for k in [*KEY_COLS, "frequency", "path"]}
        path = s["path"]
        if replace_prefix and path.startswith(replace_prefix[0]):
            path = replace_prefix[1] + path.removeprefix(replace_prefix[0])
        empty = {k: None for k in VAR_ATTRS_SCHEMA if k not in base}
        try:
            ds = netCDF4.Dataset(path, mode="r")
        except ncinfo.NC_ERRORS as exc:
            rows.append(
                {**base, **empty, "nc_status": "error", "nc_error": f"{exc}"[:500]}
            )
            continue
        try:
            names = [s["variable_id"]] if s["collection"] == "cmip6" else None
            got = ncinfo.var_attrs(ds, names)
        except ncinfo.NC_ERRORS as exc:
            got = None
            rows.append(
                {**base, **empty, "nc_status": "error", "nc_error": f"{exc}"[:500]}
            )
        finally:
            ds.close()
        if got is None:
            continue
        if not got:
            rows.append({**base, **empty, "nc_status": "missing_var"})
        for v in got:
            rows.append({**base, **empty, **v, "nc_status": "ok"})
    return pl.DataFrame(rows, schema=VAR_ATTRS_SCHEMA, orient="row")


def _norm_units(u: str | None) -> str:
    return " ".join((u or "").split())


def _key(r: dict) -> tuple:
    return tuple(r[k] for k in KEY_COLS)


def _first(vals) -> str | None:
    return next((v for v in vals if v is not None), None)


def _cmor_index(cmor: pl.DataFrame) -> tuple[dict, list]:
    """{(table_id, out_name): entry row}, preferring entry == out_name; and
    the ambiguous (table_id, out_name) pairs."""
    idx: dict[tuple, dict] = {}
    seen: dict[tuple, list[str]] = {}
    for r in cmor.sort("table_id", "entry").iter_rows(named=True):
        k = (r["table_id"], r["out_name"])
        seen.setdefault(k, []).append(r["entry"])
        if k not in idx or r["entry"] == r["out_name"]:
            idx[k] = r
    amb = [
        {"table_id": t, "variable_id": v, "entries": e}
        for (t, v), e in seen.items()
        if len(e) > 1
    ]
    return idx, amb


def _ecmwf_lookup(ecmwf: pl.DataFrame):
    by_id: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    by_short: dict[str, dict] = {}
    for r in ecmwf.sort("param_id").iter_rows(named=True):
        by_id[r["param_id"]] = r
        if r["name"]:
            by_name.setdefault(r["name"].lower(), r)
        if r["shortname"]:
            by_short.setdefault(r["shortname"], r)

    def find(nc: dict) -> tuple[dict | None, str | None]:
        pid = nc.get("grib_param_id")
        if pid is not None:
            try:
                if (r := by_id.get(int(float(pid)))) is not None:
                    return r, "GRIB_paramId"
            except ValueError:
                pass
        ln = (nc.get("long_name") or "").lower()
        if ln and (r := by_name.get(ln)) is not None:
            return r, "long_name"
        if (r := by_short.get(nc["nc_name"])) is not None:
            return r, "short name"
        return None, None

    return find


def _cf_lookup(cf: pl.DataFrame):
    entries = {
        r["standard_name"]: r for r in cf.iter_rows(named=True) if not r["alias_of"]
    }
    aliases = {
        r["standard_name"]: r["alias_of"]
        for r in cf.iter_rows(named=True)
        if r["alias_of"]
    }

    def find(name: str | None) -> tuple[str | None, bool]:
        """(description, known)."""
        if not name:
            return None, True
        e = entries.get(name) or entries.get(aliases.get(name, ""))
        return (e["description"] if e else None), e is not None

    return find


def merge_samples(var_attrs: pl.DataFrame) -> tuple[dict, dict, list]:
    """Per key: {nc_name: merged attrs} (first non-null over frequencies) and
    the scan status. Also the conflicts: a key's variable whose
    ``long_name`` or ``units`` differ between its sampled files."""
    by_key: dict[tuple, dict[str, list[dict]]] = {}
    status: dict[tuple, list[dict]] = {}
    for r in var_attrs.iter_rows(named=True):
        status.setdefault(_key(r), []).append(r)
        if r["nc_status"] == "ok" and r["nc_name"]:
            by_key.setdefault(_key(r), {}).setdefault(r["nc_name"], []).append(r)
    merged: dict[tuple, dict[str, dict]] = {}
    conflicts = []
    attrs = ["dimensions", *ncinfo.VAR_ATTRS, "path"]
    for k, vars_ in by_key.items():
        merged[k] = {}
        for n, rs in vars_.items():
            merged[k][n] = {a: _first(r[a] for r in rs) for a in attrs}
            for a in ("long_name", "units"):
                vals = {r[a] for r in rs if r[a] is not None}
                if len(vals) > 1:
                    conflicts.append(
                        {**dict(zip(KEY_COLS, k)), "nc_name": n, a: sorted(vals)}
                    )
    return merged, status, conflicts


def build_vocabulary(
    keys: pl.DataFrame,
    var_attrs: pl.DataFrame,
    sources: dict[str, pl.DataFrame],
    meta: dict | None = None,
    data_root: str = DEFAULT_DATA_ROOT,
) -> tuple[pl.DataFrame, dict]:
    """(vocabulary, report) for ``keys`` (``catalog_keys`` of every catalog).

    ``var_attrs``: ``scan_samples`` output (may be empty: official
    definitions still apply, others are reported undefined). ``sources``,
    ``meta``: ``vocab_sources.read_sources``."""
    meta = meta or {}

    def version(src: str) -> str | None:
        v = (meta.get(src) or {}).get("version")
        return None if v is None else str(v)

    prefix = data_root.rstrip("/") + "/"
    cmor, amb = _cmor_index(sources["cmip6-cmor-tables"])
    ecmwf = _ecmwf_lookup(sources["ecmwf-param-db"])
    cf = _cf_lookup(sources["cf-standard-names"])
    merged, status, conflicts = merge_samples(var_attrs)

    rows: list[dict] = []
    report: dict = {
        "not_scanned": [],
        "scan_errors": [],
        "undefined": [],
        "nc_check": [],
        "ecmwf_unmatched": [],
        "unknown_standard_names": [],
        "cmor_ambiguous": [],
        "attr_conflicts": conflicts,
    }
    used_amb = set()
    matched_by: Counter = Counter()  # ERA5: how each parameter was found

    def rel(p: str | None) -> str | None:
        return None if p is None else p.removeprefix(prefix)

    for k in keys.iter_rows(named=True):
        kt = _key(k)
        st = status.get(kt, [])
        # opened at least one sample (an unreadable file checks nothing)
        scanned = any(s["nc_status"] != "error" for s in st)
        if not st:
            report["not_scanned"].append(k)
        if errs := [s for s in st if s["nc_status"] == "error"]:
            report["scan_errors"].append(
                {**k, "path": errs[0]["path"], "error": errs[0]["nc_error"]}
            )
        ncs = merged.get(kt, {})

        if k["collection"] == "cmip6":
            names = [k["variable_id"]]
        elif ncs:
            names = sorted(ncs)
        elif k["collection"] == "reanalysis":
            names = [k["variable_id"]]
        else:
            names = k["variable_id"].split(",")

        for n in names:
            nc = ncs.get(n)
            row = {c: None for c in VOCAB_SCHEMA} | dict(k) | {"nc_name": n}
            if nc:
                row.update(
                    {
                        a: nc[a]
                        for a in (
                            "long_name",
                            "standard_name",
                            "units",
                            "description",
                            "cell_methods",
                            "dimensions",
                        )
                    }
                )
                row["sample_path"] = rel(nc["path"])
                row["source"] = "netcdf"
            official = None
            if k["collection"] == "cmip6":
                c = cmor.get((k["table_id"], n))
                if c is not None:
                    if (k["table_id"], n) not in used_amb and any(
                        a["table_id"] == k["table_id"] and a["variable_id"] == n
                        for a in amb
                    ):
                        used_amb.add((k["table_id"], n))
                        report["cmor_ambiguous"].append(
                            {
                                "table_id": k["table_id"],
                                "variable_id": n,
                                "used": c["entry"],
                            }
                        )
                    official = {
                        "long_name": c["long_name"],
                        "standard_name": c["standard_name"],
                        "units": c["units"],
                        "description": c["comment"],
                        "cell_methods": c["cell_methods"],
                        "dimensions": c["dimensions"],
                        "realm": c["modeling_realm"],
                        "source": "cmip6-cmor-tables",
                        "source_version": version("cmip6-cmor-tables"),
                    }
            elif k["product"] in ECMWF_PRODUCTS and nc:
                p, how = ecmwf(nc)
                if p is not None:
                    matched_by[how] += 1
                    official = {
                        "long_name": p["name"],
                        "units": p["units"],
                        "description": p["description"],
                        "source": "ecmwf-param-db",
                        "source_version": version("ecmwf-param-db"),
                    }
                else:
                    report["ecmwf_unmatched"].append(
                        {**k, "nc_name": n, "long_name": nc["long_name"]}
                    )
            if official is not None:
                if not scanned:
                    check = "not_checked"
                elif nc is None:
                    check = "missing_in_file"
                elif _norm_units(nc["units"]) == _norm_units(official["units"]):
                    check = "match"
                else:
                    check = "units_differ"
                if check not in ("match", "not_checked"):
                    report["nc_check"].append(
                        {
                            **k,
                            "nc_name": n,
                            "nc_check": check,
                            "official_units": official["units"],
                            "file_units": nc["units"] if nc else None,
                            "sample_path": row["sample_path"],
                        }
                    )
                # official wins; the file fills what it leaves out
                row.update({a: v for a, v in official.items() if v is not None})
                row["nc_check"] = check
            row["cf_description"], known = cf(row["standard_name"])
            if not known:
                report["unknown_standard_names"].append(
                    {**k, "nc_name": n, "standard_name": row["standard_name"]}
                )
            if row["long_name"] is None and row["standard_name"] is None:
                report["undefined"].append({**k, "nc_name": n})
            rows.append(row)

    vocab = (
        pl.DataFrame(rows, schema=VOCAB_SCHEMA, orient="row")
        .unique(list(VOCAB_KEYS), keep="first", maintain_order=True)
        .sort(list(VOCAB_KEYS), nulls_last=True)
    )
    report["ecmwf_matched_by"] = dict(matched_by)
    report["counts"] = {
        "keys": keys.height,
        "rows": vocab.height,
        "by_source": dict(
            Counter(r if r is not None else "none" for r in vocab["source"])
        ),
        "by_nc_check": dict(Counter(r for r in vocab["nc_check"] if r is not None)),
        **{k: len(v) for k, v in report.items() if isinstance(v, list)},
    }
    return vocab, report


def missing_keys(keys: pl.DataFrame, vocab: pl.DataFrame) -> pl.DataFrame:
    """Catalog keys with no vocabulary row (null-safe join)."""
    return keys.join(
        vocab.select(KEY_COLS).unique(), on=KEY_COLS, how="anti", nulls_equal=True
    )


def write_csv(df: pl.DataFrame, path: str | os.PathLike) -> Path:
    """Write via a temp file, then rename."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    df.write_csv(tmp)
    tmp.replace(p)
    return p
