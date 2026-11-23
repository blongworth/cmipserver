#!/bin/bash
# Build the store vocabulary (whoi_cmip_vocabulary.csv) from the live catalogs
# and the scan_var_attrs.sbatch output; fails if a live catalog's variable
# has no row. Opens no data files. Writes only under $OUT; review
# $OUT/whoi_cmip_vocabulary_report.json, then promote by hand to
# /proj/cmip6/catalog/live (temp name, then rename; docs/audit-runbook.md).
#
#   sbatch --export=ALL,VAR_ATTRS=$PWD/runs/vocab,OUT=$PWD/runs/$(date +%F)_vocab \
#       slurm/build_vocab.sbatch
#
# Needs: OUT, VAR_ATTRS. Optional: LIVE, MANIFEST.
#
#SBATCH --job-name=build_vocab
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --output=logs/build_vocab_%j.out

set -euo pipefail

: "${OUT:?set OUT to the output directory}"
: "${VAR_ATTRS:?set VAR_ATTRS to the scan_var_attrs output directory}"
LIVE="${LIVE:-/proj/cmip6/catalog/live}"
MANIFEST="${MANIFEST:-manifests/catalogs.yaml}"

cd "${SLURM_SUBMIT_DIR}"
mapfile -t cats < <(uv run --frozen scripts/enabled_catalogs.py "$MANIFEST")
args=()
for c in "${cats[@]}"; do args+=(--catalog "$LIVE/$c.csv"); done
# exits 1 if a variable key of any catalog has no vocabulary row
uv run --frozen scripts/build_vocab.py "${args[@]}" --var-attrs "$VAR_ATTRS" --out "$OUT"
echo done
