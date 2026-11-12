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

### Zero-byte files (open, Phase B repair worklist)
82 new `.nc` files of 0 bytes, all dated Feb 3 2022: `CAS-ESM2-0` (34),
`SAM0-UNICON` (40), `FGOALS-g3` (8; not inspected). Seen on `CAS-ESM2-0` and
`SAM0-UNICON`; more may exist in files both lists share (SAM0-UNICON has 44
zero-byte files in all). `check_nc` should report them as `empty`.

### SAM0-UNICON piControl `Amon/tasmax`: 9 files gone since 2022 (open)
`…_054101-055012.nc` through `…_062101-063012.nc` are in the 2022 file list but
not in the store (the directory now starts at 063101). Removed or moved after
Feb 2022; not searched for elsewhere. Check with Brett whether this was
deliberate.

## Level log

| Level | Scope | Files | `nc_status` | Notes |
|---|---|---|---|---|
| L1 | CESM2 historical r1i1p1f1 Amon tas | 1 `.nc` | ok | locking bug found |
| L2 | `CMIP/NCAR/CESM2` | 5,166 `.nc` | all ok | count equals old list; 19.8 files/s; peak RSS 213 MB |
| L3 | `PMIP`, `CFMIP` | 3,227 `.nc` | all ok | counts equal old list; 25.6 files/s (PMIP); 0 attribute mismatches |
| L4 scan | `CMIP6` root, 41-43 shards | 732,134 `.nc` | not run yet | 763,175 entries, 162 TB, 31,072 datasets; 729,910 `ok`, 2,216 `bad_filename`, 8 `path_filename_mismatch`; +651 `.nc` vs 2022 list (660 only in scan, 9 only in list) |
