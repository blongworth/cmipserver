# Script inventory for the CMIP server rebuild

## Context
You asked for a list of the scripts the project needs and where each one runs. This list comes from `AGENTS.md`, `task-tracker.md`, `open-questions.md`, and the Phase Plan. Nothing is built yet, and there is no repo yet (task 1.10). Python follows your defaults: uv, polars, parquet, ruff.

**Where things run:**
- **Laptop:** your Mac, on the WHOI network or VPN.
- **HPC login:** a WHOI HPC login node. Use it for light work only.
- **Slurm:** an HPC compute job, single or array.
- **DTN:** an HPC data-transfer node, or any node with outbound internet. This depends on IT-7 and IT-11.
- **GHA:** GitHub Actions. It runs checks only and never deploys.
- **Web host:** the WHOI static host. Nothing runs there. It only receives files.

## Proposed repo layout
```
cmip-server/
  pyproject.toml, uv.lock
  src/cmipserver/      shared library
    catalog/           one builder module per product (cmip6, era5, ...) plus shared base
  scripts/             command-line entry points (below)
  slurm/               sbatch wrappers
  manifests/           YAML: core list, downloads, moves, globus acl, catalogs
  site/                Quarto project
  infra/apache/        Apache config snippets for the VM (site, downloads, rate limits)
  tests/               pytest on fixture paths (no VAST needed)
  .github/workflows/, .github/ISSUE_TEMPLATE/
```

## Scripts

### 0. Shared library (imported by everything)
| Module | Purpose | Runs |
|---|---|---|
| `cmipserver/drs.py` | Parse and build CMIP6 DRS paths and filenames, and validate facets against the CMIP6 CV | everywhere |
| `cmipserver/manifest.py` | Load and validate the YAML manifests against a schema | everywhere |
| `cmipserver/movelog.py` | Append-only move log, readable for rollback | HPC |
| `cmipserver/catalog/base.py` | Product builder interface: crawl a root, parse paths to facets, write `.csv.gz` plus `.json`, and validate. Also holds the common core columns shared by every product (C-13) | HPC |
| `cmipserver/catalog/<product>.py` | One module per product (`cmip6`, `era5`, later `merra2`, `jra3q`). Each defines its path and filename templates, attributes, asset format, `aggregation_control`, and xarray open settings. Tested on fixture paths | HPC; GHA on fixtures |

### 1. Inventory and audit (tasks 1.8, 2.1, 2.2; D-1, D-3)
| Script | Purpose | Runs |
|---|---|---|
| `export_solr_inventory.py` | Pull the dataset list from the existing Solr index, read-only, to parquet | Laptop or HPC login |
| `scan_holdings.py` | Walk `/proj/cmip6`, record path, size, mtime, and symlink status to parquet. Settles 142 vs 305 TB | Slurm array, sharded by top-level directory |
| `read_nc_attrs.py` | Read NetCDF global attributes (tracking_id, version, facets, license) to parquet | Slurm array |
| `check_errata.py` | Look up retracted or superseded versions in the ESGF errata service and the latest-version API | DTN |
| `gap_analysis.py` | Compare the inventory against `manifests/core.yaml` and write a gap manifest | Laptop or HPC login |
| `audit_report.qmd` | Audit report: size, counts, versions, licenses, gaps, unparseable paths | Rendered on HPC |

### 2. Reorganization (tasks 2.3 to 2.6, 2.11; M.6)
| Script | Purpose | Runs |
|---|---|---|
| `plan_moves.py` | Old path to DRS path, written to a move manifest. Flags collisions and unmappable files | HPC login |
| `apply_moves.py` | Runs the move manifest. **Dry run by default.** Use `--execute` only after your written approval. Makes the moves and old-path symlinks, logs every action, and can resume | Slurm or HPC login, as your account or the service account |
| `rollback_moves.py` | Reverse moves from the move log | Same as `apply_moves.py` |
| `check_symlinks.py` | Check that every old path resolves (2.6). `--expired` lists symlinks to remove after the PI-3 date (M.6) | HPC |
| `validate_drs.py` | Check that the tree follows the DRS layout (2.11) | Slurm on the real tree; GHA on test fixtures |

### 3. Downloads (tasks 2.7 to 2.10; M.1, M.4)
| Script | Purpose | Runs |
|---|---|---|
| `download_esgf.py` | Manifest-driven ESGF fetch with `esgpull` or `intake-esgf`, written straight into the DRS layout | DTN, as the service account |
| `download_cds.py` | Manifest-driven ERA5 fetch with `cdsapi`, written to `reanalysis/era5/...`. Key comes from the HPC credential store | DTN, as the service account |
| `globus_transfer.py` | Submit and monitor large Globus transfers (ESGF or NCAR) with `globus-sdk` | HPC login or Laptop (the transfer runs between endpoints) |
| `verify_checksums.py` | Check sha256 against ESGF records. Log failures to quarantine; never delete | Slurm array |
| `slurm/download.sbatch` | Wrapper for long or background download runs | DTN or Slurm partition with internet |

### 4. Catalog (tasks 4.1 to 4.4)
| Script | Purpose | Runs |
|---|---|---|
| `manifests/catalogs.yaml` | Lists the products to catalog: name, root path, builder module, output name, enabled flag. Adding a product is a new module plus one entry here | n/a |
| `build_catalog.py` | Master builder. Reads `catalogs.yaml` and runs each enabled product's builder (`--product cmip6 era5` or `--all`). Writes one intake-esm catalog (CSV plus JSON) per product, then the master YAML that lists them (C-14). Builds to a temp name and replaces the live catalog only after that product passes its checks. A failed product keeps its last good catalog and is reported, without blocking the others | Slurm (one task per product, or an array) |
| `check_catalog.py` | Per product: compare catalog rows to the filesystem, check naming and that every `uri` is readable, and test that `intake.open_esm_datastore()` loads and opens a sample. Also checks that the master YAML opens and lists every enabled product | Slurm |
| `summarize_catalog.py` | Merges the common core columns of every product's catalog into one small summary for the OJS browser (parquet or JSON), with a `product` column. Strips VAST paths if PI-5 says no | Slurm |

### 5. Nightly job and deploy (tasks 4.3, 5.7)
| Script | Purpose | Runs |
|---|---|---|
| `slurm/nightly.sbatch` | Build each product's catalog, check them, write the master catalog, summarize, `quarto render`, deploy. Fails before deploy if the checks fail. A product that fails to build keeps its last good catalog, and the job reports it | Slurm, scheduled by `scrontab` or cron (IT-11) |
| `deploy_site.sh` | `rsync` `_site/` to the WHOI VM. Use a staging path first (5.8), then production | HPC, with an SSH key or shared mount (IT-10) |

### 6. Website (tasks 5.1 to 5.6)
| File | Purpose | Runs |
|---|---|---|
| `site/_quarto.yml` and pages | Static site, acknowledgment text, download guide, cloud page | Rendered on HPC |
| `site/browse.qmd` | OJS dataset browser over the catalog summary | Runs in the user's browser |
| `site/quickstart.qmd` | Open data with xarray through the catalog, with `freeze: auto` | Executed on HPC; frozen output committed |
| `.github/ISSUE_TEMPLATE/data-request.yml` | Data request form | GitHub |
| `.github/ISSUE_TEMPLATE/report-problem.yml` | Problem report form, linked per dataset from the browser (5.10) | GitHub |
| `infra/apache/*.conf` | Apache config for the VM: site, read-only HTTPS download alias to the VAST mount, MIME types, rate limits, no directory listing (5.13). Applied by WHOI IT or the VM owner | VM |

### 7. CI checks (task M.3)
| File | Purpose | Runs |
|---|---|---|
| `.github/workflows/check.yml` | ruff, pytest (`drs.py` and `manifest.py` on fixtures), manifest schema validation, Quarto render from frozen output, link check (e.g. lychee) | GHA |

### 8. Globus share (task 6.1)
| Script | Purpose | Runs |
|---|---|---|
| `globus_acl.py` (optional) | Sync guest-collection permissions from `manifests/globus_acl.yaml` (PI-6). The collection itself is created once in the Globus web UI | Laptop or HPC login |

### 9. Environment
| File | Purpose | Runs |
|---|---|---|
| `pyproject.toml` / `uv.lock` | Pinned dependencies: polars, xarray, netCDF4, ecgtools, intake-esm, esgpull, cdsapi, globus-sdk, pyyaml, pydantic | everywhere |
| `slurm/setup_env.sh` | Create or refresh the uv environment on the HPC project space (not in a purgeable directory) | HPC login |

## Build order (matches the schedule)
1. **Weeks 1 to 2:** env, `drs.py`, `manifest.py`, `export_solr_inventory.py`, `scan_holdings.py`
2. **Week 3:** `read_nc_attrs.py`, `check_errata.py`, `gap_analysis.py`, `audit_report.qmd`, `plan_moves.py`
3. **Week 4:** download scripts, `verify_checksums.py`
4. **Week 5:** `apply_moves.py` and rollback, `check_symlinks.py`, `validate_drs.py`, catalog scripts, `nightly.sbatch`
5. **Week 6:** site, `deploy_site.sh`, CI, `globus_acl.py`

## Gaps to flag
- **Task 4.3 vs. `AGENTS.md`:** Task 4.3 says the nightly job should "push to GitHub." `AGENTS.md` says the HPC job deploys by `rsync`. Suggestion: keep `rsync` as the deploy path. Push only the catalog summary and frozen notebook output, so CI can test-render the site.
- **Internet on compute nodes:** Whether compute nodes can reach the internet decides where the download scripts run. Add this to IT-7 or IT-11.
- **Solr inventory export:** This sends read-only queries to the old server, which may have the PHP vulnerability. Solr is separate from PHP, but confirm with IT first.

## Verification
- Unit tests for `drs.py` and `manifest.py` on fixture paths, run locally and in GHA.
- Every data-touching script defaults to dry run. Run it on a test subtree, such as one model, before the full tree.
- Run `nightly.sbatch` against the staging path (5.8) before DNS cutover.
