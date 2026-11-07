"""ESGF file records (checksums, sizes, versions, retractions) vs the inventory.

Uses the ESGF search API in its classic Solr JSON form (``esg-search/search``
with ``format=application/solr+json``). Two queries per local dataset (all
versions): ``type=Dataset`` for versions/latest/retracted, ``type=File`` for
file names, sizes, checksums and download URLs.

The HTTP layer is a ``Fetcher`` (``get(url, params) -> dict``) so tests use a
fake index and never touch the network. ``CachedFetcher`` keeps every response
in a parquet file, so re-runs only query what's new, and spaces requests by
``min_interval`` seconds.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, Self

import polars as pl

QUERY_FACETS = (
    "source_id",
    "experiment_id",
    "member_id",
    "table_id",
    "variable_id",
    "grid_label",
)
PAGE = 500

ESGF_DATASETS_SCHEMA = {
    **{f: pl.String for f in QUERY_FACETS},
    "activity_id": pl.String,
    "institution_id": pl.String,
    "master_id": pl.String,
    "version": pl.String,
    "latest": pl.Boolean,
    "retracted": pl.Boolean,
    "n_replicas": pl.Int64,
    "data_nodes": pl.String,
}
ESGF_FILES_SCHEMA = {
    **{f: pl.String for f in QUERY_FACETS},
    "activity_id": pl.String,
    "institution_id": pl.String,
    "version": pl.String,
    "name": pl.String,
    "size_bytes": pl.Int64,
    "checksum": pl.String,
    "checksum_type": pl.String,
    "tracking_id": pl.String,
    "url": pl.String,
    "data_node": pl.String,
    "n_replicas": pl.Int64,
    "latest": pl.Boolean,
    "retracted": pl.Boolean,
}


class Fetcher(Protocol):
    def get(self, url: str, params: dict) -> dict: ...


class RequestsFetcher:
    """Plain HTTP GET with retries. Generic User-Agent, no credentials."""

    def __init__(self, timeout: float = 120.0, retries: int = 3) -> None:
        import requests

        self.session = requests.Session()
        self.session.headers["User-Agent"] = "whoi-cmip-reorg-code (esgf inventory)"
        self.timeout = timeout
        self.retries = retries

    def get(self, url: str, params: dict) -> dict:
        import requests

        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code < 500:
                    r.raise_for_status()
                    return r.json()
            except requests.ConnectionError, requests.Timeout:
                if attempt == self.retries - 1:
                    raise
            time.sleep(2**attempt * 5)
        r.raise_for_status()
        return r.json()


CACHE_SCHEMA = {"key": pl.String, "fetched_at": pl.String, "response": pl.String}


class CachedFetcher:
    """Wrap a fetcher with a parquet response cache and a minimum spacing
    between live requests. Call ``save()`` (or use as a context manager)."""

    def __init__(
        self,
        inner: Fetcher,
        cache_path: str | os.PathLike | None,
        min_interval: float = 1.0,
        refresh: bool = False,
        save_every: int = 50,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self.cache_path = Path(cache_path) if cache_path else None
        self.min_interval = min_interval
        self.refresh = refresh
        self.save_every = save_every
        self.clock, self.sleep = clock, sleep
        self._last: float | None = None
        self._new: list[dict] = []
        self.cache: dict[str, str] = {}
        self.n_live = 0
        if self.cache_path and self.cache_path.exists():
            df = pl.read_parquet(self.cache_path)
            self.cache = dict(zip(df["key"], df["response"]))

    @staticmethod
    def key(url: str, params: dict) -> str:
        return json.dumps([url, sorted(params.items())], default=str)

    def get(self, url: str, params: dict) -> dict:
        k = self.key(url, params)
        if not self.refresh and k in self.cache:
            return json.loads(self.cache[k])
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        resp = self.inner.get(url, params)
        self._last = self.clock()
        self.n_live += 1
        s = json.dumps(resp)
        self.cache[k] = s
        self._new.append(
            {"key": k, "fetched_at": datetime.now(UTC).isoformat(), "response": s}
        )
        if len(self._new) >= self.save_every:
            self.save()
        return resp

    def save(self) -> None:
        if not self.cache_path or not self._new:
            return
        new = pl.DataFrame(self._new, schema=CACHE_SCHEMA)
        if self.cache_path.exists():
            old = pl.read_parquet(self.cache_path)
            new = pl.concat([old, new]).unique("key", keep="last", maintain_order=True)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        new.write_parquet(tmp)
        tmp.replace(self.cache_path)
        self._new = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.save()


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _vnorm(v: str | None) -> str | None:
    """ESGF versions are ``20190308``; DRS dirs are ``v20190308``."""
    if v is None:
        return None
    v = str(v)
    return v if v.startswith("v") else f"v{v}"


def search_all(fetcher: Fetcher, url: str, params: dict) -> list[dict]:
    """All docs for a query, paging by ``offset``."""
    docs: list[dict] = []
    offset = 0
    while True:
        p = {
            **params,
            "format": "application/solr+json",
            "limit": PAGE,
            "offset": offset,
        }
        resp = fetcher.get(url, p)["response"]
        page = resp.get("docs", [])
        docs.extend(page)
        offset += len(page)
        if not page or offset >= int(resp.get("numFound", 0)):
            return docs


def _base_params(facets: dict, typ: str) -> dict:
    return {
        "type": typ,
        "project": "CMIP6",
        "distrib": "true",
        **{k: facets[k] for k in QUERY_FACETS},
    }


def dataset_records(facets: dict, docs: list[dict]) -> list[dict]:
    """Collapse Dataset docs (one per replica) to one row per version."""
    by_ver: dict[str, dict] = {}
    for d in docs:
        inst = d.get("instance_id") or d.get("id", "").split("|")[0]
        ver = _vnorm(d.get("version") or inst.rsplit(".", 1)[-1])
        r = by_ver.setdefault(
            ver,
            {
                **{f: facets[f] for f in QUERY_FACETS},
                "activity_id": _first(d.get("activity_id")),
                "institution_id": _first(d.get("institution_id")),
                "master_id": d.get("master_id") or inst.rsplit(".", 1)[0],
                "version": ver,
                "latest": False,
                "retracted": False,
                "n_replicas": 0,
                "data_nodes": set(),
            },
        )
        r["latest"] |= bool(d.get("latest", False))
        r["retracted"] |= bool(d.get("retracted", False))
        r["n_replicas"] += 1
        if d.get("data_node"):
            r["data_nodes"].add(d["data_node"])
    for r in by_ver.values():
        r["data_nodes"] = ",".join(sorted(r["data_nodes"]))
    return list(by_ver.values())


def _http_url(urls) -> str | None:
    for u in urls or []:
        parts = u.split("|")
        if len(parts) >= 3 and parts[2] == "HTTPServer":
            return parts[0]
    return None


def file_records(facets: dict, docs: list[dict]) -> list[dict]:
    """Collapse File docs (one per replica) to one row per (version, name).
    The original (``replica: false``) supplies checksum and URL when there is
    one; otherwise the first replica with an HTTP URL."""
    out: dict[tuple, dict] = {}
    for d in sorted(docs, key=lambda d: bool(d.get("replica", False))):
        ds_id = (d.get("dataset_id") or "").split("|")[0]
        ver = _vnorm(d.get("version") or (ds_id.rsplit(".", 1)[-1] if ds_id else None))
        name = d.get("title") or _first(d.get("filename"))
        k = (ver, name)
        url = _http_url(d.get("url"))
        if k in out:
            out[k]["n_replicas"] += 1
            if out[k]["url"] is None and url:
                out[k]["url"] = url
                out[k]["data_node"] = d.get("data_node")
            out[k]["retracted"] |= bool(d.get("retracted", False))
            continue
        size = d.get("size")
        out[k] = {
            **{f: facets[f] for f in QUERY_FACETS},
            "activity_id": _first(d.get("activity_id")),
            "institution_id": _first(d.get("institution_id")),
            "version": ver,
            "name": name,
            "size_bytes": int(size) if size is not None else None,
            "checksum": _first(d.get("checksum")),
            "checksum_type": _first(d.get("checksum_type")),
            "tracking_id": _first(d.get("tracking_id")),
            "url": url,
            "data_node": d.get("data_node"),
            "n_replicas": 1,
            "latest": bool(d.get("latest", False)),
            "retracted": bool(d.get("retracted", False)),
        }
    return list(out.values())


def query_datasets(
    fetcher: Fetcher,
    url: str,
    datasets: Iterable[dict],
    progress: Callable[[int, dict], None] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Query ESGF for each facet dict in ``datasets``.

    Returns (esgf_datasets, esgf_files, query_errors). A failed query is
    recorded in ``query_errors`` and the run carries on.
    """
    import requests

    ds_rows, f_rows, errs = [], [], []
    for i, facets in enumerate(datasets):
        if progress:
            progress(i, facets)
        try:
            ds_docs = search_all(fetcher, url, _base_params(facets, "Dataset"))
            f_docs = (
                search_all(fetcher, url, _base_params(facets, "File"))
                if ds_docs
                else []
            )
        except (requests.RequestException, KeyError, ValueError) as exc:
            errs.append(
                {
                    **{f: facets[f] for f in QUERY_FACETS},
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        ds_rows += dataset_records(facets, ds_docs)
        f_rows += file_records(facets, f_docs)
    return (
        pl.DataFrame(ds_rows, schema=ESGF_DATASETS_SCHEMA, orient="row"),
        pl.DataFrame(f_rows, schema=ESGF_FILES_SCHEMA, orient="row"),
        pl.DataFrame(
            errs, schema={**{f: pl.String for f in QUERY_FACETS}, "error": pl.String}
        ),
    )


def local_datasets(holdings: pl.LazyFrame) -> pl.DataFrame:
    """Distinct facet combinations of real, parsed ``.nc`` files."""
    return (
        holdings.filter(pl.col("file_type") == "file", pl.col("facet_parse_ok"))
        .select(QUERY_FACETS)
        .unique()
        .sort(QUERY_FACETS)
        .collect()
    )


ESGF_STATUSES = (
    "match",
    "size_mismatch",
    "not_in_esgf_version",
    "version_not_on_esgf",
    "dataset_not_on_esgf",
    "not_queried",
)


def compare(
    holdings: pl.LazyFrame,
    esgf_datasets: pl.DataFrame,
    esgf_files: pl.DataFrame,
    queried: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Join local files with ESGF records.

    Returns (local, missing_local):

    ``local``: one row per local file (path, rel_path, facets, version, name,
    size_bytes) plus ``esgf_status``, ``esgf_version`` (the version matched,
    which for files outside a version dir is the one whose size matches, else
    the latest), ``esgf_size``, ``esgf_checksum``, ``esgf_checksum_type``,
    ``esgf_url``, ``latest_version``, ``superseded``, ``retracted``.

    ``missing_local``: ESGF files in a version held locally (DRS version dir)
    that are absent from that local directory set.

    ``queried``: the facet combinations that were queried; anything else is
    ``not_queried`` (e.g. a pilot run on one source_id).
    """
    local = (
        holdings.filter(pl.col("file_type") == "file", pl.col("facet_parse_ok"))
        .select("path", "rel_path", *QUERY_FACETS, "version", "name", "size_bytes")
        .collect()
    )
    ds = esgf_datasets
    latest = (
        ds.sort("version")
        .group_by(QUERY_FACETS)
        .agg(
            latest_version=pl.col("version").filter(pl.col("latest")).max(),
            max_version=pl.col("version").max(),
            esgf_versions=pl.col("version"),
        )
        .with_columns(latest_version=pl.coalesce("latest_version", "max_version"))
        .drop("max_version")
    )
    ver_flags = ds.select(*QUERY_FACETS, "version", "retracted")
    ef = esgf_files.select(
        *QUERY_FACETS,
        pl.col("version").alias("esgf_version"),
        "name",
        pl.col("size_bytes").alias("esgf_size"),
        pl.col("checksum").alias("esgf_checksum"),
        pl.col("checksum_type").alias("esgf_checksum_type"),
        pl.col("url").alias("esgf_url"),
    )

    # Candidate ESGF records per local file: same name, and same version when
    # the local version is known. Prefer a size match, then the latest version.
    cand = (
        local.join(ef, on=[*QUERY_FACETS, "name"], how="inner")
        .filter(
            pl.col("version").is_null() | (pl.col("version") == pl.col("esgf_version"))
        )
        .join(latest.select(*QUERY_FACETS, "latest_version"), on=QUERY_FACETS)
        .with_columns(
            _rank=pl.when(pl.col("esgf_size") == pl.col("size_bytes"))
            .then(0)
            .when(pl.col("esgf_version") == pl.col("latest_version"))
            .then(1)
            .otherwise(2)
        )
        .sort("_rank", "esgf_version", descending=[False, True])
        .unique("path", keep="first", maintain_order=True)
        .select("path", *ef.columns[len(QUERY_FACETS) :])
        .drop("name")
    )
    queried = (
        (local.select(QUERY_FACETS).unique() if queried is None else queried)
        .select(QUERY_FACETS)
        .unique()
        .with_columns(_q=pl.lit(True))
    )
    best = (
        local.join(cand, on="path", how="left")
        .join(latest, on=QUERY_FACETS, how="left")
        .join(
            ver_flags.rename({"version": "esgf_version"}),
            on=[*QUERY_FACETS, "esgf_version"],
            how="left",
        )
        .join(queried, on=QUERY_FACETS, how="left")
    )

    on_esgf = pl.col("latest_version").is_not_null()
    ver_known = pl.col("version").is_null() | pl.col("esgf_versions").list.contains(
        pl.col("version")
    )
    status = (
        pl.when(pl.col("_q").is_null())
        .then(pl.lit("not_queried"))
        .when(~on_esgf)
        .then(pl.lit("dataset_not_on_esgf"))
        .when(
            pl.col("esgf_size").is_not_null()
            & (pl.col("esgf_size") == pl.col("size_bytes"))
        )
        .then(pl.lit("match"))
        .when(pl.col("esgf_size").is_not_null())
        .then(pl.lit("size_mismatch"))
        .when(~ver_known)
        .then(pl.lit("version_not_on_esgf"))
        .otherwise(pl.lit("not_in_esgf_version"))
    )
    local_out = (
        best.with_columns(
            esgf_status=status,
            superseded=pl.col("version").is_not_null()
            & on_esgf
            & (pl.col("version") < pl.col("latest_version")),
            retracted=pl.col("retracted").fill_null(False),
        )
        .select(
            "path",
            "rel_path",
            *QUERY_FACETS,
            "version",
            "name",
            "size_bytes",
            "esgf_status",
            "esgf_version",
            "esgf_size",
            "esgf_checksum",
            "esgf_checksum_type",
            "esgf_url",
            "latest_version",
            "superseded",
            "retracted",
        )
        .sort("rel_path")
    )

    held = local.filter(pl.col("version").is_not_null())
    held_versions = held.select(*QUERY_FACETS, "version").unique()
    missing = (
        esgf_files.join(held_versions, on=[*QUERY_FACETS, "version"], how="semi")
        .join(
            held.select(*QUERY_FACETS, "version", "name"),
            on=[*QUERY_FACETS, "version", "name"],
            how="anti",
        )
        .sort(*QUERY_FACETS, "version", "name")
    )
    return local_out, missing
