# Audit findings

What the audit found that needs a decision or a later check. Add to this as
levels run; keep each entry to what was seen, how it was checked, and what is
still open. Run outputs are in `runs/<date>_<level>/` (gitignored). Levels and
their scope are in the level log at the end.

Status: **open** (needs action or a decision), **report-only** (noted, no
action planned), **resolved**.

## Summary

**Scope.** CMIP6 in four levels, from one file to the full store (L1-L4,
2026-11-12); the non-CMIP6 trees `era5`, `ocean_reanalysis`, `cmems`, `prism`
(X1-X4, 2026-11-13 to 14); a disk-use cross-check of `/proj/cmip6/data`
(2026-11-16); a shallow look at `/proj/cmip6/cmip5`. All read-only; nothing
on the store was moved, renamed or deleted.

**Size.** 286.55 TB (apparent) and 1,346,890 `.nc` under `/proj/cmip6/data`.
The 5 enabled catalogs (`cmip6`, `20crv3`, `era5`, `glorys12v1`, `soda3122`)
hold 1,209,289 files, 211.90 TB. About 70 TB of `.nc` is not cataloged (most
of it is listed on the site), and 3.6 TB is non-`.nc`. Disk and catalogs
agree exactly apart from each catalog's invalid list. CMIP5 adds 18 TB
outside `data/`.

**Health.**
- CMIP6: 717,672 of 732,134 files (98.0%) read `ok`. The 14,462 empty files
  are almost all `EC-Earth3-CC`, from one bulk event on 2022-02-03/04. 2,216
  files have non-CMIP6 names and are left out of the catalog. 370 datasets
  overlap and 106 have gaps by filename; the largest groups predate the
  reorganization.
- Non-CMIP6: 36 damaged files of 614,746 (GREP daily, HYCOM, ECCOv5), none in
  ERA5, GLORYS, BRAN2020, SODA or PRISM. ERA5 mixes old- and new-CDS formats,
  so some datasets only open with a preprocess step. GREP daily is missing
  about two years; GLORYS monthly and HYCOM hold duplicate copies.
- Tooling: HDF5 locking on VAST (resolved), threaded opens crash on hydra, and
  long runs need `use_cftime=True`. The last two affect users.

**Decisions.**
- 2026-11-13: catalog non-CMIP6 products in place, no moves; catalog ERA5 plus
  one or two ocean products and list the rest.
- 2026-11-15: catalog ERA5, GLORYS12V1 daily and SODA 3.12.2; catalog ERA5's
  1hr daily files, leaving out the 2023-2025 yearly copies.

**Main open items.**
- CMIP6 version mapping (numeric `1` dirs; bare-date dirs to rename?).
- Repairs: re-fetch CMIP6 empties and gaps from ESGF, and the damaged and
  missing non-CMIP6 files from their sources.
- Out-of-catalog CMIP6 files: native CESM output and SAM0-UNICON names.
- Site docs for users: synchronous dask scheduler, locking off, `use_cftime`.
- Cleanup proposals (quarantines, duplicates, stray files) and the owner
  questions, both waiting on approval.
- Unsettled products: ORAS5 archives, cfsr932, ECCOv5 alpha, the CMIP5 tree;
  rebuild cadence for non-CMIP6 catalogs.

## Store layout

### Version directories (open)
- **Numeric** (`.../gn/1/`, not `vYYYYMMDD`): the usual layout here, and in the
  old server's file list (L1, 2026-11-12). `drs.py` accepts them as `ok` (scan
  column `version_scheme` = `numeric`), so `reorg` never moves them. They will
  not match ESGF versions, so `check_esgf` and `plan_repairs` do nothing useful
  on them until a mapping is decided (Phase B, `audit-runbook.md` step 4).
- **Bare date** (`20201224`, no `v`): `CMIP/CAS/CAS-ESM2-0` and possibly others
  (count `version_scheme = 'bare_date'` in the scan). They scan as `ok` and are
  not reorganized. The DRS path is just `v` + the dir name, so they are a safe
  `reorg` candidate and would match ESGF. Decide: rename?

### Disk use and catalog cross-check (2026-11-16; open: VAST physical use)
`scripts/du_shard.sh` (Slurm job 358247, 26 second-level shards, 0 errors) and
`scripts/du_compare.py` against the 5 live catalogs. Output:
`runs/du/358247/compare/`.

- **`/proj/cmip6/data`: 266,870.6 GiB (286.55 TB) apparent, 266,868.8 GiB
  allocated**, 1,541,837 files (1,346,890 `.nc`). Allocated equals apparent, so
  this is logical size; physical use after VAST compression/dedup is not
  visible from hydra. Open: ask the VAST admin or IT for the capacity/quota
  figure.
- **Catalogs: 1,209,289 files, 211.90 TB.** Each catalog equals the `.nc` on
  disk under its root minus its invalid list. 20crv3, glorys12v1, soda3122 and
  cmip6 CFMIP/DAMIP/PMIP/ScenarioMIP match exactly; `cmip6/CMIP` is short
  16,682 files / 658.5 GB (`whoi_cmip6_invalid.parquet`), `era5` 23 files /
  246.7 GB (`whoi_era5_invalid.parquet`).
- **Counts agree with the earlier scans** (cmip6: 732,134 `.nc` + 31,041 other
  = L4's 763,175; every X4 product). No files appeared or vanished.
- **Not cataloged: about 70.1 TB of `.nc`**: HYCOMvGOFS30 21.38,
  HYCOMvGOFS31 19.23, bran2020 15.60, GREP daily 12.58, cmems monthly 0.53,
  ECCOv5 0.42, cfsr932 0.35, ORAS5 0.03, prism 0.005, era5 quarantine 0.0003 TB.
- **Non-`.nc`: about 3.6 TB**, mostly ORAS5 `.tar.gz` (2.3 TiB) and
  `ocean_reanalysis/emu` (160,122 files, 1.05 TiB); also 31,041 files under
  `cmip6` and 625 `prism/bil`.
- **Split rerun** (job 358301, cmems, `CMEMS_ENSAMBLE`, ORAS5 and ECCOv5 one
  level deeper, merged with 358247 as 32 shards;
  `runs/du/merged_358247_358301/compare/`): identical totals. Per product:
  glorys12v1_monthly 409 files / 532.34 GB, glorys12v1_statics 3 / 2.38 GB,
  grep_daily 5,723 / 12,575.8 GB, `ECCOv5/Version5` 10,798 / 416.91 GB,
  `ORAS5/oras5` 135 `.nc` / 32.14 GB + 3,006 other (2,338 GiB). eccov5, oras5
  and grep_monthly still share a shard; a depth-3 split would separate them.

### Stray files (open, cleanup needs approval)
- `.DS_Store`/`._.DS_Store` in `cmip6/CMIP/`, `CMIP/NCAR/`, `data/`, `era5/`,
  `era5/reanalysis/*`, `ocean_reanalysis/`, `ECCOv5/`, `ORAS5/`. Scan as
  `not_nc`. Harmless.
- Empty `logs/` and `runs/2026-11-12_L3/` in `/proj/cmip6/data/cmip6`, made
  2026-11-12 by a `mkdir` run inside the store. They show up as scan shards.
  Remove with `rmdir` (refuses if not empty).
- `ocean_reanalysis/NWA_downscaling`: empty.
- ERA5 `*.nc.lck` lock files (2023-01-14); HYCOM `index.html` (GOFS30/1995),
  `wget-log` (GOFS31/1994, 2006), a `1992/` dir inside `GOFS30/1999/`;
  `ORAS5/wget_oras5.sh`.

## Tooling

### HDF5 file locking on VAST (resolved)
With HDF5 locking on, good files report `header_error` (`ncdump`: `NetCDF: HDF
error`; `h5ls` fails) and work with `HDF5_USE_FILE_LOCKING=FALSE`. `ncinfo.py`
disables locking for its read-only opens. Do the same in any reader on the
mount.

### Threaded dataset opens segfault on hydra (open, affects users)
`check_catalog` died (exit 139, SIGSEGV; later 135, SIGBUS) when `to_dask()`
opened a multi-file dataset. `python -X faulthandler` puts the crash in dask's
thread pool, several threads in `netCDF4_.open_store_variable` at once. Single
files open fine, and the largest smoke-catalog dataset (CESM2 `ta850`, 438,000
daily steps) opens with `dask.config.set(scheduler="synchronous")`. So this
netCDF4/HDF5 build is not safe for concurrent opens.

Seen with uv's standalone Python 3.14.10, `netcdf4` 1.7.5.1.post1 (PyPI wheel,
bundled HDF5), xarray 2026.9.0, dask 2026.8.0, intake-esm 2025.7.9. Not tested
with a module or conda HDF5, which might not crash; the workaround works either
way. `check_catalog.py` now uses the synchronous scheduler and
`HDF5_USE_FILE_LOCKING=FALSE`.

Open: `site/catalog.qmd`, `site/browse.qmd` and `quickstart.ipynb` show
`to_dataset_dict()`/`to_dask()` with the default scheduler. Document the
workaround on the site.

Test (by hand on a hydra login node; repeat after any change to Python,
`netcdf4`, dask or xarray, or on a module build):
1. Build a catalog with a many-file dataset (`build_catalog.py --root
   .../CMIP/NCAR/CESM2`). One-file datasets never crash.
2. Pick the dataset with the most files: read the CSV with
   `infer_schema_length=0` (keeps `version` a string), count by `master_id`,
   `version`; the key is `master_id.version`.
3. In two processes, with `HDF5_USE_FILE_LOCKING=FALSE` and `python -X
   faulthandler`, run `intake.open_esm_datastore(<json>)[key].to_dask()`: with
   the default scheduler (bug present: exit 135 or 139), then synchronous
   (exit 0). Check `${PIPESTATUS[0]}`; a `tail` hides the exit code.

Not automated: the laptop and CI have other HDF5 builds and no multi-file VAST
data (macOS never crashed). A regression test would open a few small synthetic
files threaded in a subprocess on hydra, skipped off-HPC.

### Long runs need `use_cftime=True` (open, affects users)
`to_dataset_dict()` on `CMIP6.CMIP.CNRM-CERFACS.CNRM-CM6-1.piControl.r1i1p1f2.Eday.hus850.gr.1`
(4 `ok` files) fails with `Cannot combine along dimension 'time' with mixed
types. Found: DatetimeGregorian, Timestamp`. Years past 2262 don't fit
datetime64[ns], so some files decode to cftime and some to Timestamp. Not a
data problem. Fix: `xarray_open_kwargs={"use_cftime": True}`. `check_catalog`
passes it; the site docs should say so for piControl and other long runs.

### `columns_with_iterables` (resolved by not using it)
intake-esm 2025.7.9 with polars 2.1 can't open a catalog that sets it
(`json_decode() missing ... 'dtype'`). Generic catalogs store a multi-variable
`variable_id` as a comma-joined string, so `cat.search(variable_id=...)` needs
the exact string. Revisit when intake-esm is fixed.

### Other (report-only)
- `check_nc --deep` prints `UserWarning: missing_value not used since it cannot
  be safely cast to variable data type` (CESM2 `tas`: double `missing_value`,
  float variable). Harmless; not suppressed.
- A stat-only scan of `ocean_reanalysis/emu` (>100k entries) over the SMB mount
  did not finish in 30 minutes. Scan non-CMIP6 trees on HPC.

## Dataset findings

CMIP6, from the L4 scan, `check_nc` and `find_gaps`. "2022 list" is the old
server's file list (Feb 2022).

### Zero-byte files: 14,462 (open, Phase B repair worklist)
2.0% of 732,134 files are 0 bytes (`nc_status = empty`): `EC-Earth3-CC` 14,378,
`SAM0-UNICON` 44, `CAS-ESM2-0` 33, `KACE-1-0-G` 4, `CESM2-WACCM` 3.
`EC-Earth3-CC` empties are single-year monthly files in `1pctCO2` (7,427 of
16,176) and `abrupt-4xCO2` (6,950 of 16,176), plus 1 in `historical`;
`piControl` and `amip` are fine. All are dated 2022-02-03 (12,652) or
2022-02-04 (1,726), as are the `CAS-ESM2-0`/`SAM0-UNICON` samples checked: one
bulk event (failed copy or restore?). Other groups not checked.

- `find_gaps` reads filenames only, so empty files count as present and hide
  real gaps.
- The catalog lists 14,458 as `excluded`; the 4 `KACE-1-0-G` empties are
  `path_filename_mismatch` (see label mismatches). Build the worklist from
  `ncinfo` (`nc_status = empty`), not from the catalog's invalid list.

Open: re-fetch from ESGF, after the versioning question.

### Non-standard filenames: 2,216 `bad_filename` (open, decision)
Excluded from the catalog. Do not loosen the parser or `reorg` them.

- **Native CESM output under `CMIP/NCAR`** (578): `LENS` 254, `CESM_HR` 254,
  `CESM2-LE` 70, with names like
  `b.e11.B20TRC5CNBDRD.f09_g16.001.cam.h0.PSL.185001-200512.nc` and
  `cesm2_historical_aice_member_r10i1181p1f1.nc`. Added after the 2022 list.
  One file from each has no CMIP6 global attributes: `LENS` is CESM1/CAM5
  (CMIP5 era, 2013), `CESM_HR` has no CMIP metadata, `CESM2-LE` is native CICE
  output with CMIP6-style member names. Not published CMIP6, don't fit the DRS.
  Decide: leave out, or add as their own product.
- **SAM0-UNICON** (1,638, all in the 2022 list): an underscore between
  six-digit dates (`tauuo_Omon_SAM0-UNICON_piControl_r1i1p1f1_gn_055101_056012.nc`).
  Open: upstream names or a WHOI rename? Cataloging them needs a reviewed rename.

### Label mismatches (report-only)
All in the 2022 list.
- **`KACE-1-0-G` `tasmax`/`tasmin`** mix table labels: 4 `attr_mismatch` files
  (`historical`, `ssp585`) are named and filed `Amon` but have `table_id = day`;
  4 `path_filename_mismatch` files are named `…_day_…_gr_18500101-20001230.nc`
  in `Amon` dirs (and are 0 bytes).
- **`SAM0-UNICON` `fx`** (3): random filename suffixes (`…_gnRx4IoZ65553.nc`),
  so both `path_filename_mismatch` and `attr_mismatch` (`grid_label`).
- **`FIO-QLNM/FIO-ESM-2-0/piControl`** (1):
  `thetao_Omon_PCMDI-test-1-0_piControl-withism_r3i1p1f1_…`, a test file for
  another model and experiment.

Totals: 8 `path_filename_mismatch`, 7 `attr_mismatch`.

### Overlaps (370 datasets) and gaps (106) (open, Phase B)
From filenames (`find_gaps`, no `--core`, empty files counted as present).
- Overlaps: `NorESM2-MM` 97, `TaiESM1` 59, `FGOALS-f3-L` 53, `FGOALS-g3` 34,
  `ScenarioMIP/NorESM2-LM` 21, about 20 more models with 1-16. Typically two
  files in one `/1/` dir with nearly the same span (`000101-001012` beside
  `000102-001012`; `185001-199912` beside `185001-200912`). Likely more than one
  ESGF version downloaded into `1`; not confirmed.
- Gaps: `NorESM2-LM` 19, `SAM0-UNICON` 12, `CESM2-WACCM-FV2` 10, `E3SM-1-0` 9,
  `CESM2-FV2` 8, `IITM-ESM` 7, `CNRM-CM6-1-HR` 6, `TaiESM1` 6, `GISS-E2-1-G` 5,
  about 12 more. Real missing chunks (e.g. `CIESM` ssp585 `rtmt` 210012 ->
  402901). False gaps not ruled out: the `julian` calendar (3,728 files) was not
  checked.
- The biggest groups have the same counts in the 2022 list, so they predate
  the reorganization.

Cases looked at (all report-only):
- **CESM2 abrupt-4xCO2 `day/pr`, `day/prc`**: `…_01410101-01510101.nc` has
  3,651 steps (a `noleap` decade is 3,650), so 0151-01-01 is also in the next
  file. Both `ok`. Dated Jan 29 2020, its neighbors Sep 2019. `prc` inferred
  from the gap table, not inspected. Open: same filename on ESGF?
- **MIROC-ES2L past1000 `Amon/tas`**: stub `…_085001-085011.nc` (11 steps,
  260 KB) beside `…_085001-104912.nc`, duplicating its first 11 months. Both
  dated Oct 19 2021, both in the 2022 list. Time axes compared, not values.
  Open: on ESGF? If not, quarantine after a value comparison.
- **GISS-E2-1-G lig127k r1i1p1f1** (`Amon/{tas,tasmax,tasmin,ts}`,
  `Omon/tos`): years 3000-3099 (two 50-year files) missing in all five; the
  2022 list has the same four `tas` files. Same in every variable, so a source
  or download cause, not corruption. Open: on ESGF? Then a `plan_repairs`
  candidate.

Open: ESGF is needed to pick which overlapping file to keep and which gaps are
upstream.

### Changes since the 2022 list
- **SAM0-UNICON piControl `Amon/tasmax`: 9 files gone** (open):
  `…_054101-055012.nc` to `…_062101-063012.nc` are in the 2022 list; the dir now
  starts at 063101. Not searched for elsewhere. Open: was this deliberate?
- **Only in the scan** (report-only): +651 `.nc` net (660 only in the scan, 9
  only in the list). 73 are the `CAS-ESM2-0` (33) and `SAM0-UNICON` (40)
  empties; `FGOALS-g3` (8) and `ScenarioMIP/CAS-ESM2-0` (1) are non-empty and
  `ok`.

## Non-CMIP6 holdings

Trees under `/proj/cmip6/data` besides `cmip6/` and `reanalysis/20crv3`:
`era5/`, `ocean_reanalysis/`, `cmems/`, `prism/`. First look 2026-11-13 on the
mount (listings, X1, headers of one or two files per product); full inventory
and health check (X2-X4, `audit-runbook.md` "Non-CMIP6 trees") on HPC
2026-11-14. Counts are from the X4 scans unless noted. Decision (2026-11-13):
catalog in place, no moves; anything below that implies a move is a
**proposal** and goes through plan, approve, apply.

### Classification (open, confirm)

| Tree / dir | Class | What it is |
|---|---|---|
| `era5/reanalysis` | catalog (curated, `products/era5.yaml`) | ERA5 single and pressure levels, 1hr/3hr/6hr/daily/monthly |
| `era5/quarantine_2022_20260807T140218` | decide | partial 2022 monthly means, replaced |
| `era5/download-scripts` | exclude | CDS download and merge scripts (provenance) |
| `ocean_reanalysis/glorys12v1` | catalog | GLORYS12V1 daily means, 1993-2024 |
| `cmems/GLOBAL_REANALYSIS_PHY_001_030` | catalog | GLORYS12V1 monthly means 1993-2024, plus statics |
| `ocean_reanalysis/CMEMS_ENSAMBLE/daily` | catalog | GREP v2 daily (4 members per file), 1993-2011 |
| `ocean_reanalysis/ORAS5/oras5/monthly/global-reanalysis-phy-001-031` | catalog | GREP v2 **monthly** 1993-2019 (not ORAS5) |
| `ocean_reanalysis/ORAS5/oras5/monthly/oras5` | decide | ORAS5 as yearly `.tar.gz` archives (opa0-4), a few extracted `.nc` |
| `ocean_reanalysis/bran2020` | catalog | BRAN2020 daily (north/south halves) and monthly |
| `ocean_reanalysis/sodav3122` | catalog | SODA 3.12.2, 5-day and monthly, regridded and native |
| `ocean_reanalysis/HYCOMvGOFS30`, `HYCOMvGOFS31` | catalog | HYCOM reanalysis daily snapshots, regional subset |
| `ocean_reanalysis/ECCOv5` | catalog (decide: alpha) | ECCO v5 alpha, lat-lon and nctiles |
| `ocean_reanalysis/cfsr932` | decide | CFSR (ds093.2) GRIB converted to `.grb2.nc`, `.gz`, wget scripts, `good/new/old` dirs |
| `ocean_reanalysis/emu` | exclude | EMU (ECCO Modeling Utilities) install: software, container, inputs |
| `ocean_reanalysis/NWA_downscaling` | exclude | empty |
| `prism/netcdf` | catalog | PRISM monthly precipitation, decadal NetCDF 1895-2019 |
| `prism/bil` | exclude (source) | the PRISM `.bil.zip` the NetCDF was made from |
| `.DS_Store`, `._*` | exclude | macOS litter |

Every `catalog` entry is in `manifests/catalogs.yaml`, disabled until its HPC
`check_nc` and `check_catalog.py` pass. Decision (2026-11-13): catalog ERA5
plus one or two ocean products and list the rest. Chosen 2026-11-15: ERA5,
GLORYS12V1 daily (1 dataset, 11,681 files) and SODA 3.12.2 (3 datasets, 5,586
files), all enabled, 0 invalid, `check_catalog.py` passing. Every disabled
product and the `listed:` entries (`oras5` archives, `cfsr932`) are on the
Other holdings page (`site/holdings.qmd`) with path, years, caveats and
citation. Check/rebuild cadence per product is undecided.

### X4 inventory (scan, 2026-11-14)
`summarize_other.py --depth era5=3 --depth cmems=2`. `.nc` files and TB
(decimal); "other" is non-`.nc`.

| Product (dir) | `.nc` | other | TB | Note |
|---|---:|---:|---:|---|
| `era5/reanalysis/single-levels/1hr` | 376,656 | 1 | 9.726 | |
| `era5/reanalysis/single-levels/3hr` | 3,568 | 1 | 0.213 | 1 deg short-name dirs |
| `era5/reanalysis/single-levels/6hr` | 13,200 | 1 | 1.601 | |
| `era5/reanalysis/single-levels/daily` | 24 | 0 | 0.002 | |
| `era5/reanalysis/single-levels/monthly-means` | 1,834 | 40 | 0.019 | other: `.lck` etc. |
| `era5/reanalysis/pressure-levels/3hr` | 507 | 0 | 1.786 | `uvq`, 1 deg |
| `era5/reanalysis/pressure-levels/6hr` | 80,559 | 0 | 11.492 | |
| `era5/reanalysis/pressure-levels/monthly-means` | 235 | 6 | 0.083 | |
| **ERA5 total** | **476,583** | | **24.92** | |
| `era5/quarantine_2022_*` | 28 | 0 | 0.0003 | not cataloged |
| `ocean_reanalysis/glorys12v1` | 11,681 | 2 | 19.373 | = every day 1993-01-01 to 2024's 359th |
| `cmems/.../global-reanalysis-phy-001-030-monthly` | 409 | 1 | 0.532 | = 373 months + 36 `_(1).nc` |
| `cmems/.../global-reanalysis-phy-001-030-statics` | 3 | 0 | 0.002 | |
| `ocean_reanalysis/CMEMS_ENSAMBLE` (GREP daily) | 5,723 | 0 | 12.576 | |
| `ocean_reanalysis/bran2020` | 64,642 | 4 | 15.602 | |
| `ocean_reanalysis/sodav3122` | 5,586 | 1 | 6.470 | = 2,774 + 38 + 2,774 |
| `ocean_reanalysis/HYCOMvGOFS30` | 9,383 | 10 | 21.377 | |
| `ocean_reanalysis/HYCOMvGOFS31` | 21,308 | 4 | 19.233 | |
| `ocean_reanalysis/ECCOv5` | 10,798 | 12 | 0.417 | 1 truncated |
| `ocean_reanalysis/ORAS5` (incl. GREP monthly) | 135 | 3,009 | 2.511 | mostly `.tar.gz` |
| `ocean_reanalysis/cfsr932` | 8,454 | 55 | 0.354 | |
| `ocean_reanalysis/emu` | 0 | 160,122 | 1.129 | software install |
| `prism/netcdf` | 13 | 0 | 0.005 | |
| `prism/bil` | 0 | 625 | 0.003 | source zips |

About 124.5 TB in 4 trees. Owners (uid, file counts):
`runs/2026-11-14_X4/inventory/owners.parquet`; most products have one or two
uids, ECCOv5, ORAS5 and cfsr932 several. `duplicates.parquet`: 458 file names in
more than one product (933 rows), no hard links: 364 HYCOM GOFS 3.0 names also
in GOFS31 (see HYCOM), 66 ERA5 names reused across frequency/level dirs with
different content (not duplicates), the 28 ERA5 quarantine names (expected),
and two unrelated `README`s (`bran2020`, `emu`).

### X4 check_nc: 36 damaged files (open, repair worklist)
All 614,746 `.nc` read: 614,710 `ok`, 17 `truncated` (HDF5 end-of-file address
past the file size), 19 `empty`. **None in ERA5, GLORYS, BRAN2020, SODA or
PRISM.** List: `nc_status != "ok"` in `runs/2026-11-14_X4/ncinfo_*`.

| Product | Damaged | Files |
|---|---:|---|
| GREP daily (`CMEMS_ENSAMBLE/daily`) | 12 truncated of 5,723 | `grepv2_daily_` 19931122, 19950103, 19950201, 19990626, 20020506, 20061107, 20061201, 20070605, 20070824, 20100103, 20110502, 20110601 (1-98% present; full size ~2.2 GB) |
| HYCOM GOFS 3.0 `analysis/` | 15 empty | `hycom_GLBu0.08_201308{01..15}_t00.nc` |
| HYCOM GOFS 3.1 (NW Atlantic) | 4 truncated | `2001/..._20011008`, `2007/..._20070511`, `2007/..._20071015`, `2008/..._20080120` (`hycom_GLBv0.08_53x_*_t00.nc`) |
| HYCOM GOFS 3.1 `global/2003` | 4 empty | `hycom_GLBv0.08_532_2003{0515,0525,0527,0529}12_t000.nc` |
| ECCOv5 | 1 truncated | `nctiles_monthly/THETA/THETA_1992.nc` |

Catalogs leave all 36 out (invalid). Proposal: re-fetch from the source
(CMEMS, HYCOM, ECCO) through the repair plan; damaged files stay until replaced.

### Header gap check
`find_gaps.py --ncinfo` per tree (dataset = dir with year dirs folded to `*`,
plus data variables; `ok` files only). Datasets: era5 138 ok, 8 overlap, 4 gap,
1 duplicate; ocean_reanalysis 27 ok, 14 gap, 5 bad_time_label, 1 duplicate;
cmems 3 fixed (statics), 1 duplicate; prism 1 fixed. The first run split three
ocean products and GLORYS monthly in two by variable order (false 2021-06 ->
2023-12 gap); generic `variable_id` and the gap grouping now sort variables
(re-run 2026-11-15). Findings are under each product below.

### ERA5
**Two time formats in one dataset** (open, affects users). Old-CDS files use
dim `time` (`gregorian`); new-CDS files (2022 monthly means, all 2023-2025)
use `valid_time` (`proleptic_gregorian`) plus `number` (scalar) and `expver`
(string). 50 of 145 variable dirs mix the two (5 pressure-level monthly means,
39 single-level monthly means, 6 of the 1hr variables). `ncinfo` finds either
(`time_name`) and the header gap check treats the calendars as one after 1582.
Tested on HPC (2026-11-15, `reanalysis.era5.mon.sfc.2m_temperature`, 47
files): a plain `to_dataset_dict()` fails, and so does the rename alone with
`use_cftime=True` (`DatetimeGregorian` vs `DatetimeProlepticGregorian`, `Cannot
combine along dimension 'time' with mixed types`). Two recipes work (564
steps, 1979-01 to 2025-12, sorted, unique):
- without `use_cftime` (ERA5 starts in 1979, so both decode to `datetime64`),
  `preprocess` renames `valid_time` to `time` and drops `number` and `expver`;
- with `use_cftime=True`, also `ds.convert_calendar("standard", dim="time",
  use_cftime=True)`.

Pressure-level monthly means also differ in level: old `level` (int32,
`millibars`, 1 to 1000), new `pressure_level` (float64, `hPa`, 1000 to 1), and
`z` is float64 vs float32. The time fix alone fails ("Coordinate variable level
is neither monotonically increasing nor decreasing"); renaming `pressure_level`
to `level` and `sortby("level")` combines all 47 files (561 steps; the 3
missing months are the 2022 gap). The result keeps the first file's `level`
units. `check_catalog.py` (3 sampled datasets, `use_cftime=True`) failed 2 of 3
on `whoi_era5`; it now retries with `check_catalog.normalize_cds` (rename,
drop, sort levels, convert calendar) and reports "only with preprocess". The
user recipe is on `site/catalog.qmd`. Don't rewrite files.

**Two grids.** Long-name dirs are 0.25 deg (1440 x 721; 37 pressure levels).
Short-name dirs are 1 deg (360 x 181): `3hr/{T2m,mfd,olr,sp,tcw,uv10m,vert-int}`,
`pressure-levels/3hr/uvq` (37 levels), `pressure-levels/6hr/Z` (200, 500,
850 hPa); `mfd` is a regional subset (160 x 76). The manifest maps them to CDS
names (T2m -> 2m_temperature, olr -> top_net_thermal_radiation (holds `ttr`),
mfd -> vertically_integrated_moisture_divergence, Z -> geopotential) with level
`sfc-1deg` / `plev-1deg` / `plev3-1deg`, so they are their own datasets and
don't mix grids.

**File granularity.** `single-levels/1hr`: daily files 1979-2022; 7 variables
(`10m_u/v_component_of_wind`, `2m_temperature`, `mean_sea_level_pressure`,
`sea_surface_temperature`, `surface_latent_heat_flux`, `geopotential`) are held
twice for 2023-2025, as daily files *and* yearly `YYYY_<var>.nc` (17,170 files
= 16,071 days 1979-2022 + 1,096 days + 3 yearly). Decision (2026-11-15):
catalog the daily files; the 21 yearly files are dropped from
`products/era5.yaml` and are catalog-invalid. Removing them from the store is a
later proposal. `6hr`: monthly files (single levels), daily files (pressure
levels). `daily/surface_latent_heat_flux`: 2022-2023 only.

**Coverage** ends differ by variable: 1hr to 2022-12-31 or 2025, 6hr pressure
levels to 2022-10-31, wave variables 2015 to 2022-10, 3hr 1 deg to
2021-03/05/08.

**Gaps and bad files** (open):
- Pressure-level monthly means 2022 stop at September (`geopotential`,
  `specific_humidity`, `temperature`): old 9-month downloads, never replaced.
  Proposal: re-fetch 2022.
- 6hr `volumetric_soil_water_layer_4`: `1998-11_...nc` holds October 1998 (same
  times as `1998-10_...nc`); November is missing. Proposal: re-fetch 1998-11.
- `1hr/evaporation/1993-05-03_evaporation_corrupted.nc` beside a good
  `1993-05-03_evaporation.nc`; reads `ok`, the catalog's name pattern leaves it
  out. Proposal: quarantine.
- `monthly-means/evaporation/x.nc`: another 9-month 2022 file, same size as the
  quarantined one but different bytes (a second download); catalog-invalid.
  Proposal: move to the quarantine.

**Quarantine** (resolved: reason; open: delete).
`era5/quarantine_2022_20260807T140218/single-levels/monthly-means/<var>/2022_<var>.nc`,
28 files of 9 months each (2022-01 to 2022-09, dim `time`), written 2022-12-02/03
by `grib_to_netcdf` (old CDS). The live file at the same path under
`era5/reanalysis` holds 12 months (`valid_time`, new CDS, `cfgrib` history
2026-07-29), mtime 2026-08-07 18:03-18:11, uid 17588, the quarantine dir's
owner (created 14:02 that day). Size and sha256 differ for all 28
(`runs/2026-11-13_X3/quarantine/compare.parquet`). The 11 wave-field variables
weren't quarantined: their 2022 files were complete (fetched 2023-01-14). So
the incomplete files were re-fetched and the old ones set aside; nothing in the
quarantine is needed. Proposal: delete after the owner confirms. Outside the
catalog root.

### GLORYS12V1 (open)
- **Monthly: 36 duplicate `_(1).nc` files.** 2019-2021 each have 24 files,
  `mercatorglorys12v1_gl12_mean_YYYYMM.nc` and `..._(1).nc`. Two pairs compared
  with `cmp` (2019-01, 2019-06): byte-identical, 1.29 GB each; the `(1)` copies
  are older (2023-11-19 vs 2024-01-22). ~46 GB. They show as `duplicate` in the
  gap check. Proposal: quarantine the `(1)` copies after a full checksum pass on
  HPC. 2024 has only January.
- **Daily: naming and product change.** 1993-2019
  `mercatorglorys12v1_gl12_mean_YYYYMMDD_RYYYYMMDD.nc`; from 2020
  `cmems_mod_glo_phy_my_0.083deg_P1D-m_..._YYYY-MM-DD.nc`; from mid-2021
  `cmems_mod_glo_phy_myint_...` (interim, 184 of 2021's files). Check on HPC
  that variables and units match across the change; note the my/myint switch on
  the site. 2024: 359 days.
- Daily and monthly are different products (daily vs monthly means of
  GLOBAL_(MULTIYEAR|REANALYSIS)_PHY_001_030), same grid and variables. Not
  duplicates.

### GREP (open)
- **Daily** (`CMEMS_ENSAMBLE/daily`): 4 members x 6 variables per file
  (`*_cglo`, `*_foam`, `*_glor`, `*_oras`), so `variable_id` is long;
  1993-01-01 to 2011-06-01. Besides the 12 truncated files, whole runs are
  missing: 1993-11-22..30, 1995-01-03..02-28, 1996-02-29, **1999-06-26..2001-06-30**,
  2002-05-06..10, 2006-11-07..12-31, 2007-06-05..30, 2007-08-24..31,
  2010-01-03..03-31. Most start at a truncated file and end at a month start:
  monthly downloads that stopped. The last file (2011-06-01) is truncated too,
  so the series probably ends where a download stopped. Proposal: re-fetch from
  CMEMS, or list as incomplete.
- **Monthly** is in the "ORAS5" dir: `ORAS5/oras5/monthly/global-reanalysis-phy-001-031`
  is GREP v2 monthly (27 files, 1993-2019), cataloged as `grep_monthly`.

### ORAS5 (open, decision)
Real ORAS5 is `oras5/{ORCA025,r1x1}/<var>/opa0-4/` as yearly `.tar.gz`, with a
few extracted `.nc` (e.g. `votemper/opa0`, `sossheig/opa0`,
`r1x1/sosaline/opa0`). Some member dirs are short (`vozocrte/opa2`: 3
archives; `ORCA025/vomecrtn` has no `opa0`). Decide: extract (where?) or leave
as archives and list without a catalog.

### BRAN2020 (open)
- **3 daily files with the wrong time** (headers read `ok`; units `days since
  1979-01-01`):

  | File (`daily/global/`) | Should be | Time in file |
  |---|---|---|
  | `1999/bran2020_daily_south_salt_1999_330.nc` | 1999-11-26 | 1984-06-08T07:31:13.83 |
  | `2000/bran2020_daily_north_temp_2000_085.nc` | 2000-03-25 | 1980-03-03T01:18:26.54 |
  | `2003/bran2020_daily_north_temp_2003_229.nc` | 2003-08-17 | 1982-01-20T07:31:13.83 |

  Fractional-second garbage suggests corrupted bytes, so the data is suspect
  too (not deep-read). Proposal: re-fetch from NCI; exclude them meanwhile.
- One file per variable (`eta_t`, `salt`, `temp`, `u`, `v`); daily files come in
  `north`/`south` halves (rules set `level`), which explains the ~7,000
  `duplicate`s per variable.
- Short years: 1998 has 2,325 daily files (to day 258), 2000 has 765 (to day
  85), of a normal 3,285. These are among the 14-16 gaps per variable.
- Time is `ocean_time` (no `axis`/`standard_name`; `ncinfo` finds it by
  units). `lat` units are `"tcell latitude"` (not CF).

### HYCOM (open; listed, not cataloged)
- GOFS 3.0 (`GLBu0.08` expts 190/191, 1992-2012) and 3.1 (`GLBv0.08_53x`,
  1994-2015). The 3.1 files seen are a NW Atlantic subset (30-60N,
  85.04-40W); 3.1 also has `hycom_bottomtempsaltssh_*` files (NETCDF3) and
  `global/` and `analysis/` dirs (not inspected). Snapshots, not means
  (`field_type: instantaneous`).
- **GOFS 3.0 copies in GOFS31**: 363 files of `HYCOMvGOFS30/1993` (expt 19.0,
  e.g. `hycom_GLBu0.08_190_1993072700_t000.nc`, 1,052.5 GB) are also in
  `HYCOMvGOFS31/global/1994`, same names and sizes (364 shared names in all).
  Byte identity not checked. Proposal: sha256 the pairs, then quarantine the
  GOFS31 copies.
- Gaps: GOFS 3.0 year dirs 9 (mostly 2-15 days), `analysis/` 50 plus 44 files
  whose time is the previous day's; GOFS 3.1 year dirs 44, bottom files 134,
  `analysis/` 11, `global/` 247.
- Layout (mount, 2026-11-18): GOFS 3.1 `analysis/` holds `GLBu0.08` expt
  90.9 files (2015), not GLBv0.08; GOFS 3.0 `analysis/` holds `GLBu0.08`
  2013 files. `global/1994` mixes the 363 GOFS 3.0 copies (`_190_`) with
  325 GOFS 3.1 files (`_530_`); `global/2003` is expts 532 and 533. The
  catalog entries give `analysis/`, `global/` and the NW Atlantic year dirs
  their own `level` and exclude the `_190_` copies.

### ECCOv5 (open)
- **`Version5/Alpha/nctiles_monthly/THETA/THETA_1992.nc` is truncated**:
  366,788,608 B of the 2,274,498,300 B its HDF5 superblock gives (16%). The
  other `THETA_*.nc` are exactly 2,274,498,300 B, dated 2020-10-06; this one is
  dated 2021-06-16, so a later copy or download stopped. The other 10,797
  files are `ok`. Proposal: re-fetch from ECCO; ask the owner (question 8).
- `latlon_daily/SSH.nc`, `latlon_monthly/{SALT,THETA,UVEL,VVEL}.nc`:
  `bad_time_label`, time values 0.5 / 1.0 with no `since` units. These
  `.nc` names are **directories** of one-step files (`SSH_19920101.nc`,
  9,497 daily; `THETA_199201.nc`, 312 monthly per variable; checked on the
  mount 2026-11-18), which is where most of the 10,798 files are. The
  catalog rules date them from the file names. Open: if the files share
  one time value, intake-esm cannot order them; check on HPC with
  `check_catalog.py`.
- `nctiles_monthly/{SALT,THETA}/`: yearly files on the LLC270 grid (13
  tiles of 270 x 270, 50 levels). `SALT.1` and `THETA.1` beside them are
  small non-NetCDF files (about 20 KB, 2021-06-16), not dirs.
- It's an alpha release. Decide: catalog it?

### SODA 3.12.2 (cataloged 2026-11-15)
`gridded/` (regridded `_reg_`, 2,774 5-day + 38 monthly) and `original/`
(native `_or_`, 2,774 5-day): 3 datasets, 5,586 files, 0 invalid. The monthly
axis is 30-day steps from mid-January, not calendar-month centers (2017:
2017-01-16 to 2017-12-12); a site caveat.

### cfsr932 (open, decision; listed only)
CFSR (NCAR RDA ds093.2) converted from GRIB, `.grb2.nc` names, some still
`.gz`; `pgbhnl` atmospheric files in `air_temp/`;
`ocean_var_monthly/{good,new,old}`; `global/` holds only wget scripts.
`daily` has 1 gap; in `global`, `ocnf.gdas.*` and `ocnh.gdas.*` have the same
times and variables (162 pairs). Decide what, if anything, is the product
(owner question 4).

### PRISM (report-only)
`prism/netcdf/monthly`: 13 decadal files, `ppt_monthly_1895-1899.nc` to
`..._2010-2019.nc`, all `ok`. No time coordinate (dims `t, y, x` with
`year(t)`, `month(t)`, `latitude(y)`, `longitude(x)`), so the gap check says
`fixed` and the catalog takes `frequency: mon` from a rule. Global attributes
name a WHOI contact and the converter (`aekholm-utils`).
`prism/bil/monthly/ppt/<year>/` covers the same 1895-2019: `stable_4kmM2`
annual zips early, `provisional_4kmM3` monthly zips for 2019. So the NetCDF
mixes PRISM versions; the catalog uses version `1` until that is sorted out.
Catalog trial (local, X1 ncinfo, 2026-11-18): 13 files, 1 dataset, decade
time ranges from the file names, join dim `t`; `check_catalog.py` fails
`to_dataset_dict`: intake-esm orders files with `combine_by_coords`, and no
PRISM dimension has a coordinate. Decide: keep it listed (users open the 13
files with `xr.open_mfdataset(..., combine="nested", concat_dim="t")`), or
add a `preprocess` that builds `t` from `year`/`month` (as `normalize_cds`
does for ERA5).

## CMIP5 tree `/proj/cmip6/cmip5` (open, decision)
Shallow look on hydra (`ls`, `find -maxdepth 3`, `du`; no scan or headers). A
sibling of `data/`. Owner `finn.wimberly`, group `sg-cmip6-mgr`; `data/` dated
Sep 29.
- **18T** apparent, all under `cmip5/data`; **67,178** files, **6,714** `.nc`
  (the other ~60,000 unidentified).
- `cmip5/data/`: `CMIP5/{output,output1,output1-climate,output1-climodes,output2}`,
  `PMIP3/{output,output1}`,
  `reanalysis/{era.20c,era.interim,ncep.reanalysis.i,noaa.cires.20crv2c}`,
  `stat/makeup`.
- Not checked: per-dir sizes, file types, whether the layout is the ESGF DRS,
  overlap of `reanalysis/` with `data/reanalysis` and `era5/`.
- Not in `manifests/catalogs.yaml` or `site/holdings.qmd`. Decide: list on
  Other holdings, catalog, or leave out.

## Owners (open)
Directory uids (`ls -la`; names not resolved): 12795 `era5/`, `cmems/`,
`prism/`, single-levels data; 17588 `era5/reanalysis` and the quarantine; 15795
`pressure-levels`, `download-scripts`; 13493 `ocean_reanalysis/`,
`glorys12v1`, `NWA_downscaling`; 100747 `CMEMS_ENSAMBLE`, HYCOM, `sodav3122`;
16165 `ECCOv5`, `ORAS5`, `bran2020`, `cfsr932`; 16360 `emu`. Per-file
`uid`/`gid` are in the X4 scan (`owners.parquet`).

Draft questions (not sent):
1. (uid 17588) The 2022 ERA5 monthly means in `era5/quarantine_2022_20260807T140218`
   are the 9-month files you replaced on 2026-08-07. OK to delete them? Is
   `monthly-means/evaporation/x.nc` the same kind of leftover?
2. (ERA5 owners) The 1 deg short-name dirs (`3hr/*`, `pressure-levels/6hr/Z`,
   `3hr/uvq`): where and how were they made (CDS grid option, or regridded
   here)? Is `mfd`'s region intentional? Are the `1hr/geopotential` daily files
   for 2023-2025 still needed beside the yearly ones?
3. (uid 16165) ORAS5: should the `.tar.gz` archives be extracted for users, and
   where? Are the short `opa*` member dirs incomplete downloads?
4. (uid 16165) cfsr932: which files are the product (`ocean_var_monthly/good`
   vs `new`/`old`), and can the `.gz` and wget scripts go?
5. (uid 13493 / 100747) GLORYS monthly `_(1).nc` copies: OK to remove after a
   checksum check? HYCOM: is the NW Atlantic subset the whole holding?
6. (uid 16165) BRAN2020 1998 and 2000 daily: stopped downloads or intentional?
7. (uid 16360) `emu`: leave as an install under `ocean_reanalysis`, or move out
   of the data tree (proposal)?
8. (owner of `ECCOv5/.../THETA/THETA_1992.nc`) The file was rewritten on
   2021-06-16 and is truncated (367 MB of 2.27 GB; the other years date from
   2020-10-06). Was it replaced on purpose, and from which source can it be
   fetched again?
9. License and citation for each product (drafts in `manifests/catalogs.yaml`,
   marked FILL IN / CONFIRM).

## Level log

| Level | Scope | Files | `nc_status` | Notes |
|---|---|---|---|---|
| L1 | CESM2 historical r1i1p1f1 Amon tas | 1 `.nc` | ok | locking bug found |
| L2 | `CMIP/NCAR/CESM2` | 5,166 `.nc` | all ok | count equals old list; 19.8 files/s; peak RSS 213 MB |
| L3 | `PMIP`, `CFMIP` | 3,227 `.nc` | all ok | counts equal old list; 25.6 files/s (PMIP); 0 attribute mismatches |
| L4 scan | `CMIP6` root, 41-43 shards | 732,134 `.nc` | see below | 763,175 entries, 162 TB, 31,072 datasets; 729,910 `ok`, 2,216 `bad_filename`, 8 `path_filename_mismatch`; +651 `.nc` vs 2022 list |
| L4 check_nc | same | 732,134 `.nc` | 717,672 ok, 14,462 empty | 7 `attr_mismatch`; calendars incl. `julian` (3,728) and `360_day` (12,869); gaps (filename-only): 27,546 ok, 3,137 fixed, 370 overlap, 106 gap |
| L4 catalog | full `build_catalog.sbatch` | 715,452 files, 31,031 datasets, 161.3 TB | n/a | 16,682 invalid (14,458 excluded, 2,216 `bad_filename`, 8 `path_filename_mismatch`); crawl 306-415 s on 8 cpus; `whoi_20crv3` 10 datasets; `check_catalog` passes with synchronous dask and `use_cftime=True` |
| X1 | `prism/netcdf/monthly`; `era5/.../monthly-means/2m_temperature` (laptop, mount) | 13 + 47 `.nc` | all ok (`--deep`) | counts and bytes equal `ls -l` (5,235,175,036 B; 693,964,995 B incl. 1 `.lck`); 1.5 and 0.8 files/s over SMB; ERA5 gaps `ok` 1979-01 to 2025-12 across `time`/`valid_time`; PRISM `fixed` |
| X2 | `ocean_reanalysis/bran2020`, one Slurm task each (hydra, 4G) | 64,642 `.nc` | all ok | scan 28 s (~2,300 files/s), MaxRSS 380 MB; check_nc 37:48 (28.5 files/s); sacct MaxRSS 4.19 GB is page cache (`/usr/bin/time -v` on 5,000 files: 147 MB), 4G is enough |
| X3 | `era5/{quarantine_*,download-scripts,_root_files}` (laptop: stat scans, quarantine headers + sha256); `ocean_reanalysis/{emu,cfsr932,ECCOv5,ORAS5}`, `prism/bil` (hydra, one task each) | 28 + 19,387 `.nc` | 19,386 ok, 1 truncated | quarantine reason found; 0 scan errors; entries / TB: emu 198,731 / 1.129, ORAS5 3,144 / 2.511, ECCOv5 10,810 / 0.417, cfsr932 8,509 / 0.354, bil 625 / 0.003; truncated: `THETA_1992.nc` |
| X4 scan | `era5`, `ocean_reanalysis`, `cmems`, `prism` (hydra arrays; era5 split by product) | 614,746 `.nc`, ~124.5 TB | 614,710 ok, 17 truncated, 19 empty | see X4 inventory, damaged files and header gap check |
| DU | `/proj/cmip6/data`, 26 shards (`du_shard.sh`, job 358247, %16) | 1,346,890 `.nc` + 194,947 other | n/a | 266,870.6 GiB apparent; catalog vs disk differences equal the invalid lists; split rerun 358301 identical |

In X2-X4, ncinfo rows equal the scan's `.nc` count.
