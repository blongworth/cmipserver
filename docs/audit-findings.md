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
They were added after the Feb 2022 file list. They are not CMIP6 DRS data and
must be kept out of the CMIP6 catalog (or given their own collection). Do not
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

## Level log

| Level | Scope | Files | `nc_status` | Notes |
|---|---|---|---|---|
| L1 | CESM2 historical r1i1p1f1 Amon tas | 1 `.nc` | ok | locking bug found |
| L2 | `CMIP/NCAR/CESM2` | 5,166 `.nc` | all ok | count equals old list; 19.8 files/s; peak RSS 213 MB |
| L3 | `PMIP`, `CFMIP` | 3,227 `.nc` | all ok | counts equal old list; 25.6 files/s (PMIP); 0 attribute mismatches |
| L4 scan | `CMIP6` root, 41-43 shards | 732,134 `.nc` | see below | 763,175 entries, 162 TB, 31,072 datasets; 729,910 `ok`, 2,216 `bad_filename`, 8 `path_filename_mismatch`; +651 `.nc` vs 2022 list (660 only in scan, 9 only in list) |
| L4 check_nc | same | 732,134 `.nc` | 717,672 ok, 14,462 empty | 7 `attr_mismatch`; calendars incl. `julian` (3,728) and `360_day` (12,869); gaps (filename-only): 27,546 ok, 3,137 fixed, 370 overlap, 106 gap |
| L4 catalog | full `build_catalog.sbatch` | 715,452 files, 31,031 datasets, 161.3 TB | n/a | 16,682 invalid (14,458 excluded as empty, 2,216 `bad_filename`, 8 `path_filename_mismatch`; sum 732,134); crawl 306-415 s on 8 cpus; `whoi_20crv3` 10 datasets; all `check_catalog` checks pass with synchronous dask and `use_cftime=True` |
