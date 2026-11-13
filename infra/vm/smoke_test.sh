#!/usr/bin/env bash
# Check a release before it goes live. Run by pull_site.sh; also by hand:
#
#   infra/vm/smoke_test.sh /srv/cmip/releases/<stamp>
#
# The release dir holds site/ (as written by scripts/publish_site.sh). Checks:
# key pages exist; the JSON the dataset browser loads parses; the summary has
# rows; the site config has a known audience; vendored files match the sha256
# in vendor/vendor.yml. Exits non-zero and names every failed check.
# Needs bash and python3 only.

set -euo pipefail

rel=${1:?usage: smoke_test.sh <release dir>}
site="$rel/site"
fail=0
bad() { echo "smoke: FAIL $*" >&2; fail=1; }

[[ -d "$site" ]] || { bad "$site is not a directory"; exit 1; }

for f in index.html browse.html dataset.html catalog.html holdings.html about.html \
    access.html search.json build.json robots.txt \
    data/site_config.json data/catalog_summary.json data/catalog_stats.json \
    data/products.json vendor/vendor.yml; do
    [[ -s "$site/$f" ]] || bad "missing or empty: $f"
done
((fail)) && exit 1

python3 - "$site" <<'EOF' || fail=1
import json
import sys
from pathlib import Path

site = Path(sys.argv[1])
errors = []


def load(rel):
    try:
        return json.loads((site / rel).read_text())
    except (OSError, ValueError) as e:
        errors.append(f"{rel}: {e}")
        return None


cfg = load("data/site_config.json")
if cfg is not None:
    if cfg.get("audience") not in ("internal", "external"):
        errors.append(f"site_config.json: audience {cfg.get('audience')!r}")
    if not cfg.get("catalog_version"):
        errors.append("site_config.json: no catalog_version")
summary = load("data/catalog_summary.json")
if summary is not None and not (isinstance(summary, list) and summary):
    errors.append("catalog_summary.json: no rows")
stats = load("data/catalog_stats.json")
if stats is not None and not stats.get("n_datasets"):
    errors.append("catalog_stats.json: n_datasets is 0")
if summary and stats and len(summary) != stats.get("n_datasets"):
    errors.append(
        f"catalog_summary.json has {len(summary)} rows, stats say {stats.get('n_datasets')}"
    )
load("data/products.json")
load("search.json")
load("build.json")
for e in errors:
    print(f"smoke: FAIL {e}", file=sys.stderr)
sys.exit(1 if errors else 0)
EOF

# vendor.yml lists "path: <file>" followed by 'sha256: "<hex>"'
if command -v sha256sum >/dev/null; then
    sha() { sha256sum "$1" | cut -d' ' -f1; }
else
    sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
fi
n_vendor=0
while read -r path want; do
    n_vendor=$((n_vendor + 1))
    if [[ ! -f "$site/vendor/$path" ]]; then
        bad "vendor/$path missing"
    elif [[ "$(sha "$site/vendor/$path")" != "$want" ]]; then
        bad "vendor/$path sha256 differs from vendor.yml"
    fi
done < <(awk '
    $1 == "path:" { p = $2; gsub(/"/, "", p) }
    $1 == "sha256:" && p != "" { h = $2; gsub(/"/, "", h); print p, h; p = "" }
' "$site/vendor/vendor.yml")
((n_vendor > 0)) || bad "no files listed in vendor/vendor.yml"

if ((fail)); then
    exit 1
fi
echo "smoke: ok $rel ($n_vendor vendored files)"
