#!/bin/bash
# Build the site from the live catalogs and publish it to /proj/cmip6/site for
# the VM to pull (with /srv/cmip/HOLD on the VM it stays on staging until
# promoted). Submit by hand from the repo root; not scheduled.
#
#   mkdir -p logs && sbatch slurm/build_site.sbatch
#
# Catalogs (names under /proj/cmip6/catalog/live/whoi_<name>.csv):
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
cd "${SLURM_SUBMIT_DIR:-.}"

L=/proj/cmip6/catalog/live
args=()
for c in ${CATALOGS:-cmip6 20crv3}; do
  [[ -f $L/whoi_$c.csv ]] || { echo "missing $L/whoi_$c.csv" >&2; exit 1; }
  args+=(--catalog "$L/whoi_$c.csv")
done

quarto --version
uv sync --all-groups
uv run scripts/summarize_catalog.py "${args[@]}" --products manifests/catalogs.yaml --out site/data
uv run quarto render site
scripts/publish_site.sh --publish
