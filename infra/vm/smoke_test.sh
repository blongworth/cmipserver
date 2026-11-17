#!/usr/bin/env bash
# Smoke test a release directory before it goes live.
#   infra/vm/smoke_test.sh DIR
set -euo pipefail

dir=${1:?usage: smoke_test.sh DIR}
fail=0
bad() { echo "FAIL: $*" >&2; fail=1; }

for f in index.html browse.html dataset.html access.html data/site_config.json data/catalog_summary.json; do
  [[ -s $dir/$f ]] || bad "missing or empty $f"
done

python3 - "$dir" <<'EOF' || fail=1
import json, sys
d = sys.argv[1]
cfg = json.load(open(f"{d}/data/site_config.json"))
for k in ("site_url", "https_prefix", "catalog_version"):
    assert k in cfg, f"site_config.json lacks {k}"
rows = json.load(open(f"{d}/data/catalog_summary.json"))
assert len(rows) > 0, "catalog_summary.json has no rows"
EOF

[[ $fail -eq 0 ]] && echo "smoke test ok: $dir"
exit $fail
