#!/bin/bash
# sha256 of a candidate set (read-only). Select candidates first:
#   uv run scripts/verify_checksums.py select --scan scan/ --ncinfo ncinfo/ \
#       --esgf esgf/esgf_compare.parquet --out checksums/candidates.parquet
# Then:
#   mkdir -p logs; N=10
#   sbatch --array=0-$((N - 1))%10 \
#       --export=ALL,CANDIDATES=$PWD/checksums/candidates.parquet,OUT=$PWD/checksums,N=$N \
#       slurm/verify_checksums.sbatch
#
#SBATCH --job-name=verify_checksums
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=24:00:00
#SBATCH --output=logs/verify_checksums_%A_%a.out

set -euo pipefail

: "${CANDIDATES:?set CANDIDATES to candidates.parquet}"
: "${OUT:?set OUT to the output directory}"
: "${N:?set N to the array size}"
: "${SLURM_ARRAY_TASK_ID:?submit as an array job}"

cd "${SLURM_SUBMIT_DIR}"
mkdir -p "$OUT"
echo "host=$(hostname) task=${SLURM_ARRAY_TASK_ID}/${N}"
uv run --frozen scripts/verify_checksums.py hash --candidates "$CANDIDATES" \
    --out "$OUT" --n-shards "$N" --shard-index "${SLURM_ARRAY_TASK_ID}"
