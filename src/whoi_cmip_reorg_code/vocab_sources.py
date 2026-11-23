"""Readers for the official vocabularies behind the store vocabulary.

Each reader turns one upstream format into a polars frame of the fields the
vocabulary needs. ``scripts/fetch_vocab_sources.py`` fetches the pinned
versions and writes the frames as CSV snapshots in ``vocab/sources/`` (with
``SOURCES.yaml``: URL, version, fetch date); the build reads only those, so
it needs no network.

- CMIP6 CMOR tables (PCMDI ``cmip6-cmor-tables``): one row per
  ``variable_entry`` per table.
- ECMWF parameter database: one row per parameter (ERA5).
- CF standard name table: one row per standard name, plus one per alias.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import polars as pl
import yaml

CMOR_SCHEMA = {
    "table_id": pl.String,
    "entry": pl.String,  # key in variable_entry (may differ from out_name)
    "out_name": pl.String,  # the CMIP6 variable_id
    "long_name": pl.String,
    "standard_name": pl.String,
    "units": pl.String,
    "comment": pl.String,
    "cell_methods": pl.String,
    "dimensions": pl.String,
    "modeling_realm": pl.String,
    "frequency": pl.String,
}
ECMWF_SCHEMA = {
    "param_id": pl.Int64,
    "shortname": pl.String,
    "name": pl.String,
    "units": pl.String,
    "description": pl.String,
}
CF_SCHEMA = {
    "standard_name": pl.String,
    "canonical_units": pl.String,
    "description": pl.String,
    "alias_of": pl.String,  # set for aliases (old names): the current name
}

SOURCE_FILES = {
    "cmip6-cmor-tables": ("cmip6_cmor_tables.csv", CMOR_SCHEMA),
    "ecmwf-param-db": ("ecmwf_params.csv", ECMWF_SCHEMA),
    "cf-standard-names": ("cf_standard_names.csv", CF_SCHEMA),
}
SOURCES_YAML = "SOURCES.yaml"

# CMOR table files that hold no variables.
CMOR_NON_TABLES = ("CV", "coordinate", "formula_terms", "grids", "input_example")


def _blank(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _plain(text: str | None) -> str | None:
    """HTML from the ECMWF database as one line of plain text."""
    if text is None:
        return None
    t = re.sub(r"<br\s*/?>", " ", text)
    t = re.sub(r"<[^>]+>", "", t)
    return _blank(" ".join(html.unescape(t).split()))


def cmor_rows(table: dict) -> list[dict]:
    """Rows of one CMOR table JSON (``CMIP6_Amon.json``); [] if it has no
    ``variable_entry`` (CV, coordinate, ...)."""
    entries = table.get("variable_entry")
    if not entries:
        return []
    table_id = table["Header"]["table_id"].removeprefix("Table").strip()
    out = []
    for entry, e in entries.items():
        out.append(
            {
                "table_id": table_id,
                "entry": entry,
                "out_name": _blank(e.get("out_name")) or entry,
                "long_name": _blank(e.get("long_name")),
                "standard_name": _blank(e.get("standard_name")),
                "units": _blank(e.get("units")),
                "comment": _blank(e.get("comment")),
                "cell_methods": _blank(e.get("cell_methods")),
                "dimensions": _blank(e.get("dimensions")),
                "modeling_realm": _blank(e.get("modeling_realm")),
                "frequency": _blank(e.get("frequency")),
            }
        )
    return out


def ecmwf_rows(params: list[dict], units: dict[int, str]) -> list[dict]:
    """Rows from the parameter database API (``/api/v1/param/`` results and
    ``/api/v1/unit/`` as {id: name}). Retired parameters are left out."""
    return [
        {
            "param_id": int(p["id"]),
            "shortname": _blank(p.get("shortname")),
            "name": _blank(p.get("name")),
            "units": units.get(p.get("unit_id")),
            "description": _plain(p.get("description")),
        }
        for p in params
        if not p.get("retired")
    ]


def cf_rows(xml_text: str | bytes) -> tuple[list[dict], str | None]:
    """(rows, version_number) from the CF standard name table XML."""
    root = ET.fromstring(xml_text)
    version = _blank(root.findtext("version_number"))
    rows = [
        {
            "standard_name": e.get("id"),
            "canonical_units": _blank(e.findtext("canonical_units")),
            "description": _blank(" ".join((e.findtext("description") or "").split())),
            "alias_of": None,
        }
        for e in root.iter("entry")
    ]
    rows += [
        {
            "standard_name": a.get("id"),
            "canonical_units": None,
            "description": None,
            "alias_of": _blank(a.findtext("entry_id")),
        }
        for a in root.iter("alias")
    ]
    return rows, version


def frame(rows: list[dict], schema: dict) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=schema, orient="row")


def read_sources(src_dir: str | Path) -> tuple[dict[str, pl.DataFrame], dict]:
    """({source: frame}, SOURCES.yaml contents) from a snapshot directory.
    A source whose CSV is missing gets an empty frame."""
    d = Path(src_dir)
    meta_path = d / SOURCES_YAML
    meta = yaml.safe_load(meta_path.read_text()) if meta_path.exists() else {}
    frames = {}
    for name, (fname, schema) in SOURCE_FILES.items():
        p = d / fname
        frames[name] = (
            pl.read_csv(p, schema=schema) if p.exists() else pl.DataFrame(schema=schema)
        )
    return frames, meta or {}
