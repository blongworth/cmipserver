"""Reanalysis catalog builder: ecgtools crawl + path-only parser.

The product manifest (``manifests/products/<product>.yaml``) drives the
parser: each group's ``path_template`` and ``file_pattern`` become one regex,
so the same file decides what is downloaded and how it is catalogued. Facets
(``product``, ``frequency``, ``level``, ``variable_id``) and ``version`` come
from the manifest, ``time_range`` from ``{year}`` (null for single-file
groups), size from ``stat``. Files no group matches are invalid assets.
"""

from __future__ import annotations

import os
import re
import string
from pathlib import Path

import polars as pl

from whoi_cmip_reorg_code.catalog.io import INVALID_SCHEMA, from_builder
from whoi_cmip_reorg_code.catalog_schema import CATALOG_SCHEMA
from whoi_cmip_reorg_code.manifest import ProductGroup, ProductManifest

# Deep enough for any mirrored remote path (e.g. Monthlies/2mSI-MO/f.nc).
CRAWL_DEPTH = 8


def group_regex(g: ProductGroup) -> re.Pattern:
    """Regex for a group's local path (relative to ``reanalysis/<product>``),
    with named groups ``variable`` and ``year``."""
    literal = {"remote_dir": g.remote_dir, "frequency": g.frequency, "level": g.level}
    tmpl = g.template.replace("{file}", g.file_pattern)
    out, seen = [], set()
    for text, field, _, _ in string.Formatter().parse(tmpl):
        out.append(re.escape(text))
        if field is None:
            continue
        if field in literal:
            out.append(re.escape(literal[field]))
        elif field in seen:
            out.append(f"(?P={field})")
        else:
            seen.add(field)
            alt = "|".join(re.escape(v) for v in g.variables)
            out.append(
                f"(?P<variable>{alt})" if field == "variable" else r"(?P<year>\d{4})"
            )
    return re.compile("".join(out))


def parse_reanalysis(path: str, product_root: str, spec: ProductManifest) -> dict:
    """ecgtools parsing function. ``product_root`` is ``.../reanalysis/<product>``."""
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
        try:
            size = os.stat(path).st_size
        except OSError as exc:
            return {INVALID_ASSET: path, TRACEBACK: f"stat: {exc}"}
        return {
            "collection": "reanalysis",
            "path": path,
            "product": spec.product,
            "frequency": g.frequency,
            "level": g.level,
            "variable_id": m["variable"],
            "version": spec.version,
            "time_range": year,
            "size_bytes": size,
        }
    return {INVALID_ASSET: path, TRACEBACK: "matches no group in the product manifest"}


def build(
    product_root: str,
    spec: ProductManifest,
    jobs: int = -1,
    exclude_patterns: list[str] | None = None,
    exclude_paths: set[str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Crawl ``product_root`` (``<store>/reanalysis/<product>``). Returns
    (catalog, invalid), like ``cmip6.build``."""
    from ecgtools import Builder

    root = os.path.abspath(product_root)
    if Path(root).name != spec.product:
        raise ValueError(f"root {root} is not the {spec.product} product dir")
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
        parsing_func_kwargs={"product_root": root, "spec": spec},
    )
    return from_builder(b, exclude_paths)
