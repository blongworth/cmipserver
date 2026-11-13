"""Reanalysis catalog builder: ecgtools crawl + path-only parser.

The product manifest (``manifests/products/<product>.yaml``) drives the
parser: each group's ``path_template`` and ``file_pattern`` become one regex,
so the same file decides what is downloaded and how it is catalogued. Facets
(``product``, ``frequency``, ``level``, ``variable_id``) and ``version`` come
from the manifest, ``time_range`` from ``{year}`` or the digits of
``{date:...}`` (``1979-01-01`` -> ``19790101``; null for single-file groups),
size from ``stat``. ``variable_id`` is the group's alias for the on-disk name,
if it has one. Files no group matches are invalid assets.

The product root comes from ``catalogs.yaml`` (ERA5 lives at
``era5/reanalysis``, not ``reanalysis/era5``).
"""

from __future__ import annotations

import os
import re
import string
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog.io import INVALID_SCHEMA, from_builder
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA
from whoi_cmip_reorg_code.manifest import (
    DATE_DIRECTIVES,
    ProductGroup,
    ProductManifest,
)

# Deep enough for any mirrored remote path (e.g. Monthlies/2mSI-MO/f.nc).
CRAWL_DEPTH = 8


def _date_regex(spec: str) -> str:
    out, i = [], 0
    while i < len(spec):
        tok = spec[i : i + 2]
        if tok in DATE_DIRECTIVES:
            out.append(DATE_DIRECTIVES[tok])
            i += 2
        else:
            out.append(re.escape(spec[i]))
            i += 1
    return "".join(out)


def group_regex(g: ProductGroup) -> re.Pattern:
    """Regex for a group's local path (relative to the product root), with
    named groups ``variable`` and ``year`` or ``date``."""
    literal = {"remote_dir": g.remote_dir, "frequency": g.frequency, "level": g.level}
    tmpl = g.template.replace("{file}", g.file_pattern)
    out, seen = [], set()
    for text, field, spec, _ in string.Formatter().parse(tmpl):
        out.append(re.escape(text))
        if field is None:
            continue
        if field in literal:
            out.append(re.escape(literal[field]))
        elif field in seen:
            out.append(f"(?P={field})")
        else:
            seen.add(field)
            if field == "variable":
                alt = "|".join(re.escape(v) for v in g.variables)
                out.append(f"(?P<variable>{alt})")
            elif field == "date":
                out.append(f"(?P<date>{_date_regex(spec)})")
            else:
                out.append(r"(?P<year>\d{4})")
    return re.compile("".join(out))


def parse_reanalysis(
    path: str,
    product_root: str,
    spec: ProductManifest,
    collection: str = "reanalysis",
) -> dict:
    """ecgtools parsing function. ``product_root`` is the product's root
    from ``catalogs.yaml``."""
    from ecgtools.builder import INVALID_ASSET, TRACEBACK

    try:
        rel = Path(path).relative_to(product_root).as_posix()
    except ValueError:
        return {INVALID_ASSET: path, TRACEBACK: f"not under {product_root}"}
    for g in spec.groups:
        m = group_regex(g).fullmatch(rel)
        if m is None:
            continue
        year = m.groupdict().get("year")
        if year is not None and int(year) not in spec.group_years(g):
            continue
        date = m.groupdict().get("date")
        yrs = g.years or spec.years
        if date is not None and yrs and not yrs[0] <= int(date[:4]) <= yrs[1]:
            continue
        try:
            size = os.stat(path).st_size
        except OSError as exc:
            return {INVALID_ASSET: path, TRACEBACK: f"stat: {exc}"}
        return {
            "collection": collection,
            "path": path,
            "product": spec.product,
            "frequency": g.frequency,
            "level": g.level,
            "variable_id": g.variable_id(m["variable"]),
            "version": spec.version,
            "time_range": year or (date and re.sub(r"\D", "", date)),
            "size_bytes": size,
        }
    return {INVALID_ASSET: path, TRACEBACK: "matches no group in the product manifest"}


def build(
    product_root: str,
    spec: ProductManifest,
    jobs: int = -1,
    exclude_patterns: list[str] | None = None,
    exclude_paths: set[str] | None = None,
    collection: str = "reanalysis",
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Crawl ``product_root`` (the ``root`` in ``catalogs.yaml``). Returns
    (catalog, invalid), like ``cmip6.build``."""
    from ecgtools import Builder

    root = os.path.abspath(product_root)
    empty = pl.DataFrame(schema=CATALOG_SCHEMA), pl.DataFrame(schema=INVALID_SCHEMA)
    if not os.path.isdir(root):
        raise ValueError(f"no such directory {root}")
    b = Builder(
        paths=[root],
        depth=CRAWL_DEPTH,
        include_patterns=["*.nc"],
        exclude_patterns=exclude_patterns or [],
        joblib_parallel_kwargs={"n_jobs": jobs},
    )
    b.get_assets()
    if not b.assets:
        return empty
    b.parse(
        parsing_func=parse_reanalysis,
        parsing_func_kwargs={
            "product_root": root,
            "spec": spec,
            "collection": collection,
        },
    )
    return from_builder(b, exclude_paths)
