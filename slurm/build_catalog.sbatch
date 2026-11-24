#!/bin/bash
# Build, check and summarize the intake-esm catalog(s). Reads the store, writes
# only under $OUT. Stops if the build fails (set -e). Every catalog is checked
# even if one fails (multi-variable products always fail data_vars for now;
# docs/todo.md); the job then exits 1, and whoi_cmip6 is summarized only if its
# own check passed.
#
#   mkdir -p logs
#   sbatch --export=ALL,OUT=$PWD/runs/$(date +%F)_catalog,NCINFO=$PWD/runs/<date>_L4/ncinfo \
#       slurm/build_catalog.sbatch
# Smoke test on one subtree first (cmip6 only):
#   ... --export=ALL,OUT=...,NCINFO=...,PRODUCT=cmip6,SUBTREE=/proj/cmip6/data/cmip6/CMIP/NCAR/CESM2
# Chain the full build after a successful smoke test:
#   J=$(sbatch --parsable ...smoke...); sbatch --dependency=afterok:$J ...full...
#
# Needs: OUT, NCINFO (one ncinfo dir, or several separated by ":", e.g.
# $R/ncinfo_ocean_reanalysis:$R/ncinfo_cmems:$R/ncinfo_prism).
# Optional: MANIFEST (default manifests/catalogs.yaml),
# PRODUCT (one product name), INCLUDE_DISABLED=1 (with PRODUCT: build it even
# if not enabled yet), SUBTREE (crawl one subtree; cmip6 only; not ROOT, which
# the runbook exports as the store root).
# Outputs: $OUT/<output>.{csv,json}, <output>_invalid.parquet, checks/<output>.json,
# $OUT/summary/ (site summary for whoi_cmip6; copy to site/data after review).
#
#SBATCH --job-name=build_catalog
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=12:00:00
#SBATCH --output=logs/build_catalog_%j.out

set -euo pipefail

: "${OUT:?set OUT to the output directory}"
: "${NCINFO:?set NCINFO to the ncinfo parquet directory}"
MANIFEST="${MANIFEST:-manifests/catalogs.yaml}"

cd "${SLURM_SUBMIT_DIR}"
mkdir -p "$OUT/checks"
# files are only read; HDF5 locking fails on VAST (check_catalog opens samples)
export HDF5_USE_FILE_LOCKING=FALSE

args=(--manifest "$MANIFEST" --out "$OUT" --jobs "${SLURM_CPUS_PER_TASK:-1}")
IFS=: read -ra ncinfo_dirs <<< "$NCINFO"
for d in "${ncinfo_dirs[@]}"; do args+=(--ncinfo "$d"); done
[[ -n "${PRODUCT:-}" ]] && args+=(--product "$PRODUCT")
[[ -n "${INCLUDE_DISABLED:-}" ]] && args+=(--include-disabled)
[[ -n "${SUBTREE:-}" ]] && args+=(--root "$SUBTREE")

echo "host=$(hostname) out=${OUT} ncinfo=${NCINFO} product=${PRODUCT:-all} subtree=${SUBTREE:-all}"
uv run --frozen scripts/build_catalog.py "${args[@]}"

failed=()
for js in "$OUT"/*.json; do
    name=$(basename "$js" .json)
    echo "== check $name"
    uv run --frozen scripts/check_catalog.py --catalog "$js" --products "$MANIFEST" \
        --report "$OUT/checks/${name}.json" || failed+=("$name")
done

if [[ -f "$OUT/whoi_cmip6.csv" && " ${failed[*]} " != *" whoi_cmip6 "* ]]; then
    echo "== summarize whoi_cmip6"
    uv run --frozen scripts/summarize_catalog.py --catalog "$OUT/whoi_cmip6.csv" --out "$OUT/summary"
fi
if (( ${#failed[@]} )); then
    echo "checks failed: ${failed[*]} (see $OUT/checks/)" >&2
    exit 1
fi
echo done
