# Site hand-off: first session (steps 1–5)

Date: 2026-11-05. Plan: `docs/static-site-plan.md` (v4). Steps 6–8 (Apache,
nightly deploy, launch) were not started. Nothing has been committed. All
work is uncommitted on branch `static-site`.

## Work done

| Step | What | Check (§6a) |
|---|---|---|
| 0 | Moved plan to `docs/`. Added Quarto entries to `.gitignore` (`site/_freeze/` stays committed). New `CLAUDE.md`. | — |
| 1 Scaffold | `site/_quarto.yml` (navbar, search, blog + RSS, `freeze: auto`, pre-render), `_variables.yml`, `index.qmd`, blog + seed post, `site/README.md` (Quarto 1.10.19 pinned), `robots.txt`, issue forms, `.github/workflows/check.yml` | `quarto render site` exits 0. All 11 navbar targets exist in `_site/`. ruff clean. 6 YAML files parse. |
| 2 Sample catalog | `catalog_schema.py`, `scripts/make_sample_catalog.py` (1069 files, 155 datasets: 4 models, 3 experiments, ERA5), `scripts/summarize_catalog.py`, `tests/test_catalog.py` | 43 tests pass. Loads offline with `intake.open_esm_datastore`. Summary columns match §5a, plus `time_start`, `time_end`, and `file_list`. |
| 3 Browser | `site/browse.qmd` (OJS): 12 facet filters, free-text search, result table with per-row **Report** link, Get-data tabs (Python, VAST path, HTTPS + `urls.txt`) | Tested in headless Chrome over DevTools. CESM2 + historical + tas gives 2 datasets / 8 files. Picking one row narrows all three outputs to that dataset. Screenshots checked. |
| 4 Quickstart | `site/notebooks/quickstart.ipynb`: opens the catalog, searches, loads with xarray, plotly global-mean plot. Frozen output in `site/_freeze/`. | Ran locally against the sample. Re-rendered with `ipykernel` disabled: no kernel started, render OK. |
| 5 Pages | `about`, `access`, `catalog`, `download-guide`, `cloud`, `cite`, `report` | Every page exists. 379 internal links OK (stdlib checker; 2 false positives inside embedded OJS source). External links checked with curl. lychee **skipped** (not installed). |

Task IDs touched: **5.1a** (scaffold), **5.3** (quickstart), **5.4**
(download guide), **5.5** (cloud), **5.6** (data-request form), **5.9**
(cite), **5.10** (report a problem), **5.12** (download-access page, audience
config), **M.3** (CI checks). Also prepares **5.11** (versioning text and
`catalog_version`).

## Deviations from the plan

1. **Package name.** Kept `src/whoi_cmip_reorg_code/` (the plan says
   `src/cmipserver/`). The schema is `whoi_cmip_reorg_code/catalog_schema.py`,
   not `manifests/catalog_schema.yaml`. It reuses `drs.DRS_DIR_FACETS`.
2. **Extra catalog columns: `master_id` and `size_bytes`.** intake-esm rejects
   groupby columns that are partly null, and CMIP6 facets are null on
   reanalysis rows. A single groupby column also breaks key lookup with
   pandas 3 (`get_group` KeyError, intake-esm 2025.7.9). So the descriptor
   groups on `["master_id", "version"]`. Keys come out as ESGF `instance_id`s
   (e.g. `CMIP6.CMIP.NCAR.CESM2.historical.r1i1p1f1.Amon.tas.gn.v20190308`),
   the same as the summary's `dataset_id`. `build_catalog.py` (4.1) must
   write `master_id`. If ecgtools can't, add it in a post-processing step.
   `size_bytes` is needed for `size_gb`.
3. **File-list shards** (`site/data/files/<collection>.<source|product>.json`).
   A dataset-level summary can't produce `wget` lists, and the VM has no
   directory listing. The browser fetches only the shards it needs. At about
   555k files, check shard sizes. The largest source may need splitting.
4. **Acknowledgment URL** is already `https://cmip.whoi.edu/` (Brett,
   2026-11-05), not the A.4 text. PIs still need to confirm the wording.
5. **Old-site link.** `old_site_url` in `_variables.yml` drives a
   `{{< old-site >}}` shortcode (`site/_filters/old-site.lua`) used in the
   footer and on `about.qmd`. The navbar can't be made conditional, so its
   "Old site" entry is a separate, commented line in `_quarto.yml`. Retiring
   the old site (6.3) takes two deletions.
6. **Navbar grouping.** Access, Download guide, and Cloud sit under one "Using
   the data" menu, to keep 11 items readable. The quickstart notebook is
   rendered directly (`notebooks/quickstart.ipynb`). There is no separate
   `quickstart.qmd`.
7. **Reanalysis layout and version (proposal).** The sample uses
   `reanalysis/era5/<frequency>/<variable>/<vYYYYMMDD>/era5_<var>_<freq>_<year>.nc`
   with ERA5 short names (`t2m`, `msl`, `sst`). This is a guess at the layout
   and needs deciding before ERA5 downloads land.
8. **Dependencies.** New `site` dependency group: ipykernel, jupyter-core,
   nbclient, nbformat, netcdf4, plotly, pyyaml. It resolved on Python 3.14
   with no changes to `requires-python`. `lychee` in the main deps is the PyPI
   package, **not** the Rust link checker. I suggest removing it. CI uses
   `lycheeverse/lychee-action`.
9. **Synthetic NetCDF.** `tests/fixtures/sample_data/` holds four tiny files
   (96 KB) for the CESM2 historical tas dataset. Their attributes mark them
   as `SYNTHETIC`, not model output.

## Browser upgrade: vendored libraries (2026-11-05)

`site/browse.qmd` now uses Tom Select (filters, with per-value result
counts), Tabulator (table), and Arquero (counts and filtering). The filters
and search text are saved in the page URL, e.g.
`browse.html?source_id=CESM2&variable_id=tas&search=Amon`. The search key is
`search`, not `q`, because Quarto's site search reads and removes `?q=` on
page load.

All third-party code and fonts are pinned and served from `site/vendor/`
(manifest `site/vendor/vendor.yml`, script `scripts/vendor_js.py`, test
`tests/test_vendor.py` checks the sha256 hashes):

| Package | Version | License | Why |
|---|---|---|---|
| tom-select | 2.6.2 | Apache-2.0 | facet filters |
| tabulator-tables | 6.7.1 | MIT | results table |
| arquero | 8.0.3 | BSD-3-Clause | facet counts, filtering |
| d3 | 7.8.5 | ISC | OJS stdlib, same version Quarto 1.10.19 pins |
| htl | 0.3.1 | ISC | OJS stdlib (`html`) |
| @observablehq/inputs | 0.10.6 | ISC | OJS stdlib (`Inputs.search`) |
| @fontsource/source-sans-pro | 5.3.0 | OFL-1.1 | cosmo theme font |

**For the IT security review:** the site loads no third-party scripts, styles,
or fonts at runtime. Checked in headless Chrome: every request from
`browse.html` went to the local server. How:

- `browse.qmd` shadows the OJS stdlib entries it uses (`d3`, `htl`, `html`,
  `Inputs`) with the vendored copies. Quarto would otherwise fetch them from
  jsDelivr on first use. The page no longer uses `md` (it would fetch
  `marked`). Any new OJS page must do the same, or it will load from jsDelivr.
- `site/theme.scss` sets `$web-font-path: false` so cosmo does not import
  Google Fonts on every page. `site/fonts.css` serves Source Sans Pro (latin
  300/400/700) from `vendor/`.

## Placeholders (in `site/_variables.yml` and `.github/ISSUE_TEMPLATE/config.yml`)

| Key | Value now | Waiting on |
|---|---|---|
| `github_repo` | `OWNER/REPO` | which repo hosts issues |
| `help_email` | `HELP_EMAIL_TBD` (also in `config.yml`) | shared help address |
| `catalog_path` | `CATALOG_PATH_TBD` | C-4 |
| `catalog_version` | `sample` | C-12, real catalog (4.1–4.4) |
| `smb_path` | `SMB_PATH_TBD` | IT (share name) |
| `https_prefix` | `HTTPS_PREFIX_TBD` (proposal: `https://cmip.whoi.edu/data`) | IT-8–IT-10, C-17, 5.13 |
| `globus_url` | empty | 6.1, S-4 |
| `audience` | `internal` | PI-4, PI-5 |
| `wget_max_files` | 500 | tune after C-17 |
| About page holdings | old-site figures, labelled "audit pending" | audit |

## Open questions / proposed decisions

- **PI-4 / PI-5:** public vs WHOI-only, and whether VAST paths may appear.
  `audience: external` hides VAST paths in the browser. The summary never
  contains absolute paths.
- **C-4:** stable catalog path on VAST.
- **C-5:** the notebook reads `WHOI_CMIP_CATALOG` and `WHOI_CMIP_DATA_ROOT`,
  and the browser's external snippet swaps the prefix in `cat.df["path"]`. I
  have not checked whether xarray can open the real files over HTTPS
  efficiently.
- **C-6:** about 28k dataset rows. The browser builds facet options from the
  full summary, which is fine at 155 rows. Check load time and JSON size
  (estimate: a few MB) with a full-size synthetic summary before launch.
- **C-12:** snapshot naming and retention. `catalog.qmd` describes `latest`
  plus dated snapshots as planned.
- **New:** whether `master_id` is an acceptable catalog column (deviation 2),
  and the ERA5 directory layout (deviation 7).

## Commands for Brett

```bash
uv sync --all-groups
uv run ruff check && uv run ruff format --check && uv run pytest
uv run scripts/make_sample_catalog.py --out tests/fixtures
uv run scripts/summarize_catalog.py --catalog tests/fixtures/sample_catalog.csv --out site/data
uv run quarto render site          # or: uv run quarto preview site
brew install lychee && lychee --offline --root-dir "$PWD/site/_site" 'site/_site/**/*.html'   # optional, local link check
```

Notes:

- `ruff format` also formats `site/notebooks/*.ipynb`. A format change
  changes the notebook source, so the next render re-executes it. Commit the
  updated `site/_freeze/` afterwards.
- On HPC, set `WHOI_CMIP_CATALOG=<production json>` before re-rendering the
  notebook against the real catalog.
- Before the GitHub repo exists: confirm the Actions versions in `check.yml`
  (`setup-uv@v6`, `quarto-actions/setup@v2`, `lychee-action@v2`). CI has not
  run yet.
