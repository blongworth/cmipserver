#!/usr/bin/env bash
# Pull the newest published site from VAST onto the VM. Run as cmipsite
# (systemd timer, every 15 min). Never writes to the VAST mount.
#
#   pull_site.sh                  pull LATEST; staging, then current unless HOLD
#   pull_site.sh --promote        staging -> current (clears nothing; HOLD still blocks auto)
#   pull_site.sh --rollback STAMP point current at an existing release and set HOLD
#                                 (rm $DEST/HOLD to resume automatic promotion)
#
#   SRC   VAST release area as mounted here (default /vast/cmip6/site)
#   DEST  local site root (default /srv/cmip)
#   KEEP  local releases to keep (default 7)
set -euo pipefail

SRC=${SRC:-/vast/cmip6/site}
DEST=${DEST:-/srv/cmip}
KEEP=${KEEP:-7}
here=$(cd "$(dirname "$0")" && pwd)

# Atomically point link $1 at release stamp $2.
point() {
  ln -sfn "$DEST/releases/$2" "$DEST/.$1.tmp"
  mv -T "$DEST/.$1.tmp" "$DEST/$1"
  echo "$1 -> $2"
}

case ${1:-} in
  --promote)
    [[ -L $DEST/staging ]] || { echo "no staging release" >&2; exit 1; }
    point current "$(basename "$(readlink "$DEST/staging")")"
    exit 0 ;;
  --rollback)
    stamp=${2:?usage: --rollback STAMP}
    [[ -d $DEST/releases/$stamp ]] || { echo "no such release: $stamp" >&2; exit 1; }
    point current "$stamp"
    touch "$DEST/HOLD"
    echo "HOLD set; rm $DEST/HOLD to resume"
    exit 0 ;;
  "") ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

[[ -f $SRC/LATEST ]] || { echo "no $SRC/LATEST" >&2; exit 1; }
stamp=$(tr -d '[:space:]' <"$SRC/LATEST")
[[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "bad LATEST: $stamp" >&2; exit 1; }
src=$SRC/releases/$stamp
[[ -f $src/BUILD_OK ]] || { echo "release $stamp has no BUILD_OK" >&2; exit 1; }

if [[ -d $DEST/releases/$stamp ]]; then
  echo "release $stamp already pulled"
  exit 0
fi

tmp=$DEST/releases/.tmp-$stamp
rm -rf "$tmp"
rsync -a "$src"/ "$tmp"/

want=$(sed -n 's/^files=//p' "$tmp/BUILD_OK")
have=$(find "$tmp" -type f ! -name BUILD_OK | wc -l | tr -d ' ')
if [[ $have != "$want" ]]; then
  echo "file count mismatch for $stamp: have $have, BUILD_OK says $want" >&2
  rm -rf "$tmp"
  exit 1
fi
if ! "$here/smoke_test.sh" "$tmp"; then
  rm -rf "$tmp"
  exit 1
fi

mv "$tmp" "$DEST/releases/$stamp"
point staging "$stamp"
if [[ -e $DEST/HOLD ]]; then
  echo "HOLD present: current not changed"
else
  point current "$stamp"
fi

# keep the newest KEEP releases, never the ones in use
inuse="$(readlink "$DEST/current" 2>/dev/null || true) $(readlink "$DEST/staging" 2>/dev/null || true)"
ls -1d "$DEST"/releases/[0-9]*T*Z | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
  [[ $inuse == *"$old"* ]] || { echo "removing $old"; rm -rf "$old"; }
done
