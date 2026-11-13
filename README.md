# whoi-cmip-reorg-code

Tools for inventorying and reorganizing the WHOI CMIP6 holdings.

## Holdings scan (read-only)

```bash
# one shard per top-level directory under the CMIP6 root (activities)
uv run scripts/scan_holdings.py --root /proj/cmip6/data/cmip6 --list-shards
uv run scripts/scan_holdings.py --root /proj/cmip6/data/cmip6 --out scan/ --shard CMIP

# all shards on Slurm (see header of slurm/scan_holdings.sbatch)
mkdir -p logs
N=$(uv run scripts/scan_holdings.py --root /proj/cmip6/data/cmip6 --list-shards | wc -l)
sbatch --array=0-$((N - 1))%20 --export=ALL,ROOT=/proj/cmip6/data/cmip6,OUT=$PWD/scan \
    slurm/scan_holdings.sbatch

# inventory outputs (totals, datasets, multi-version, DRS conformance, unparsed)
uv run scripts/summarize_scan.py --scan scan/ --out inventory/ --csv
```

`holdings_<shard>.parquet` has one row per file or symlink (symlinks are never
followed): stats, link info, CMIP6 facets parsed from the filename/path
(`whoi_cmip_reorg_code.drs`), and `drs_status`, which is the first failing check
of `not_nc` → `bad_filename` → `wrong_depth` → `bad_version_dir` →
`path_filename_mismatch` → `ok`.

## Site

Quarto site for `cmip.whoi.edu` in `site/` (see `site/README.md`,
`docs/static-site-plan.md`, `docs/site-handoff.md`). Site data comes from the
production catalogs:

```bash
uv sync --all-groups
L=/proj/cmip6/catalog/live   # ~/mnt/cmip6/catalog/live on a mac with the mount
uv run scripts/summarize_catalog.py --catalog $L/whoi_cmip6.csv --catalog $L/whoi_20crv3.csv --out site/data
uv run quarto render site
```

## Development

```bash
uv run ruff check --fix && uv run ruff format && uv run pytest
```
