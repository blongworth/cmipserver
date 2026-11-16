#!/bin/bash
# Total file sizes under /proj/cmip6/data, one Slurm array task per shard dir.
# Read-only: runs find, never writes under ROOT. Compare the result with the
# catalogs using scripts/du_compare.py.
#
# Make the shard list (cheap: one directory listing, not a walk). Shards are
# paths relative to ROOT; any depth is fine as long as no two overlap, e.g.
# replace cmip6/CMIP with its cmip6/CMIP/* subdirs if it dominates:
#   bash scripts/du_shard.sh --list > shards.txt
#
# Submit (logs/ must exist; %16 caps concurrent walkers, tune 8-32;
# add --partition/--account as the cluster requires):
#   mkdir -p logs
#   sbatch --array=1-$(wc -l < shards.txt)%16 scripts/du_shard.sh
#
# Try one shard by hand first:
#   SLURM_ARRAY_TASK_ID=1 OUT=runs/du/test bash scripts/du_shard.sh
#
# Output: $OUT/<task>.tsv, one line, no header:
#   shard files apparent_bytes allocated_bytes nonnc_files nonnc_bytes errors
# errors is the number of find error lines (also in $OUT/<task>.err); a task
# with errors > 0 exits non-zero so sacct shows it as FAILED.
#
#SBATCH --job-name=cmip-du
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=04:00:00
#SBATCH --output=logs/du_%A_%a.log
set -euo pipefail

ROOT=${ROOT:-/proj/cmip6/data}

if [[ "${1:-}" == "--list" ]]; then
    # Second-level entries; depth-1 files would be missed, so report them.
    find "$ROOT" -mindepth 2 -maxdepth 2 -printf '%P\n' | sort
    n=$(find "$ROOT" -mindepth 1 -maxdepth 1 -not -type d | wc -l)
    if (( n > 0 )); then
        echo "warning: $n non-directory entries directly in $ROOT are not in any shard" >&2
    fi
    exit 0
fi

SHARDS=${SHARDS:-shards.txt}
OUT=${OUT:-runs/du/${SLURM_ARRAY_JOB_ID:-manual}}
task=${SLURM_ARRAY_TASK_ID:?set SLURM_ARRAY_TASK_ID}
mkdir -p "$OUT"

shard=$(sed -n "${task}p" "$SHARDS")
if [[ -z "$shard" ]]; then
    echo "no shard on line $task of $SHARDS" >&2
    exit 1
fi
echo "task $task: $ROOT/$shard -> $OUT/$task.tsv ($(date +%FT%T))"

# One metadata pass: apparent size (%s), 512-byte blocks (%b), name (%f).
# Symlinks are not followed or counted. Sums use %.0f: awk numbers are
# doubles (exact to 2^53 bytes, ~9 PB) and some awks truncate %d to 32 bits.
status=0
find "$ROOT/$shard" -type f -printf '%s %b %f\n' 2> "$OUT/$task.err" |
awk -v s="$shard" '
  { n++; b += $1; a += $2 * 512
    if ($0 !~ /\.nc$/) { on++; ob += $1 } }
  END { printf "%s\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f", s, n, b, a, on, ob }' \
  > "$OUT/$task.tmp" || status=$?

errors=$(wc -l < "$OUT/$task.err" | tr -d " ")
printf '\t%d\n' "$errors" >> "$OUT/$task.tmp"
mv "$OUT/$task.tmp" "$OUT/$task.tsv"
[[ -s "$OUT/$task.err" ]] || rm -f "$OUT/$task.err"

cat "$OUT/$task.tsv"
echo "done ($(date +%FT%T)), find status $status, $errors error lines"
if (( status != 0 || errors > 0 )); then
    exit 1
fi
