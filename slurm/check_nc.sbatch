#!/bin/bash
# Read-only NetCDF check: one array task per holdings_*.parquet shard.
#
#   mkdir -p logs; SCAN=$PWD/scan OUT=$PWD/ncinfo
#   N=$(uv run scripts/check_nc.py --scan "$SCAN" --list-shards | wc -l)
#   sbatch --array=0-$((N - 1))%20 --export=ALL,SCAN="$SCAN",OUT="$OUT" \
#       slurm/check_nc.sbatch
# One shard by tag (L2/L3), no array: add SHARD=<tag> (see --list-shards) to
#   --export and drop --array.
# Add DEEP=1 to read the last time step of every file (much more I/O).
#
#SBATCH --job-name=check_nc
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=24:00:00
#SBATCH --output=logs/check_nc_%A_%a.out

set -euo pipefail

: "${SCAN:?set SCAN to the holdings parquet directory}"
: "${OUT:?set OUT to the output directory}"
: "${SHARD:=}"
if [[ -z "$SHARD" ]]; then : "${SLURM_ARRAY_TASK_ID:?submit as an array job, or set SHARD}"; fi

cd "${SLURM_SUBMIT_DIR}"
mkdir -p "$OUT"
if [[ -n "$SHARD" ]]; then sel=(--shard "$SHARD"); else sel=(--shard-index "${SLURM_ARRAY_TASK_ID}"); fi
deep=()
[[ "${DEEP:-0}" == 1 ]] && deep=(--deep)

echo "host=$(hostname) task=${SLURM_ARRAY_TASK_ID:-} shard=${SHARD} scan=${SCAN} out=${OUT}"
uv run --frozen scripts/check_nc.py --scan "$SCAN" --out "$OUT" \
    "${sel[@]}" "${deep[@]}"
