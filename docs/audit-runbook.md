# Audit, repair and catalog runbook

Commands for the HPC steps, in order. Every data-touching script is
read-only or dry-run by default. Keep each level's outputs in
`runs/<date>_<level>/` (gitignored) so levels can be compared.

## Rollout ladder

| Level | Target | Where | Gate |
|---|---|---|---|
| L0 | `tests/` (`messy_store` fixture) | laptop / GHA | pytest green |
| L1 | one dataset dir | HPC login | outputs match `ls -l`, `ncdump -h`, `sha256sum` |
| L2 | one `source_id` | Slurm, 1 task | record error rate, files/s, memory; extrapolate |
| L3 | one odd top-level shard | Slurm, 1 task | odd paths reviewed, no new error classes |
| L4 | full store | Slurm array `%20` | — |

Below, `R=runs/$(date +%F)_L1` etc. and `ROOT=/proj/cmip6`.

## 1. Scan (read-only)

```bash
uv run scripts/scan_holdings.py --root $ROOT --list-shards
uv run scripts/scan_holdings.py --root $ROOT --out $R/scan --shard CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas
uv run scripts/summarize_scan.py --scan $R/scan --out $R/inventory --csv
```

L4: `slurm/scan_holdings.sbatch` (see its header).

## 2. NetCDF check (read-only)

```bash
uv run scripts/check_nc.py --scan $R/scan --out $R/ncinfo --shard <tag> [--prefix <dir>] [--limit N] [--deep]
```

L4: `slurm/check_nc.sbatch` (`DEEP=1` reads the last time step of every file).
Output `nc_status`: ok / empty / not_hdf5_or_nc / truncated / header_error / read_error.

## 3. Gaps (offline)

```bash
uv run scripts/find_gaps.py --scan $R/scan --out $R/gaps --core manifests/core.yaml
```

## 4. ESGF (DTN; needs outbound HTTPS)

`--index-url` has no default. Confirm the search endpoint first.

```bash
uv run scripts/check_esgf.py --scan $R/scan --out $R/esgf --index-url <URL> --source-id CESM2
uv run scripts/check_esgf.py --scan $R/scan --out $R/esgf --compare-only   # offline re-run
```

## 5. Checksums (candidate set only)

```bash
uv run scripts/verify_checksums.py select --scan $R/scan --ncinfo $R/ncinfo \
    --esgf $R/esgf/esgf_compare.parquet --out $R/checksums/candidates.parquet
```

Then `slurm/verify_checksums.sbatch` (see its header).

## 6. Repair plan → staging download

```bash
uv run scripts/plan_repairs.py --esgf $R/esgf --ncinfo $R/ncinfo \
    --checksums $R/checksums --gaps $R/gaps --out manifests/repairs/<date>.yaml
# review manifests/repairs/<date>.md
uv run scripts/download_esgf.py --repairs manifests/repairs/<date>.yaml \
    --staging-root <STAGING> --log $R/download.parquet            # dry run
uv run scripts/download_esgf.py ... --execute
```

## 7. Moves (written approval required)

```bash
uv run scripts/plan_moves.py repairs --repairs manifests/repairs/<date>.yaml \
    --staging-root <STAGING> --quarantine-root <QUARANTINE> \
    --store-root /proj/cmip6 --drs-root <DRS_ROOT> --verify \
    --out manifests/moves/<date>-repairs.yaml
uv run scripts/apply_moves.py --manifest manifests/moves/<date>-repairs.yaml --log $R/moves.jsonl
# Brett: set approved_by / approved_on in the YAML, then
uv run scripts/apply_moves.py ... --execute --limit 2     # one dataset first
uv run scripts/rollback_moves.py --log $R/moves.jsonl     # dry run; --execute to undo
```

Moves use `link` + `unlink`, so they never overwrite and need the same
filesystem. Keep the move log; rollback replays it.

## 8. Catalog

```bash
uv run scripts/build_catalog.py --drs-root <DRS_ROOT> --root <DRS_ROOT>/CMIP/NCAR/CESM2 \
    --ncinfo $R/ncinfo --out $R/catalog
uv run scripts/check_catalog.py --catalog $R/catalog/whoi_cmip6.json --report $R/catalog/check.json
uv run scripts/summarize_catalog.py --catalog $R/catalog/whoi_cmip6.csv --out site/data
```

## Placeholders to confirm

- `<DRS_ROOT>`: `manifests/catalogs.yaml` says `/proj/cmip6/cmip6` (the
  site's sample layout). Confirm it from the Step 1 scan.
- `<STAGING>`, `<QUARANTINE>`: paths and owner (service account?).
- ESGF `--index-url`.
