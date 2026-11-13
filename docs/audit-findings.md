# Audit findings

Things the audit found that need a decision or a later check. Add to this as
levels run; keep each entry to what was seen, how it was checked, and what is
still open. Run outputs are in `runs/<date>_<level>/` (gitignored).

Status: **open** (needs action or a decision), **report-only** (noted, no
action planned), **resolved**.

## Store layout

### Numeric version directories (open, Phase B)
Version dirs are plain numbers (`.../gn/1/`), not `vYYYYMMDD`. The old server's
file list uses the same layout (L1, 2026-11-12). `drs.py` now accepts them as
`ok` (scan column `version_scheme` = `numeric`), so `reorg` never moves them.
They will not match ESGF `vYYYYMMDD` versions, so `check_esgf` and
`plan_repairs` do nothing useful on them until we decide a mapping. See the
Phase B note in `audit-runbook.md` step 4.

### Bare-date version directories (open, decision)
`CMIP/CAS/CAS-ESM2-0` (and possibly others; count with `version_scheme =
'bare_date'` in the scan) uses `20201224`-style version dirs: a date without
the `v`. They scan as `ok` and are not reorganized. Unlike `1`, the date is
known, so the DRS path is just `v` + the dir name (a safe `reorg` candidate) and
they would match ESGF versions. Decide whether to rename.

### Stray files in the store (open, cleanup by Brett)
- `.DS_Store` files at `CMIP/` and `CMIP/NCAR/`. They scan as `not_nc`. Harmless.
- Empty `logs/` and `runs/2026-11-12_L3/` directly in `/proj/cmip6/data/cmip6`
  (created 2026-11-12 by a `mkdir` run from inside the store). They show up as
  scan shards. Remove with `rmdir` (refuses if not empty).

## Tooling

### HDF5 file locking on VAST (resolved)
Opening files with HDF5 locking on made good files report `header_error`
(`ncdump`: `NetCDF: HDF error`; `h5ls` fails; works with
`HDF5_USE_FILE_LOCKING=FALSE`). `ncinfo.py` now disables locking for its
read-only opens. Do the same in any other reader on the mount.

### Threaded dataset opens segfault on hydra (open, affects users)
`check_catalog` exited 139 (SIGSEGV) on hydra when `to_dask()` opened a sampled
dataset. `python -X faulthandler` put the crash inside dask's thread pool:
`intake_esm _delayed_open_ds` -> `xarray.open_dataset` ->
`netCDF4_.open_store_variable`, several threads at once. Opening a single file
with xarray (plain, with dask chunks, and with the synchronous scheduler) works.
Confirmed on the largest smoke-catalog dataset (a CESM2 `ta850` series, 438,000
daily steps over many files): default scheduler crashes (exit 135, SIGBUS; the
first run was exit 139, SIGSEGV), `dask.config.set(scheduler="synchronous")`
opens it fine. So this netCDF4/HDF5 build is not safe for concurrent opens.
Environment where it was seen: uv-managed standalone Python 3.14.10
(`~/.local/share/uv/python/cpython-3.14.10-linux-x86_64-gnu`), `netcdf4`
1.7.5.1.post1 from a PyPI wheel (bundled HDF5/netCDF-C), xarray 2026.9.0,
dask 2026.8.0, intake-esm 2025.7.9. Not tested with a system or conda
`netCDF4`/HDF5 (`module avail netcdf hdf5`), which might not crash; users on
other builds may see different behaviour. The workaround below does not depend
on the build.
`check_catalog.py` now runs the sample opens under
`dask.config.set(scheduler="synchronous")` and sets `HDF5_USE_FILE_LOCKING=FALSE`.
Open: `site/catalog.qmd`, `site/browse.qmd` and `site/notebooks/quickstart.ipynb`
document `to_dataset_dict()`/`to_dask()` with the default scheduler, which users
on hydra would run. Document the workaround (synchronous scheduler, locking off) in the site.

How the test was built (by hand on hydra, login node; repeat it after any
change to the Python, `netcdf4`, dask or xarray versions, or on a module build):
1. Build a small catalog that includes at least one dataset with many files
   (the CESM2 smoke catalog does: `build_catalog.py --root .../CMIP/NCAR/CESM2`).
   A one-file dataset never triggers it, so the first sample runs passed.
2. Pick the dataset with the most files. Read the CSV with
   `infer_schema_length=0` (so `version` stays a string), group by
   `master_id`, `version`, sort by count; the key is `master_id.version`.
3. In two separate processes (a crash kills the interpreter), with
   `HDF5_USE_FILE_LOCKING=FALSE` and `python -X faulthandler`:
   `intake.open_esm_datastore(<catalog json>)[key].to_dask()` with the default
   scheduler (expect exit 135 or 139 if the bug is present), then the same with
   `dask.config.set(scheduler="synchronous")` first (expect exit 0).
   Check `${PIPESTATUS[0]}` for the exit code; a `tail` hides it.
Not automated: pytest runs on the laptop and in CI, which have different HDF5
builds and no multi-file VAST data, and my macOS run never crashed. A real
regression test would need a synthetic multi-file dataset (a few small NetCDF
files) opened threaded in a subprocess on hydra, asserting a non-crash exit
code; it would have to be skipped off-HPC.

### Long-run datasets need `use_cftime=True` (open, affects users)

`to_dataset_dict()` on `CMIP6.CMIP.CNRM-CERFACS.CNRM-CM6-1.piControl.r1i1p1f2.Eday.hus850.gr.1`
(4 files, all `nc_status=ok`) fails with `Cannot combine along dimension 'time'
with mixed types. Found: DatetimeGregorian, Timestamp`. Years past 2262 do not
fit pandas datetime64[ns], so some files decode to cftime and others to
Timestamp. Not a data problem. Fix: `xarray_open_kwargs={"use_cftime": True}`.
`check_catalog` now passes it; the site docs should say so for piControl and
other long runs.

### `missing_value` warning (report-only)
`check_nc --deep` prints a netCDF4 `UserWarning: missing_value not used since it
cannot be safely cast to variable data type`. In CESM2 `tas`, `missing_value`
is a double and the variable is float. Harmless; not suppressed.

## Dataset findings

### CESM2 abrupt-4xCO2 `day/pr` and `day/prc`: one-day overlap (report-only)
`CMIP/NCAR/CESM2/abrupt-4xCO2/r1i1p1f1/day/{pr,prc}/gn/1`: file
`…_01410101-01510101.nc` has 3,651 time steps (a normal decade of `noleap` daily
data has 3,650), so 0151-01-01 is also the first day of the next file. Both
files are `ok` in `check_nc`. The odd file is dated Jan 29 2020, its neighbours
Sep 2019. `prc` was inferred from the gap table, not inspected.
Open: check whether ESGF publishes the same filename (then it is upstream).

### MIROC-ES2L past1000 `Amon/tas`: stub file overlap (report-only)
`PMIP/MIROC/MIROC-ES2L/past1000/r1i1p1f2/Amon/tas/gn/1`:
`…_085001-085011.nc` (11 monthly steps, 260 KB) sits beside
`…_085001-104912.nc` (2,400 steps from Jan 850), so it duplicates the first 11
months. Both are dated Oct 19 2021 and both are in the 2022 file list. Time
axes were compared; values were not.
Open: does ESGF publish it? If not, candidate to quarantine after a value
comparison (Phase B).

### GISS-E2-1-G lig127k r1i1p1f1: years 3000-3099 missing (report-only)
`PMIP/NASA-GISS/GISS-E2-1-G/lig127k/r1i1p1f1/{Amon/tas,tasmax,tasmin,ts;Omon/tos}/gn/1`
each hold only 2900-2949, 2950-2999, 3100-3149, 3150-3199. The two 50-year files
for 3000-3099 are missing in all five variables. The 2022 file list has the same
four `tas` files, so this predates the reorganization. Identical across
variables, which suggests a source or download-batch cause, not corruption.
Open: does ESGF have those years? If so, a repair candidate for `plan_repairs`.

### Non-CMIP6 products under `CMIP/NCAR` (open, decision)
578 files in `CMIP/NCAR/{LENS (254), CESM_HR (254), CESM2-LE (70)}` use native
CESM names (`b.e11.B20TRC5CNBDRD.f09_g16.001.cam.h0.PSL.185001-200512.nc`,
`cesm2_historical_aice_member_r10i1181p1f1.nc`), so they scan as `bad_filename`.
They were added after the Feb 2022 file list. One file from each directory was
opened: none has CMIP6 global attributes (`mip_era`, `activity_id`,
`source_id`, `experiment_id`). `LENS` is CESM1/CAM5 output (CMIP5 era, 2013),
`CESM_HR` has no CMIP metadata, `CESM2-LE` is native CICE output that only borrows
CMIP6-style member names. So they are native CESM model output, not published
CMIP6 files, and do not fit the DRS. Currently excluded from the catalog as
`bad_filename` (578 of the 2,216; the other 1,638 are the SAM0-UNICON files
below). Decide: leave out, or add as their own product/collection. Do not
`reorg` them.

### SAM0-UNICON non-standard filenames (open, decision)
1,638 files (e.g. `tauuo_Omon_SAM0-UNICON_piControl_r1i1p1f1_gn_055101_056012.nc`,
underscore between six-digit dates) scan as `bad_filename`; all are also in the
2022 list. Plus three `fx` files with random suffixes (`…_gnRx4IoZ65553.nc`).
Open: upstream names or a WHOI rename? If they should be in the catalog, a
reviewed rename is needed; do not loosen the parser.

### `path_filename_mismatch`: 8 files, all also in the 2022 list (report-only)
- `KACE-1-0-G` (4): `tasmin`/`tasmax` files named `…_day_…_gr_18500101-20001230.nc`
  in `Amon` directories.
- `SAM0-UNICON` fx (3): the random-suffix filenames above.
- `FIO-QLNM/FIO-ESM-2-0/piControl` (1): `thetao_Omon_PCMDI-test-1-0_piControl-withism_r3i1p1f1_…`,
  a test file for a different model and experiment.

### Zero-byte files: 14,462 `empty` (open, Phase B repair worklist)
L4 `check_nc`: 14,462 of 732,134 files (2.0%) are 0 bytes, `nc_status = empty`:
`EC-Earth3-CC` 14,378, `SAM0-UNICON` 44, `CAS-ESM2-0` 33, `KACE-1-0-G` 4,
`CESM2-WACCM` 3. `EC-Earth3-CC` is in `1pctCO2` (7,427 of 16,176 files) and
`abrupt-4xCO2` (6,950 of 16,176), single-year monthly files such as
`ts_Amon_EC-Earth3-CC_abrupt-4xCO2_r1i1p1f1_gr_186401-186412.nc`; `piControl`,
`amip` and `historical` are fine (1 empty `historical` file).
All 14,378 `EC-Earth3-CC` files are dated 2022-02-03 (12,652) or 2022-02-04
(1,726), and the `CAS-ESM2-0`/`SAM0-UNICON` samples checked were dated
2022-02-03: one bulk event (failed copy/restore?) on those two days. Not
checked for the other groups.
`find_gaps` ignores size, so empty files count as present and hide real gaps
(it reads filenames only).
The 4 `KACE-1-0-G` empties are the daily `tasmax`/`tasmin` files filed under
`Amon` (see below), so the catalog build lists them as
`path_filename_mismatch`, not `excluded` (14,458 `excluded` + 4). Build the
Phase B worklist from `ncinfo` (`nc_status = empty`), not from the catalog's
invalid list.
Open: re-fetch from ESGF (Phase B), after the versioning question.

### 9 other files only in the scan, not in the 2022 list (report-only)
`FGOALS-g3` (8) and `ScenarioMIP/CAS-ESM2-0` (1) are non-empty and `ok`; the
other 73 files only in the scan (`CAS-ESM2-0` 33, `SAM0-UNICON` 40) are the
empty ones above.

### KACE-1-0-G tasmax/tasmin: daily data labelled `Amon` (report-only)
`attr_mismatch` (4 files, `historical` and `ssp585` `tasmax`/`tasmin`): filename
and directory say `Amon`, the file's `table_id` attribute says `day`. Together
with the 4 `path_filename_mismatch` files (`…_day_…` in `Amon` dirs) the
`KACE-1-0-G` `tasmax`/`tasmin` holdings mix table labels. The 3 other
`attr_mismatch` files are the `SAM0-UNICON` random-suffix `fx` files
(`grid_label`). Total `attr_mismatch`: 7 of 732,134.

### SAM0-UNICON piControl `Amon/tasmax`: 9 files gone since 2022 (open)
`…_054101-055012.nc` through `…_062101-063012.nc` are in the 2022 file list but
not in the store (the directory now starts at 063101). Removed or moved after
Feb 2022; not searched for elsewhere. Check with Brett whether this was
deliberate.

### Overlaps (370 datasets) and gaps (106 datasets) in L4 (open, Phase B)
Filename-based (`find_gaps`, no `--core`, empty files counted as present).
Overlaps: `NorESM2-MM` 97, `TaiESM1` 59, `FGOALS-f3-L` 53, `FGOALS-g3` 34,
`ScenarioMIP/NorESM2-LM` 21, then about 20 more models with 1-16 each. Gaps:
`NorESM2-LM` 19, `SAM0-UNICON` 12, `CESM2-WACCM-FV2` 10, `E3SM-1-0` 9,
`CESM2-FV2` 8, `IITM-ESM` 7, `CNRM-CM6-1-HR` 6, `TaiESM1` 6, `GISS-E2-1-G` 5, and
about 12 more. Counts for the biggest overlap groups equal the counts
recomputed from the 2022 file list, so they predate the reorganization.
Typical overlap: two files in one `/1/` directory covering nearly the same
span (`000101-001012` beside `000102-001012` in `NorESM2-MM`; `185001-199912`
beside `185001-200912` in `FGOALS-f3-L`). Likely cause: more than one ESGF
version of a variable downloaded into the single `1` directory, not
corruption; not confirmed. Gaps are real missing time chunks (examples:
`CIESM` ssp585 `rtmt` 210012 -> 402901; `GISS-E2-1-G` lig127k 299912 -> 310001).
Not yet looked at: whether any of the 106 gaps are false (the `julian` calendar,
3,728 files, was not checked). Open: need ESGF to decide which overlapping file
to keep and which gaps are upstream.

## Non-CMIP6 holdings

Trees under `/proj/cmip6/data` besides `cmip6/` and `reanalysis/20crv3`:
`era5/`, `ocean_reanalysis/`, `cmems/`, `prism/`. Seen on the mounted store
(`~/mnt/cmip6`, read-only) on 2026-11-13: directory listings, X1 scans and
headers, and header reads of one or two files per product. The full
inventory and health check (X2-X4) still have to run on HPC
(`audit-runbook.md`, "Non-CMIP6 trees"); file counts below are from `ls`, not
from a scan, unless marked X1. Decision (Brett, 2026-11-13): catalog in place,
no moves; anything below that suggests a move is a **proposal**.

### Classification (open, confirm with Brett)

| Tree / dir | Class | What it is |
|---|---|---|
| `era5/reanalysis` | catalog (curated, `products/era5.yaml`) | ERA5 single and pressure levels, 1hr/3hr/6hr/daily/monthly |
| `era5/quarantine_2022_20260807T140218` | decide | partial 2022 monthly means, replaced (below) |
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
| `.DS_Store`, `._*` | exclude | macOS litter (in `data/`, `era5/`, `era5/reanalysis/*`, `ocean_reanalysis/`, `ORAS5/`) |

Every `catalog` entry is in `manifests/catalogs.yaml`, **disabled** until its
HPC check_nc run and `check_catalog.py` pass.

### ERA5 quarantine: partial 2022 monthly means (resolved: reason; open: delete?)
`era5/quarantine_2022_20260807T140218/single-levels/monthly-means/<var>/2022_<var>.nc`,
28 files. Each holds 9 months (2022-01 to 2022-09, dim `time`), written
2022-12-02/03 by `grib_to_netcdf` (old CDS). The live file at the same path
under `era5/reanalysis` holds all 12 months (dim `valid_time`, new CDS,
`cfgrib` history 2026-07-29), mtime 2026-08-07 18:03-18:11, uid 17588 (the
quarantine dir's owner, created 14:02 the same day). Size and sha256 differ for
all 28 (`runs/2026-11-13_X3/quarantine/compare.parquet`). The other 11
monthly-means variables (wave fields) were not quarantined: their 2022 files
were fetched complete on 2023-01-14. So: someone re-fetched incomplete 2022
files and kept the old ones aside. Nothing in the quarantine is needed.
Proposal: delete after the owner confirms. Not catalogued (outside the root).

### ERA5 layout and format (open, affects users)
- **Two time formats in one dataset.** Old-CDS files use dim `time`
  (`gregorian`); new-CDS files (2022 monthly means, all 2023-2025 files) use
  `valid_time` (`proleptic_gregorian`) plus `number` (scalar) and `expver`
  (string) variables. A dataset spanning 2022 or 2023 has both, so
  intake-esm's `join_existing` on `time` will fail for it (not yet run;
  check on HPC). `ncinfo` finds either (`time_name`), and the header gap
  check treats the calendars as one after 1582: X1 `monthly-means/2m_temperature`
  is `ok`, 1979-01 to 2025-12, 47 files. Proposal: document a
  `preprocess=lambda ds: ds.rename(valid_time="time")` for users; do not
  rewrite files.
- **Two grids.** Long-name dirs are 0.25 deg (1440 x 721), pressure levels 37.
  Short-name dirs are 1 deg (360 x 181): `3hr/{T2m,mfd,olr,sp,tcw,uv10m,vert-int}`,
  `pressure-levels/3hr/uvq` (37 levels), `pressure-levels/6hr/Z` (only 200,
  500, 850 hPa); `mfd` is a regional subset (160 x 76). The manifest aliases
  the single-variable ones to CDS names (T2m -> 2m_temperature, sp, olr ->
  top_net_thermal_radiation (file holds `ttr`), mfd ->
  vertically_integrated_moisture_divergence, Z -> geopotential) but gives them
  level `sfc-1deg` / `plev-1deg` / `plev3-1deg`, so they are their own
  datasets. Grouping them with the 0.25 deg data, as first planned, would mix
  grids.
- **Mixed file granularity.** `single-levels/1hr`: daily files 1979-2022,
  yearly files 2023-2025 for 7 variables. `1hr/geopotential` has **both**
  daily files for 2023-2025 (1,096) and yearly files for the same years
  (duplicate). `6hr`: monthly files (single levels) and daily files
  (pressure levels). `daily/surface_latent_heat_flux`: 2022-2023 only.
- **Strays.** `monthly-means/evaporation/x.nc`: another partial (9-month)
  2022 evaporation file, same size as the quarantined one but different bytes
  (a second download); catalog invalid (matches no group). Proposal: move
  to the quarantine with the others. `*.nc.lck` lock files from 2023-01-14
  (e.g. `2023_2m_temperature.nc.lck`): not `.nc`, not crawled.
- Coverage ends differ by variable: 1hr to 2022-12-31 or 2025, 6hr pressure
  levels to 2022-10-31, wave variables 2015-2022-10, 3hr 1 deg to 2021-03/05/08.

### Ocean products (open)
- **GLORYS12V1 monthly: duplicate `_(1).nc` files.** 2019-2021 each have 24
  files: `mercatorglorys12v1_gl12_mean_YYYYMM.nc` and `..._(1).nc`. Two pairs
  compared with `cmp` (2019-01, 2019-06): byte-identical, 1.29 GB each; the
  `(1)` copies are older (2023-11-19 vs 2024-01-22). Likely all 36 pairs
  (~46 GB). Proposal: quarantine the `(1)` copies after a full checksum pass
  on HPC. 2024 has only January.
- **GLORYS12V1 daily: naming and product change.** 1993-2019
  `mercatorglorys12v1_gl12_mean_YYYYMMDD_RYYYYMMDD.nc`; from 2020
  `cmems_mod_glo_phy_my_0.083deg_P1D-m_..._YYYY-MM-DD.nc`, and from mid-2021
  `cmems_mod_glo_phy_myint_...` (interim product, 184 of 2021's files). Check
  on HPC that variables and units match across the change; record the
  my/myint switch on the site. 2024: 359 days.
- **GLORYS daily vs monthly** are different products (daily means vs monthly
  means of GLOBAL_(MULTIYEAR|REANALYSIS)_PHY_001_030), same grid and
  variables; both catalogued. Not duplicates.
- **"ORAS5" dir holds GREP monthly.** `ORAS5/oras5/monthly/global-reanalysis-phy-001-031`
  is GREP v2 monthly (27 files 1993-2019), catalogued as `grep_monthly`. Real
  ORAS5 is under `oras5/{ORCA025,r1x1}/<var>/opa0-4/` as yearly `.tar.gz`
  archives, with a few extracted `.nc` (e.g. `votemper/opa0`,
  `sossheig/opa0`, `r1x1/sosaline/opa0`); some member dirs are short
  (`vozocrte/opa2`: 3 archives, `ORCA025/vomecrtn` has no `opa0`). Decide:
  extract (where?) or leave as archives and list on the site without a
  catalog.
- **BRAN2020.** Variables are one file each (`eta_t`, `salt`, `temp`, `u`,
  `v`); daily files come in `north`/`south` halves (rules set `level`).
  Daily year dirs are uneven: 1998 has 2,325 files (to day 258), 2000 has 765
  (to day 85) of a normal 3,285. Time is `ocean_time` (no `axis`/
  `standard_name`; `ncinfo` finds it by units). `lat` units are
  `"tcell latitude"` (not CF).
- **HYCOM.** GOFS 3.0 (`GLBu0.08` expts 190/191, 1992-2012) and 3.1
  (`GLBv0.08_53x`, 1994-2015). The 3.1 files seen are a NW Atlantic subset
  (30-60N, 85.04-40W), not global; 3.1 also has `hycom_bottomtempsaltssh_*`
  files (NETCDF3) beside the 3-D files, and `global/` and `analysis/` dirs
  (not inspected). Strays: `index.html` (GOFS30/1995), `wget-log`
  (GOFS31/1994, 2006), a `1992/` dir inside `GOFS30/1999/`. Snapshots, not
  means (`field_type: instantaneous`).
- **SODA 3.12.2.** `gridded/` (regridded `_reg_`, 2,774 5-day files + 38
  monthly) and `original/` (native `_or_`, 2,774 5-day). The monthly file
  for 2017 runs 2017-01-16 to 2017-12-12 with a 30-day median step; check
  the calendar of the monthly series on HPC.
- **GREP daily** holds 4 members x 6 variables per file (`*_cglo`, `*_foam`,
  `*_glor`, `*_oras`), so its `variable_id` is long; 1993-01-01 to 2011-06-01
  (5,723 files).
- **cfsr932**: CFSR (NCAR RDA ds093.2) converted from GRIB with `.grb2.nc`
  names, some still `.gz`, `pgbhnl` atmospheric files in `air_temp/`,
  `ocean_var_monthly/{good,new,old}`, `global/` holds only wget scripts.
  Decide what (if anything) is the product before cataloguing.

### PRISM (report-only)
`prism/netcdf/monthly`: 13 decadal files, `ppt_monthly_1895-1899.nc` to
`..._2010-2019.nc` (X1: counts and bytes equal `ls -l`, all `ok`). No time
coordinate: dims `t, y, x` with `year(t)` and `month(t)`, `latitude(y)`,
`longitude(x)`, so the header gap check reports `fixed` and the catalog takes
`frequency: mon` from a rule. The global attrs name a WHOI contact and the
converter (`aekholm-utils`). `prism/bil/monthly/ppt/<year>/`: 125 year dirs
(1895-2019), the same span as the NetCDF: `stable_4kmM2` annual zips for the
early years, `provisional_4kmM3` monthly zips for 2019. So the NetCDF mixes
PRISM versions (M2 stable, M3 provisional); the catalog uses the default
version `1` until that is sorted out.

### Owners (open, for Brett)
uids of the directories (`ls -la`, numeric; names not resolved here):
`era5/` 12795 (also `cmems/`, `prism/`, single-levels data), `era5/reanalysis`
and the quarantine 17588, `pressure-levels` and `download-scripts` 15795,
`ocean_reanalysis/` and `glorys12v1`, `NWA_downscaling` 13493,
`CMEMS_ENSAMBLE`, HYCOM, `sodav3122` 100747, `ECCOv5`, `ORAS5`, `bran2020`,
`cfsr932` 16165, `emu` 16360. The X4 scan has per-file `uid`/`gid`
(`summarize_other.py` writes `owners.parquet`). Draft questions for Brett to
send (not sent):

1. (uid 17588) The 2022 ERA5 monthly means in `era5/quarantine_2022_20260807T140218`
   are the 9-month files you replaced on 2026-08-07. OK to delete them? Is
   `monthly-means/evaporation/x.nc` the same kind of leftover?
2. (ERA5 owners) The 1 deg short-name dirs (`3hr/*`, `pressure-levels/6hr/Z`,
   `3hr/uvq`): where and how were they made (CDS grid option, or regridded
   here)? Is `mfd`'s region intentional? Are `1hr/geopotential` daily files
   for 2023-2025 still needed beside the yearly ones?
3. (uid 16165) ORAS5: should the `.tar.gz` archives be extracted for users,
   and where? Are the short `opa*` member dirs incomplete downloads?
4. (uid 16165) cfsr932: which files are the product (`ocean_var_monthly/good`
   vs `new`/`old`), and can the `.gz` and wget scripts go?
5. (uid 13493 / 100747) GLORYS monthly `_(1).nc` copies: OK to remove after a
   checksum check? HYCOM: is the NW Atlantic subset the whole holding?
6. (uid 16165) BRAN2020 1998 and 2000 daily: stopped downloads or intentional?
7. (uid 16360) `emu`: leave as an install under `ocean_reanalysis`, or move
   out of the data tree (proposal)?
8. Licence and citation for each product (drafts in `manifests/catalogs.yaml`,
   marked FILL IN / CONFIRM).

### Tooling (resolved / open)
- **`columns_with_iterables` does not work** (resolved by not using it):
  intake-esm 2025.7.9 with polars 2.1 fails to open a catalog given
  `columns_with_iterables` (`json_decode() missing ... 'dtype'`, seen in
  `check_catalog`). Generic catalogs store multi-variable `variable_id` as a
  comma-joined string; `cat.search(variable_id=...)` needs the exact string.
  Revisit when intake-esm is fixed.
- `ncinfo.py`: `data_vars`, `time_name`, `time_step_days` and generic global
  attrs (`title`, `source`, `institution`, `product`, `dataset`,
  `references`, `citation`, `licence`, `history` cut to 500 chars). Time is
  `time`, else `valid_time`, else a dimension coordinate with `axis=T`,
  `standard_name=time` or `<unit> since` units.
- A stat-only scan of `ocean_reanalysis/emu` over the SMB mount did not
  finish in about 30 minutes (>100k entries). Scan non-CMIP trees on HPC.

## Level log

| Level | Scope | Files | `nc_status` | Notes |
|---|---|---|---|---|
| L1 | CESM2 historical r1i1p1f1 Amon tas | 1 `.nc` | ok | locking bug found |
| L2 | `CMIP/NCAR/CESM2` | 5,166 `.nc` | all ok | count equals old list; 19.8 files/s; peak RSS 213 MB |
| L3 | `PMIP`, `CFMIP` | 3,227 `.nc` | all ok | counts equal old list; 25.6 files/s (PMIP); 0 attribute mismatches |
| L4 scan | `CMIP6` root, 41-43 shards | 732,134 `.nc` | see below | 763,175 entries, 162 TB, 31,072 datasets; 729,910 `ok`, 2,216 `bad_filename`, 8 `path_filename_mismatch`; +651 `.nc` vs 2022 list (660 only in scan, 9 only in list) |
| L4 check_nc | same | 732,134 `.nc` | 717,672 ok, 14,462 empty | 7 `attr_mismatch`; calendars incl. `julian` (3,728) and `360_day` (12,869); gaps (filename-only): 27,546 ok, 3,137 fixed, 370 overlap, 106 gap |
| L4 catalog | full `build_catalog.sbatch` | 715,452 files, 31,031 datasets, 161.3 TB | n/a | 16,682 invalid (14,458 excluded as empty, 2,216 `bad_filename`, 8 `path_filename_mismatch`; sum 732,134); crawl 306-415 s on 8 cpus; `whoi_20crv3` 10 datasets; all `check_catalog` checks pass with synchronous dask and `use_cftime=True` |
| X1 | `prism/netcdf/monthly`; `era5/.../monthly-means/2m_temperature` (laptop, mount) | 13 + 47 `.nc` | all ok (`--deep`) | counts and bytes equal `ls -l` (5,235,175,036 B; 693,964,995 B incl. 1 `.lck`); 1.5 and 0.8 files/s over SMB; ERA5 header gaps `ok` 1979-01 to 2025-12 across `time`/`valid_time`; PRISM `fixed` (no time axis) |
| X3 (partial) | `era5/{quarantine_*,download-scripts,_root_files}` stat scans; quarantine headers + sha256 | 28 `.nc` (quarantine) | ok | quarantine reason found (above); `ocean_reanalysis/emu` scan >100k entries, unfinished on the laptop; rest of X3, X2, X4 to run on HPC |
