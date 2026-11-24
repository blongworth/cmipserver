# To do

Open items as of 2026-11-15, after the non-CMIP6 audit (X1-X4) and the first
three non-CMIP6 catalogs (`era5`, `glorys12v1`, `soda3122`). Details and
numbers: `docs/audit-findings.md`. Nothing here moves, renames, or deletes
anything on `/proj/cmip6`; cleanup items are proposals that go through plan,
approval, apply (`docs/audit-runbook.md`). Tick items off and date them.

## Catalogs and site

- [x] **Copy the new catalogs to `/proj/cmip6/catalog/live`** (HPC, Brett;
  done 2026-11-15). `whoi_era5`, `whoi_glorys12v1` and `whoi_soda3122` (csv,
  `_invalid.parquet`, json) are in `live`.
- [x] **Write `scripts/promote_catalog.py`** (2026-11-20): dry run unless
  `--execute`; refuses without a passing check report newer than the build,
  or on a dataset drop over 5% vs `live` (`--allow-drop`); temp-and-rename
  copy, JSON last. Dry run against the mounted store checked.
- [ ] **Re-run the site summary and render** with every enabled catalog
  (`sbatch slurm/build_site.sbatch` now uses all enabled catalogs by default;
  summary checked locally 2026-11-17: five catalogs, 31,162 datasets), on HPC
  where the notebooks can execute. Check that
  Other holdings lists all 11 listed-only products with their citations.
- [ ] **Generate `site/data/holdings.json`** (file counts for Other holdings):
  `summarize_other.py --products manifests/catalogs.yaml --site-json
  site/data/holdings.json` on the X4 scans (`runs/2026-11-14_X4`), then commit.
- [ ] **Licences and citations** (`manifests/catalogs.yaml`, marked FILL IN or
  CONFIRM): licence for `soda3122`, `bran2020`, `hycom_gofs30/31`, `eccov5`,
  `prism`, `oras5`, `cfsr932`; confirm the draft citations and the ERA5 DOIs
  and licence text.
- [x] **Self-host plotly.js for the notebook figures** (2026-11-20). Quarto
  adds require.js and jQuery for any HTML cell output, and plotly adds
  plotly.js and MathJax, all from CDNs, which the CSP blocks. They are now in
  `site/vendor/` (`vendor.yml`, with the injected URLs as `aliases`), and the
  post-render step `scripts/localize_site_scripts.py` points the pages at
  them and fails the render if any other external script remains. Both
  notebooks executed locally draw their figures with the production CSP and
  no violations. Also: the blog listing no longer loads MathJax (`html-math-method:
  plain`), `catalog.qmd` loads `htl` from `vendor/`, and `infra/apache/cmip.conf`
  allows `data:` fonts (the OJS runtime's icon font). **To do on the VM:**
  install the updated `cmip.conf` and reload Apache; then rebuild, publish
  and pull.
- [ ] **Quickstart notebook: set `HDF5_USE_FILE_LOCKING=FALSE`** (and the
  synchronous Dask scheduler for multi-file datasets) in the setup cell, as
  `20crv3.ipynb` does; users on hydra hit "NetCDF: HDF error" otherwise
  (`docs/audit-findings.md`, "HDF5 file locking on VAST").
- [x] Show a product's `caveats` (and `open_kwargs`) on its dataset page
  (2026-11-20; rendered, not yet checked in a browser).
- [ ] Optional: `check_nc.py --deep` (reads the last time step of every file)
  on the catalogued products.
- [ ] Fix the flaky `tests/test_checksums.py::test_select_and_hash` (an
  unseeded random sample sometimes adds `,sample` to the expected reason).

- [x] **Catalog the remaining generic products** (tooling done 2026-11-18:
  `exclude`, `time_dim`, `start`/`end` rules, repeated `--ncinfo`,
  `--include-disabled`, the dataset-dir collision check; manifest entries
  written). On HPC, per product in this order: `glorys12v1_statics`,
  `grep_monthly`, `glorys12v1_monthly`, `bran2020`, `grep_daily`,
  `hycom_gofs30`, `hycom_gofs31`, `eccov5`: build with `--include-disabled`, then
  `check_catalog.py --sample-datasets 10`. Gates: catalog + invalid rows = X4
  `.nc` count; invalid reasons are only damaged or excluded; check passes;
  datasets match `find_gaps.py`. Rebuild `glorys12v1` and `soda3122` and diff
  them against `live` (should be identical). Then enable, promote, render.
  Done 2026-11-20: all eight promoted to `live` and enabled (`bran2020`
  after opening all 14 datasets in a Slurm array); `glorys12v1` and
  `soda3122` rebuilt identical to `live`.
- [ ] Re-run the site summary and render with the 13 enabled catalogs
  (`sbatch slurm/build_site.sbatch`); check a HYCOM or ECCO dataset page
  shows the Notes block with the open options.
- [x] Decided 2026-11-18: catalog `eccov5` (alpha, no final V5 exists);
  keep `prism` listed (no coordinates, intake-esm cannot join it).
- [x] `eccov5`: the latlon one-step files load in date order with
  `decode_times=False` (HPC 2026-11-20).
- [x] HPC builds 2026-11-20: `glorys12v1_statics`, `grep_monthly`,
  `glorys12v1_monthly`, `bran2020`, `grep_daily` pass every gate.
- [x] `hycom_gofs30`, `hycom_gofs31` and `eccov5` load with their
  `open_kwargs` (HYCOM `tau`, ECCO lat-lon `months since`; HPC 2026-11-20).
- [ ] Repair proposals: move the complete nested ECCO `THETA_1992.nc` over
  the truncated one; quarantine HYCOM `1999/1992/` stray file.

- [x] **Build the store vocabulary on HPC** (live 2026-11-23;
  `docs/audit-runbook.md` 8b). All ERA5 variables matched the ECMWF
  database: by long name, `GRIB_paramId`, short name, or the
  `p<param>.<table>` names of GRIB1 parameters with no short name
  (`p71.162` = 162071), so no parameter ids are needed in `era5.yaml`.
- [ ] **Vocabulary in the build pipeline.** For now it is a by-hand step of
  catalog promotion (`docs/audit-runbook.md` 8, "Promoting a catalog").
  Make it part of the pipeline: run the vocabulary scan and build after each
  catalog build (`slurm/scan_var_attrs.sbatch` then
  `slurm/build_vocab.sbatch`, chained with `--dependency=afterok`), have
  `promote_catalog.py` promote the vocabulary with the catalogs, and fail
  promotion if `check_catalog.py --vocabulary` does not pass.
- [ ] **Parquet catalogs** (found 2026-11-23). intake-esm 2025.12.12 (current
  on PyPI and conda-forge) opens a JSON whose `catalog_file` is `.parquet`;
  writing Parquet and real list columns (`variable_id`) are merged upstream
  (intake/intake-esm #797, #802) but not released. Blocker: 2025.12.12 pins
  `polars<1.33`, so with our `polars>=2.0.0` uv resolves 2025.7.9 (CSV only),
  and users on a recent polars can't open Parquet; the open refactor (#800)
  moves to `polars>=1.34`. Plan: once a release lifts the cap, raise the
  `intake-esm` pin, time CSV against Parquet on the sample catalog, and have
  the build also write `whoi_<output>.parquet` plus its own JSON next to the
  CSV. Keep CSV as the published default until users can be told a minimum
  intake-esm version; `summarize_catalog.py`, `check_catalog.py`,
  `build_vocab.py` and the docs read `.csv` today.
- [ ] Vocabulary `nc_check`: optionally treat equivalent unit spellings
  (`psu` = `0.001`, `K` = `degK`) as a match, so only real unit differences
  are reported.
- [ ] **Vocabulary phase 2: concepts across products.** A curated
  `manifests/vocab/concepts.yaml` (label, synonyms, match rules on
  `standard_name` plus level, e.g. "sea surface temperature" =
  `sea_surface_temperature`, or `sea_water_potential_temperature` at the top
  level), a `whoi_cmip_concepts.csv` next to the vocabulary, and a concept
  picker on the site, so one search leads to the matching variable in each
  product.

- [x] **Browse page redesign** (2026-11-23): search box, collection chooser,
  filter chips, checkbox facets with counts ordered by use (variables and CF
  standard names from the store vocabulary; multi-variable datasets listed
  per variable), picks kept across searches, sticky Get data bar. Plain JS
  (`site/js/`), not OJS; same URLs, Python snippet and `urls.txt` as before
  (compared on synthetic data). **Check after the next HPC render** with the
  13 catalogs and `--vocab`: facet sizes, page load time, and that the
  Variables page's "Datasets" links open the right datasets.

## Data updates (needs a plan)

Found 2026-11-18: the plans cover a one-time fill and catalog rebuilds, not
getting new data into the store. Only the pieces below exist; the rest needs
planning in `docs/deploy-plan.md` and `docs/audit-runbook.md` first.

- [ ] **Decide the update policy per product**: which products get updates
  (ERA5 at least), how often (regular or on demand), who runs them, and
  whether ERA5T (preliminary, later replaced) goes on the store.
- [ ] **Decide how often catalogs are checked and rebuilt** (regular or on
  demand, per product). Nothing in the docs assumes a nightly rebuild.
- [ ] **ERA5 fetch path**: `manifests/products/era5.yaml` is catalog-only (no
  source) and `download_product.py` reads only HTTPS autoindex listings, so
  there is no way to fetch new ERA5 data. Needs a CDS source and download mode;
  the CDS API key stays in the account's own credentials file, never the repo.
  Plan for the old/new CDS format change (`time` vs `valid_time`).
- [ ] **20CRv3 and other HTTPS products**: updating works by hand (raise
  `years`, `plan_product.py`, download, approve, apply), but files changed
  upstream are only reported (`present_differs`), never re-fetched.
- [ ] **CMIP6**: `check_esgf.py` and the repair path cover new versions and
  retractions of datasets already held; nothing adds new datasets
  (`manifests/core.yaml` is empty).
- [ ] **Generic products** (GLORYS, SODA): new files stay invalid until
  `check_nc.py` is re-run on them; make that a required update step.
  Proposal for scheduled rebuilds: a persistent ncinfo per tree
  (`/proj/cmip6/catalog/ncinfo/<tree>/`) and an incremental `check_nc.py`
  mode that reads only new paths or ones whose size or mtime changed.
- [ ] **Runbook section "Updating a product"** (`docs/audit-runbook.md`), once
  the above is decided: plan, download, approve, move, `check_nc` (generic),
  rebuild, check and promote, site news post.
- [ ] Then add the update loop to `docs/pipeline-flowchart.html` (not before).

## Cleanup proposals (need approval; nothing done)

Re-fetch from the source (repair plan):
- [ ] 36 damaged files: GREP daily 12 truncated, HYCOM 15 + 4 empty and 4
  truncated, ECCOv5 `THETA_1992.nc` (list in "X4 check_nc: 36 damaged files").
- [ ] BRAN2020: 3 files with wrong times (`south_salt_1999_330`,
  `north_temp_2000_085`, `north_temp_2003_229`).
- [ ] ERA5: pressure-level monthly means 2022 stop in September
  (`geopotential`, `specific_humidity`, `temperature`); 6hr
  `volumetric_soil_water_layer_4` has `1998-11` holding October.
- [ ] GREP daily gaps (longest 1999-06-26 to 2001-06-30), or list as incomplete.

Set aside after a checksum pass (quarantine, never delete):
- [ ] GLORYS12V1 monthly: 36 `_(1).nc` copies (about 46 GB).
- [ ] HYCOM: 363 files of `GOFS30/1993` copied to `GOFS31/global/1994` (1.05 TB).
- [ ] ERA5: `monthly-means/evaporation/x.nc`,
  `1hr/evaporation/1993-05-03_evaporation_corrupted.nc`, and the 21 yearly 1hr
  files for 2023-2025 (redundant with the daily files; catalog-invalid).
- [ ] `era5/quarantine_2022_20260807T140218` (28 files): delete once the owner
  confirms.
- [ ] Litter: `.DS_Store`, `._*`, `*.nc.lck`, `index.html` and `wget-log` under HYCOM.

Decide what to do with:
- [ ] `ORAS5` yearly `.tar.gz` archives (extract, and where, or leave listed).
- [ ] `cfsr932` (which files are the product; `.gz` and wget scripts).
- [ ] `emu` (leave under `ocean_reanalysis`, or move out of the data tree).

## Messages for Brett to send (drafts, not sent)

- [ ] The owner questions in `docs/audit-findings.md`, "Owners" (1-9), now
  including the ECCOv5 `THETA_1992.nc` question. Claude drafts; Brett sends.
