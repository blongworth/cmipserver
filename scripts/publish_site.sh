#!/usr/bin/env bash
# Publish a rendered site (site/_site) as a release in the VAST site directory,
# where the VM's pull_site.sh picks it up (docs/deploy-plan.md). Dry run unless
# --publish: prints what it would do and writes nothing.
#
#   scripts/publish_site.sh --dest <site dir> [--src site/_site] [--keep 10]
#       [--commit <sha>] [--catalog-version <YYYY-MM-DD>] [--publish]
#
# Layout under --dest (never under /proj/cmip6):
#   releases/<stamp>/site/...      the rendered site (served as the docroot)
#   releases/<stamp>/SHA256SUMS    sha256 of every file under site/
#   releases/<stamp>/BUILD_OK      stamp, commit, catalog_version, files,
#                                  sums_sha256; written last
#   LATEST                         the newest complete release's stamp
# A release is copied to releases/.tmp-<stamp> and renamed when complete, and
# LATEST is replaced by rename, so the VM never sees a half-written release.
# site/build.json (stamp, commit, catalog_version) lets monitoring check the
# build date over HTTP. Keeps the newest --keep releases (and LATEST's).

set -euo pipefail

src=site/_site
dest=""
keep=10
commit=""
catalog_version=""
publish=0

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while (($#)); do
    case "$1" in
        --src) src=$2; shift 2 ;;
        --dest) dest=$2; shift 2 ;;
        --keep) keep=$2; shift 2 ;;
        --commit) commit=$2; shift 2 ;;
        --catalog-version) catalog_version=$2; shift 2 ;;
        --publish) publish=1; shift ;;
        --dry-run) publish=0; shift ;;
        -h | --help) usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 2 ;;
    esac
done

die() { echo "publish_site: $*" >&2; exit 1; }

[[ -n "$dest" ]] || die "--dest is required"
[[ "$keep" =~ ^[1-9][0-9]*$ ]] || die "--keep must be a positive integer"
case "$(cd "$dest" 2>/dev/null && pwd -P || echo "$dest")/" in
    /proj/cmip6/*) die "--dest must be outside /proj/cmip6" ;;
esac
[[ -f "$src/index.html" ]] || die "$src/index.html not found (render the site first)"
[[ -f "$src/data/site_config.json" ]] || die "$src/data/site_config.json not found"
if [[ -n "$(find "$src" -type l -print -quit)" ]]; then
    die "$src contains symlinks; releases must be plain files"
fi

if [[ -z "$commit" ]]; then
    commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)
fi
if [[ -z "$catalog_version" ]]; then
    catalog_version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["catalog_version"])' \
        "$src/data/site_config.json")
fi

if command -v sha256sum >/dev/null; then
    sha=(sha256sum)
else
    sha=(shasum -a 256)
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
releases="$dest/releases"
tmp="$releases/.tmp-$stamp"
files=$(find "$src" -type f | wc -l | tr -d ' ')

echo "src=$src dest=$dest stamp=$stamp commit=$commit catalog_version=$catalog_version files=$files"
if ((!publish)); then
    echo "dry run: would copy $src -> $releases/$stamp/site, write SHA256SUMS and BUILD_OK,"
    echo "         set $dest/LATEST to $stamp, and keep the newest $keep releases"
    exit 0
fi

[[ -d "$dest" ]] || die "$dest does not exist"
[[ -e "$releases/$stamp" ]] && die "$releases/$stamp already exists"
mkdir -p "$releases"
trap 'rm -rf "$tmp"' EXIT

rsync -rlt "$src/" "$tmp/site/"
printf '{"stamp": "%s", "commit": "%s", "catalog_version": "%s"}\n' \
    "$stamp" "$commit" "$catalog_version" >"$tmp/site/build.json"
files=$(find "$tmp/site" -type f | wc -l | tr -d ' ')
(cd "$tmp" && find site -type f -print0 | LC_ALL=C sort -z | xargs -0 "${sha[@]}" >SHA256SUMS)
sums=$(cd "$tmp" && "${sha[@]}" SHA256SUMS | cut -d' ' -f1)
cat >"$tmp/BUILD_OK" <<EOF
stamp=$stamp
commit=$commit
catalog_version=$catalog_version
files=$files
sums_sha256=$sums
EOF
mv "$tmp" "$releases/$stamp"
trap - EXIT

echo "$stamp" >"$dest/.LATEST.tmp"
mv -f "$dest/.LATEST.tmp" "$dest/LATEST"
echo "published $releases/$stamp ($files files); LATEST=$stamp"

# prune: newest $keep complete releases stay (this one is the newest), plus
# temp dirs left by killed runs
find "$releases" -mindepth 1 -maxdepth 1 -type d -name '20*Z' -exec basename {} \; |
    LC_ALL=C sort -r | tail -n +"$((keep + 1))" | while read -r s; do
    [[ "$s" == "$stamp" ]] && continue
    rm -rf "${releases:?}/$s"
    echo "pruned $s"
done
find "$releases" -mindepth 1 -maxdepth 1 -type d -name '.tmp-*' -mmin +1440 -exec rm -rf {} +
