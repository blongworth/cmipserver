# To do

Open items as of 2026-11-15, after the non-CMIP6 audit (X1-X4) and the first
three non-CMIP6 catalogs (`era5`, `glorys12v1`, `soda3122`). Details and
numbers: `docs/audit-findings.md`. Nothing here moves, renames, or deletes
anything on `/proj/cmip6`; cleanup items are proposals that go through plan,
approval, apply (`docs/audit-runbook.md`). Tick items off and date them.

## Catalogs and site

- [ ] **Copy the new catalogs to `/proj/cmip6/catalog/live`** (HPC, Brett).
  `whoi_era5`, `whoi_glorys12v1` and `whoi_soda3122` (csv, `_invalid.parquet`,
  json) are only under `runs/2026-11-15_era5cat` and `runs/2026-11-15_oceancat`.
  Copy to a hidden temp name, then rename, JSON last.
- [ ] **Write `scripts/promote_catalog.py`** (`next` -> `live`; design in the
  `docs/deploy-plan.md` "Code to write" table: dry run by default, require a
  passing check report, `--previous` comparison, temp-and-rename copy).
- [ ] **Re-run the site summary and render** with every enabled catalog
  (command in `CLAUDE.md`), on HPC where the notebooks can execute. Check that
  Other holdings lists all 11 listed-only products with their citations.
- [ ] **Generate `site/data/holdings.json`** (file counts for Other holdings):
  `summarize_other.py --products manifests/catalogs.yaml --site-json
  site/data/holdings.json` on the X4 scans (`runs/2026-11-14_X4`), then commit.
- [ ] **Licences and citations** (`manifests/catalogs.yaml`, marked FILL IN or
  CONFIRM): licence for `soda3122`, `bran2020`, `hycom_gofs30/31`, `eccov5`,
  `prism`, `oras5`, `cfsr932`; confirm the draft citations and the ERA5 DOIs
  and licence text.
- [ ] **Decide how often non-CMIP6 catalogs are checked and rebuilt** (regular
  or on demand, per product). Nothing in the docs assumes a nightly rebuild.
- [ ] Optional: show a product's `caveats` on its dataset page. ERA5 caveats no
  longer appear on the site now that it is catalogued (only the recipe on the
  Catalog page does).
- [ ] Optional: `check_nc.py --deep` (reads the last time step of every file)
  on the catalogued products.
- [ ] Fix the flaky `tests/test_checksums.py::test_select_and_hash` (an
  unseeded random sample sometimes adds `,sample` to the expected reason).

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
