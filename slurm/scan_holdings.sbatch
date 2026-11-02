#!/bin/bash
# Read-only holdings scan: one array task per top-level directory of $ROOT.
#
# Submit with the array size taken from the shard list, e.g.:
#   mkdir -p logs; ROOT=/proj/cmip6 OUT=$PWD/scan
#   N=$(uv run scripts/scan_holdings.py --root "$ROOT" --list-shards | wc -l)
#   sbatch --array=0-$((N - 1))%20 --export=ALL,ROOT="$ROOT",OUT="$OUT" \
#       slurm/scan_holdings.sbatch
# Then:
#   uv run scripts/summarize_scan.py --scan "$OUT" --out inventory/ --csv
#
#SBATCH --job-name=scan_holdings
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=12:00:00
#SBATCH --output=logs/scan_holdings_%A_%a.out

set -euo pipefail

: "${ROOT:?set ROOT to the tree to scan}"
: "${OUT:?set OUT to the output directory}"
: "${SLURM_ARRAY_TASK_ID:?submit as an array job}"

cd "${SLURM_SUBMIT_DIR}"
mkdir -p "$OUT"

echo "host=$(hostname) task=${SLURM_ARRAY_TASK_ID} root=${ROOT} out=${OUT}"
uv run --frozen scripts/scan_holdings.py --root "$ROOT" --out "$OUT" \
    --shard-index "${SLURM_ARRAY_TASK_ID}"
