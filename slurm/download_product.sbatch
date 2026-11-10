#!/bin/bash
# Download a product fetch manifest into staging (one job; files in sequence).
# Use for downloads over ~5 GB. Smaller ones run interactively on a login node
# (tmux). Never writes to the store; installing is a reviewed move afterwards.
#
# Dry run first on the login node, then submit from the repo root:
#   mkdir -p logs
#   sbatch --export=ALL,FETCH=manifests/fetch/<date>-20crv3.yaml,STAGING=/proj/cmip6/tmp/staging,LOG=runs/<date>_20crv3/download.parquet \
#       slurm/download_product.sbatch
# Re-submitting resumes interrupted files (.part) and skips finished ones, so a
# job that hits its time limit can simply be submitted again.
#
#SBATCH --job-name=download_product
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=12:00:00
#SBATCH --output=logs/download_product_%j.out

set -euo pipefail

: "${FETCH:?set FETCH to the fetch manifest (plan_product.py --fetch-out)}"
: "${STAGING:?set STAGING to the staging root}"
: "${LOG:?set LOG to the download log parquet}"

cd "${SLURM_SUBMIT_DIR}"
mkdir -p "$(dirname "$LOG")"

echo "host=$(hostname) job=${SLURM_JOB_ID} fetch=${FETCH} staging=${STAGING}"
# Fail fast if this node has no route to the source (compute nodes may not).
first_url=$(sed -n 's/^- url: //p' "$FETCH" | head -1)
if [[ -n "$first_url" ]]; then
    curl -sSfI --max-time 30 "$first_url" >/dev/null
fi
uv run --frozen scripts/download_product.py --fetch "$FETCH" \
    --staging-root "$STAGING" --log "$LOG" --execute
