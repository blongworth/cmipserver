#!/bin/bash
# Read variable attributes for the store vocabulary: one array task per live
# catalog, one sampled file per variable key (scripts/scan_var_attrs.py).
# Reads file headers only; writes $OUT/var_attrs_<catalog>.parquet. Keep OUT
# between runs: samples already read are reused, so a rerun opens only new
# variables.
#
#   mkdir -p logs
#   N=$(uv run scripts/enabled_catalogs.py | wc -l)
#   J=$(sbatch --parsable --array=0-$((N - 1)) --export=ALL,OUT=$PWD/runs/vocab \
#       slurm/scan_var_attrs.sbatch)
#   sbatch --dependency=afterok:$J --export=ALL,VAR_ATTRS=$PWD/runs/vocab,OUT=$PWD/runs/$(date +%F)_vocab \
#       slurm/build_vocab.sbatch
#
# Needs: OUT. Optional: LIVE (default /proj/cmip6/catalog/live),
# MANIFEST (default manifests/catalogs.yaml), RESCAN=1 (open every sample again).
#
#SBATCH --job-name=scan_var_attrs
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=04:00:00
#SBATCH --output=logs/scan_var_attrs_%A_%a.out

set -euo pipefail

: "${OUT:?set OUT to the var_attrs directory (kept between runs)}"
LIVE="${LIVE:-/proj/cmip6/catalog/live}"
MANIFEST="${MANIFEST:-manifests/catalogs.yaml}"

cd "${SLURM_SUBMIT_DIR}"
# files are only read; HDF5 locking fails on VAST
export HDF5_USE_FILE_LOCKING=FALSE

mapfile -t cats < <(uv run --frozen scripts/enabled_catalogs.py "$MANIFEST")
i="${SLURM_ARRAY_TASK_ID:-0}"
c="${cats[$i]:?no catalog for array index $i}"
echo "host=$(hostname) catalog=$c out=$OUT"
args=(--catalog "$LIVE/$c.csv" --out "$OUT")
[[ -n "${RESCAN:-}" ]] && args+=(--rescan)
uv run --frozen scripts/scan_var_attrs.py "${args[@]}"
