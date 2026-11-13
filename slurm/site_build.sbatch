#!/bin/bash
# Build the site from origin/main against the live catalogs and publish it as a
# release in the VAST site dir; the VM picks it up within 5 minutes
# (docs/deploy-plan.md, docs/runbook-site.md). Takes minutes; independent of
# the slow catalog build (build_catalog.sbatch), which only refreshes the CSVs.
# Dry run unless PUBLISH=1: renders and stops before writing the release.
#
# Run from the repo clone (it is only fetched; the build uses a throwaway
# worktree, so the clone is never dirtied):
#   mkdir -p logs
#   sbatch --export=ALL,SITE_DIR=<VAST site dir> slurm/site_build.sbatch            # dry run
#   sbatch --export=ALL,SITE_DIR=<VAST site dir>,PUBLISH=1 slurm/site_build.sbatch  # deploy now
# Against the sample catalog (rollout step 2):
#   uv run scripts/make_sample_catalog.py --out runs/sample
#   sbatch --export=ALL,SITE_DIR=...,CATALOGS="$PWD/runs/sample/sample_catalog.csv" slurm/site_build.sbatch
# Nightly (scrontab -e, as the service account; same options as below):
#   #SCRON --job-name=site_build --time=01:00:00 --mem=8G --cpus-per-task=2
#   #SCRON --output=<clone>/logs/site_build_%j.out --mail-type=FAIL
#   30 5 * * * cd <clone> && SITE_DIR=<VAST site dir> PUBLISH=1 bash slurm/site_build.sbatch
#
# Needs: SITE_DIR (outside /proj/cmip6). Optional: REF (default origin/main),
# CATALOG_DIR (default /proj/cmip6/catalog/live; one <output>.csv per enabled
# product in manifests/catalogs.yaml), CATALOGS (space-separated CSVs used
# instead of CATALOG_DIR), PUBLISH=1, KEEP (releases kept on VAST, default 10).
# Quarto must be on PATH at the version CI uses (QUARTO_VERSION below).
#
#SBATCH --job-name=site_build
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --output=logs/site_build_%j.out
#SBATCH --mail-type=FAIL

set -euo pipefail

: "${SITE_DIR:?set SITE_DIR to the VAST site directory}"
REF="${REF:-origin/main}"
CATALOG_DIR="${CATALOG_DIR:-/proj/cmip6/catalog/live}"
PUBLISH="${PUBLISH:-0}"
KEEP="${KEEP:-10}"
# keep equal to .github/workflows/check.yml and site/README.md
QUARTO_VERSION=1.10.19

repo="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$repo"
have=$(quarto --version)
[[ "$have" == "$QUARTO_VERSION" ]] || {
    echo "quarto $have on PATH, need $QUARTO_VERSION" >&2
    exit 1
}
# notebooks open catalog files on VAST; HDF5 locking fails there
export HDF5_USE_FILE_LOCKING=FALSE

git fetch --quiet origin
wt=$(mktemp -d "${TMPDIR:-/tmp}/site_build.XXXXXX")
cleanup() { git -C "$repo" worktree remove --force "$wt" || rm -rf "$wt"; }
trap cleanup EXIT
git worktree add --quiet --detach "$wt" "$REF"
cd "$wt"
commit=$(git rev-parse HEAD)
echo "host=$(hostname) ref=$REF commit=$commit site_dir=$SITE_DIR publish=$PUBLISH"

args=(--products manifests/catalogs.yaml --out site/data)
if [[ -n "${CATALOGS:-}" ]]; then
    read -r -a csvs <<<"$CATALOGS"
    for c in "${csvs[@]}"; do args+=(--catalog "$c"); done
else
    args+=(--catalog-dir "$CATALOG_DIR")
    mapfile -t csvs < <(PYTHONPATH=scripts uv run --frozen python -c '
import sys
from summarize_catalog import enabled_catalogs
print("\n".join(enabled_catalogs(sys.argv[1], sys.argv[2])))
' manifests/catalogs.yaml "$CATALOG_DIR")
fi
echo "== catalogs: ${csvs[*]}"
# the date of the newest catalog CSV is the catalog version shown on the site
newest=${csvs[0]}
for c in "${csvs[@]}"; do
    [[ "$c" -nt "$newest" ]] && newest=$c
done
catalog_version=$(date -u -r "$newest" +%F)

echo "== summarize"
uv run --frozen scripts/summarize_catalog.py "${args[@]}"
echo "== production settings (catalog_version=$catalog_version)"
uv run --frozen scripts/write_site_config.py --production site/_production.yml \
    --set "catalog_version=$catalog_version"
echo "== render"
uv run --frozen quarto render site --profile production

echo "== publish"
publish_args=(--src site/_site --dest "$SITE_DIR" --keep "$KEEP"
    --commit "$commit" --catalog-version "$catalog_version")
[[ "$PUBLISH" == 1 ]] && publish_args+=(--publish)
scripts/publish_site.sh "${publish_args[@]}"
echo "done"
