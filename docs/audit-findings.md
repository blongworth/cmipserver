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

## Level log

| Level | Scope | Files | `nc_status` | Notes |
|---|---|---|---|---|
| L1 | CESM2 historical r1i1p1f1 Amon tas | 1 `.nc` | ok | locking bug found |
| L2 | `CMIP/NCAR/CESM2` | 5,166 `.nc` | all ok | count equals old list; 19.8 files/s; peak RSS 213 MB |
| L3 | `PMIP`, `CFMIP` | 3,227 `.nc` | all ok | counts equal old list; 25.6 files/s (PMIP); 0 attribute mismatches |
