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
- [ ] **Write `scripts/promote_catalog.py`** (`next` -> `live`; design in the
  `docs/deploy-plan.md` "Code to write" table: dry run by default, require a
  passing check report, `--previous` comparison, temp-and-rename copy).
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
- [ ] **Self-host plotly.js for the notebook figures** (found 2026-11-17).
  Notebook figures did not show on the live site: Quarto's rendered plotly
  output loads plotly.js/require.js from CDNs, and the Apache CSP
  (`infra/apache/cmip.conf`, `script-src 'self' ...`) blocked them. Confirmed by
  turning the CSP off on the VM (figures appeared); restore it afterwards.
  Plan: copy `plotly/package_data/plotly.min.js` from the Python package (same
  version as the code that builds the figures, about 4.7 MB) into
  `site/vendor/` and record it in `site/vendor/vendor.yml`; make the notebooks
  load that one file instead of a CDN (renderer or `include_plotlyjs`; not yet
  tested with Quarto); then rebuild, publish and pull. Do not leave the CSP
  open. Stopgap if needed: add only the observed CDN hosts to `script-src`.
  Applies to `quickstart.ipynb` and `20crv3.ipynb`.
- [ ] **Quickstart notebook: set `HDF5_USE_FILE_LOCKING=FALSE`** (and the
  synchronous Dask scheduler for multi-file datasets) in the setup cell, as
  `20crv3.ipynb` does; users on hydra hit "NetCDF: HDF error" otherwise
  (`docs/audit-findings.md`, "HDF5 file locking on VAST").
- [ ] Optional: show a product's `caveats` on its dataset page. ERA5 caveats no
  longer appear on the site now that it is catalogued (only the recipe on the
  Catalog page does).
- [ ] Optional: `check_nc.py --deep` (reads the last time step of every file)
  on the catalogued products.
- [ ] Fix the flaky `tests/test_checksums.py::test_select_and_hash` (an
  unseeded random sample sometimes adds `,sample` to the expected reason).

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
