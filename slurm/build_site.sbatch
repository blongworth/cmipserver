#!/bin/bash
# Build the site from the live catalogs and publish it to /proj/cmip6/site for
# the VM to pull (with /srv/cmip/HOLD on the VM it stays on staging until
# promoted). Submit by hand from the repo root; not scheduled.
#
#   mkdir -p logs && sbatch slurm/build_site.sbatch
#
# Catalogs: every enabled product in manifests/catalogs.yaml (read from
# /proj/cmip6/catalog/live/whoi_<name>.csv; a missing one fails the job).
# Override with names, with or without the whoi_ prefix:
#   sbatch --export=ALL,CATALOGS="cmip6 20crv3" slurm/build_site.sbatch
#
#SBATCH --job-name=cmip-site
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#SBATCH --output=logs/site_%j.log
#SBATCH --mail-type=FAIL
set -euo pipefail

export PATH=$HOME/opt/quarto/bin:$PATH
# HDF5 file locking fails on VAST/NFS ("NetCDF: HDF error"); docs/audit-findings.md.
export HDF5_USE_FILE_LOCKING=FALSE
cd "${SLURM_SUBMIT_DIR:-.}"

quarto --version
uv sync --all-groups

L=/proj/cmip6/catalog/live
cats=${CATALOGS:-$(uv run scripts/enabled_catalogs.py manifests/catalogs.yaml)}
args=()
for c in $cats; do
  c=${c#whoi_}
  [[ -f $L/whoi_$c.csv ]] || { echo "missing $L/whoi_$c.csv" >&2; exit 1; }
  args+=(--catalog "$L/whoi_$c.csv")
done
[[ ${#args[@]} -gt 0 ]] || { echo "no catalogs" >&2; exit 1; }
# store vocabulary (slurm/build_vocab.sbatch, promoted by hand): the site's
# variable search and Variables page; without it they are empty
if [[ -f $L/whoi_cmip_vocabulary.csv ]]; then
  args+=(--vocab "$L/whoi_cmip_vocabulary.csv")
else
  echo "no $L/whoi_cmip_vocabulary.csv: building without variable definitions" >&2
fi
echo "catalogs: $cats"
uv run scripts/summarize_catalog.py "${args[@]}" --products manifests/catalogs.yaml --out site/data
uv run quarto render site
scripts/publish_site.sh --publish
