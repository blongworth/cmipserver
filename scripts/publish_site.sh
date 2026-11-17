#!/usr/bin/env bash
# Publish a built site (site/_site) to the VAST release area for the VM to pull.
# Run on HPC after `quarto render site`. Dry run unless --publish. Only writes
# under SITE_ROOT; never touches anything else on /proj/cmip6.
#
#   scripts/publish_site.sh [--publish] [SRC]
#
#   SRC        built site (default site/_site)
#   SITE_ROOT  release area (default /proj/cmip6/site)
#   KEEP       releases to keep (default 14)
#
# Layout: SITE_ROOT/releases/<UTC stamp>/ (+ BUILD_OK), SITE_ROOT/LATEST.
# Order: copy to a hidden .tmp- dir, write BUILD_OK, rename, then write LATEST,
# so a puller never sees a partial release.
set -euo pipefail

SITE_ROOT=${SITE_ROOT:-/proj/cmip6/site}
KEEP=${KEEP:-14}
publish=0
src=site/_site
for a in "$@"; do
  case $a in
    --publish) publish=1 ;;
    -*) echo "unknown option: $a" >&2; exit 2 ;;
    *) src=$a ;;
  esac
done

[[ -f $src/index.html ]] || { echo "no $src/index.html; render the site first" >&2; exit 1; }

stamp=$(date -u +%Y%m%dT%H%M%SZ)
n=$(find "$src" -type f | wc -l | tr -d ' ')
sum=$(cd "$src" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
catver=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["catalog_version"])' \
  "$src/data/site_config.json" 2>/dev/null || echo unknown)

echo "release $stamp: $n files from $src -> $SITE_ROOT/releases/$stamp (commit $commit, catalog $catver)"
if [[ $publish -eq 0 ]]; then
  echo "dry run: pass --publish to write"
  exit 0
fi

rel=$SITE_ROOT/releases
tmp=$rel/.tmp-$stamp
mkdir -p "$rel"
rsync -a "$src"/ "$tmp"/
printf 'stamp=%s\ncommit=%s\ncatalog_version=%s\nfiles=%s\nsha256=%s\n' \
  "$stamp" "$commit" "$catver" "$n" "$sum" >"$tmp/BUILD_OK"
mv "$tmp" "$rel/$stamp"
echo "$stamp" >"$SITE_ROOT/.LATEST.tmp"
mv "$SITE_ROOT/.LATEST.tmp" "$SITE_ROOT/LATEST"

# keep the newest KEEP releases
ls -1d "$rel"/[0-9]*T*Z 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
  echo "removing old release $old"
  rm -rf "$old"
done
echo "published $stamp"
