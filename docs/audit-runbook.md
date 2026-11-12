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

Set these in every new shell (an unset `$R` expands to `/scan` and fails):

```bash
export ROOT=/proj/cmip6/data/cmip6
export R=runs/$(date +%F)_L1     # _L2, _L3, ... for later levels
mkdir -p $R
```

Use that
root, not `/proj/cmip6`: shards are the top-level dirs under the root, and
`/proj/cmip6` would make `data/` one giant shard and scan `bin/`, `etc/`,
`var/` and `tmp/` too. Reanalysis (`/proj/cmip6/data/reanalysis`) is scanned
separately, only if needed. Do not scan the full store outside this root.

## HPC setup (once per account)

Install uv in your home directory (no admin rights, nothing global). If
`module avail uv` lists a module, `module load uv` instead.

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh   # -> ~/.local/bin/uv
cat >> ~/.bashrc <<'SH'
export PATH="$HOME/.local/bin:$PATH"
# If home quota is small, keep uv's cache and Pythons in project space:
# export UV_CACHE_DIR=<project space>/uv-cache
# export UV_PYTHON_INSTALL_DIR=<project space>/uv-python
export UV_LINK_MODE=copy   # cache and repo on different filesystems: copy, don't warn
SH
source ~/.bashrc && uv --version

git clone <repo url> whoi-cmip-reorg-code && cd whoi-cmip-reorg-code
uv sync          # login node: fetches Python 3.14 and packages into .venv
```

Run `uv sync` on a login node. Slurm jobs use `uv run --frozen` with the
`.venv` already built, so they don't need internet access for packages.

## 1. Scan (read-only)

```bash
uv run scripts/scan_holdings.py --root $ROOT --list-shards
uv run scripts/scan_holdings.py --root $ROOT --out $R/scan --shard CMIP/NCAR/CESM2/historical/r1i1p1f1/Amon/tas
uv run scripts/summarize_scan.py --scan $R/scan --out $R/inventory --csv
```

L4: `slurm/scan_holdings.sbatch` (see its header). `CMIP` is 92% of the files
and `CMIP/EC-Earth-Consortium` 60%, so split them over more tasks (each
shard is one task; the biggest left is about 106k files):

```bash
SPLIT="CMIP:CMIP/EC-Earth-Consortium"; args=(); IFS=: read -ra d <<< "$SPLIT"
for x in "${d[@]}"; do args+=(--split "$x"); done
uv run scripts/scan_holdings.py --root $ROOT --list-shards "${args[@]}"   # review
N=$(uv run scripts/scan_holdings.py --root $ROOT --list-shards "${args[@]}" | wc -l)
mkdir -p logs
sbatch --array=0-$((N - 1))%20 --export=ALL,ROOT=$ROOT,OUT=$PWD/$R/scan,SPLIT="$SPLIT" \
    slurm/scan_holdings.sbatch
```

Keep scan outputs and logs out of `$ROOT` (run `mkdir`/`sbatch` from the repo,
not from inside the store).

Cross-check the L4 totals against the old server's file lists
(`/proj/cmip6/tmp/cmip6/nc-file-list.txt`, `json-file-list.txt`). They stand in
for the unbuilt Solr export.

## 2. NetCDF check (read-only)

```bash
uv run scripts/check_nc.py --scan $R/scan --out $R/ncinfo --shard <tag> [--prefix <dir>] [--limit N] [--deep]
```

L4: `slurm/check_nc.sbatch` (`DEEP=1` reads the last time step of every file).
Output `nc_status`: ok / empty / not_hdf5_or_nc / truncated / header_error / read_error.

## 3. Gaps (offline)

```bash
uv run scripts/find_gaps.py --scan $R/scan --out $R/gaps    # no --core yet
```

`manifests/core.yaml` is empty until Brett writes it from the inventory
(`datasets.csv`, `partial_coverage`). Until then, time-chunk gaps and
`partial_coverage` still work; `core_gaps` is skipped. After it is filled in,
re-run with `--core manifests/core.yaml` (offline, from the existing scan).

## 4. ESGF (DTN; needs outbound HTTPS)

**Postponed (Phase B): versioning.** The store uses numeric version dirs
(`.../gn/1/`), which the DRS check accepts as `ok` (scan column
`version_scheme` = `numeric`; `vYYYYMMDD` = `date`), so `reorg` never moves
them. Phase A does not depend on this. Do not run steps 4-6 on numeric-version
data until it is settled. Today they match nothing: every file gets
`version_not_on_esgf` and `superseded`, there are no repairs or checksum
candidates, and missing-file detection is empty (fails safe, but useless).
Options when we pick it up:

- Treat numeric local versions as unknown in `esgf.py` compare (match by name,
  prefer size match, then latest). Caveats: a valid older version shows as
  `size_mismatch`; repairs install to `gn/v<date>/` beside `gn/1/`; no
  missing-file detection.
- Map `1` to an ESGF version using the old server's file lists, if they
  record it (not yet checked).

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
    --staging-root /proj/cmip6/tmp/staging --log $R/download.parquet            # dry run
uv run scripts/download_esgf.py ... --execute
```

## 7. Moves (written approval required)

```bash
uv run scripts/plan_moves.py repairs --repairs manifests/repairs/<date>.yaml \
    --staging-root /proj/cmip6/tmp/staging --quarantine-root /proj/cmip6/tmp/quarantine \
    --store-root /proj/cmip6/data --drs-root /proj/cmip6/data/cmip6 --verify \
    --out manifests/moves/<date>-repairs.yaml
uv run scripts/apply_moves.py --manifest manifests/moves/<date>-repairs.yaml --log $R/moves.jsonl
# Brett: set approved_by / approved_on in the YAML, then
uv run scripts/apply_moves.py ... --execute --limit 2     # one dataset first
uv run scripts/rollback_moves.py --log $R/moves.jsonl     # dry run; --execute to undo
```

Create the quarantine dir once before the first repair move
(`mkdir /proj/cmip6/tmp/quarantine`; a data-store write, run as Brett's
account). Quarantined files land at `<quarantine>/<path relative to
/proj/cmip6/data>`.

Moves use `link` + `unlink`, so they never overwrite and need the same
filesystem. Keep the move log; rollback replays it. If a run stops on an
error (e.g. permission denied on unlink), fix the cause and re-run the same
`apply_moves.py` command: it settles the stopped move from the filesystem and
the logged inode, and reports, rather than touches, any file at `dst` that
isn't the one being moved.

## 7b. Reorg of non-DRS files (written approval required)

Moves files that `drs_status` flags as not in their DRS location to
`<drs-root>/<expected path>`. Needs no ESGF or staging. Same approval,
`--limit` and rollback flow as step 7; rehearse rollback first.

```bash
uv run scripts/plan_moves.py reorg --scan $R/scan --ncinfo $R/ncinfo \
    --drs-root /proj/cmip6/data/cmip6 --out manifests/moves/<date>-reorg.yaml
# Brett: set approved_by / approved_on, then
uv run scripts/apply_moves.py --manifest manifests/moves/<date>-reorg.yaml --log $R/moves.jsonl
uv run scripts/apply_moves.py ... --execute --limit 2
```

Rescan after the moves so `--ncinfo` and the catalog match the final paths.

## 8. Catalog

```bash
uv run scripts/build_catalog.py --drs-root /proj/cmip6/data/cmip6 --root /proj/cmip6/data/cmip6/CMIP/NCAR/CESM2 \
    --ncinfo $R/ncinfo --out $R/catalog
uv run scripts/check_catalog.py --catalog $R/catalog/whoi_cmip6.json --report $R/catalog/check.json
uv run scripts/summarize_catalog.py --catalog $R/catalog/whoi_cmip6.csv --out site/data
```

## Adding a reanalysis product

A product is a YAML manifest, `manifests/products/<product>.yaml` (what to
fetch: source, groups of files, variables, years, expected units). Files keep
the provider's layout under `/proj/cmip6/data/reanalysis/<product>/` (or a
`path_template`, for API-only products). Example: `20crv3.yaml`.

Runs on an HPC login node (outbound HTTPS, `/proj/cmip6` mounted). Check
once per node: `curl -sI https://downloads.psl.noaa.gov/ | head -1` should
print an HTTP status line, not a reset. **Downloads over 5 GB run as a Slurm
job**; smaller ones run interactively in `tmux` (`download_product.py` prints a
note when a fetch is over the limit).

```bash
tmux new -s 20crv3            # detach: Ctrl-b d   reattach: tmux attach -t 20crv3
D=$(date +%F); R=runs/${D}_20crv3; F=manifests/fetch/${D}-20crv3.yaml
STORE=/proj/cmip6/data; STAGING=/proj/cmip6/tmp/staging

# 1. Plan (read-only: one listing per dir, a HEAD per file, stat of the store).
#    Ladder L1 first: one small group (prmsl, 0.32 GB).
uv run scripts/plan_product.py --product manifests/products/20crv3.yaml \
    --store-root $STORE --out $R/plan --fetch-out $F --group Monthlies/miscSI-MO
# review $R/plan.md: verdict new / partial / complete / possible_duplicate

# 2. Download into staging. Dry run first, always on the login node:
uv run scripts/download_product.py --fetch $F --staging-root $STAGING --log $R/download-dry.parquet
#    <= 5 GB: run it here
uv run scripts/download_product.py --fetch $F --staging-root $STAGING --log $R/download.parquet --execute
#    > 5 GB: submit instead (resubmit to resume after a timeout)
mkdir -p logs
sbatch --export=ALL,FETCH=$F,STAGING=$STAGING,LOG=$R/download.parquet slurm/download_product.sbatch
squeue --me; tail -f logs/download_product_<jobid>.out

# 3. Plan the install (writes an UNAPPROVED move manifest; touches no data)
uv run scripts/plan_moves.py install --fetch $F --staging-root $STAGING \
    --store-root $STORE --out manifests/moves/${D}-20crv3.yaml
# Brett: review, set approved_by / approved_on, then (dry run, then --execute)
uv run scripts/apply_moves.py --manifest manifests/moves/${D}-20crv3.yaml --log $R/moves.jsonl
uv run scripts/apply_moves.py --manifest manifests/moves/${D}-20crv3.yaml --log $R/moves.jsonl --execute
# undo: uv run scripts/rollback_moves.py --log $R/moves.jsonl --execute

# 4. Confirm: same plan command as step 1 -> "verdict: complete"
# first install only: set enabled: true for 20crv3 in manifests/catalogs.yaml
uv run scripts/build_catalog.py --manifest manifests/catalogs.yaml --product 20crv3 --out catalog/next
```

Before the first Slurm download, check that compute nodes can reach the source
(the job also checks, and fails fast if not):
`srun --pty curl -sI https://downloads.psl.noaa.gov/ | head -1`. If they
can't, ask whether there is a transfer partition (`sinfo -s`).

- Files already in the store are never planned. `present_differs` (size
  differs, or upstream is newer) is report-only; replacing a file is a
  separate, reviewed quarantine-and-install step.
- `possible_duplicate` needs `--scan` (an existing holdings scan, or a scan of
  `reanalysis/` only, never a new full-store scan). It lists same-named files
  elsewhere in the store. Resolve them before downloading.
- PSL publishes no checksums. A download is accepted only if it matches the
  exact size from HEAD, opens as NetCDF, holds the expected variable (and
  units, if listed), and its time axis is inside the year in its name.
  Failures stay in staging as `.bad`. sha256 is logged.
- Downloads get the upstream Last-Modified as their mtime, and the install
  (hard link) keeps it. That is how a later plan detects upstream changes.
- Staging is `/proj/cmip6/tmp/staging`: same filesystem as the data (install
  moves are `link` + `unlink`), outside `/proj/cmip6/data`. The download
  scripts refuse a staging root inside or above `/proj/cmip6/data`.

## Placeholders to confirm

- DRS root: `/proj/cmip6/data/cmip6` (data lives in `/proj/cmip6/data/`;
  checked on the mounted store, 2026-11-10).
- Quarantine: `/proj/cmip6/tmp/quarantine` (same filesystem as the data;
  create once before the first repair move). Staging: `/proj/cmip6/tmp/staging`.
- ESGF `--index-url`.
