#!/usr/bin/env bash
# Install the newest site release from VAST and make it live (docs/deploy-plan.md,
# docs/runbook-site.md). Run every 5 minutes by cmip-pull.timer as cmipsite;
# needs no root.
#
#   pull_site.sh                     pull LATEST if new, check it, switch current
#   pull_site.sh --status            show current, LATEST, HOLD, FAILED, releases
#   pull_site.sh --rollback <stamp>  switch to an older release and set HOLD
#   pull_site.sh --hold              stop switching (the site stays as it is)
#   pull_site.sh --resume            clear HOLD and FAILED, then pull
#
# A release is used only if releases/<stamp>/BUILD_OK exists on VAST. It is
# copied to <dest>/releases/.tmp-<stamp>, checked (file count, SHA256SUMS,
# smoke_test.sh) and renamed; then the current symlink is replaced by rename,
# so Apache never serves a half-copied release. A release that fails its
# checks is recorded in FAILED and not retried until a newer build or --resume.
# Keeps the newest KEEP releases plus the live one.
#
# Environment (defaults are the VM's paths; override for local tests):
#   CMIP_SITE_SRC   /vast/cmip6/site  /proj/cmip6/site (read-only mount)
#   CMIP_SITE_DEST  /srv/cmip         holds current -> releases/<stamp>
#   CMIP_SITE_KEEP  5
#   CMIP_SMOKE      smoke_test.sh next to this script

set -euo pipefail

src=${CMIP_SITE_SRC:-/vast/cmip6/site}
dest=${CMIP_SITE_DEST:-/srv/cmip}
keep=${CMIP_SITE_KEEP:-5}
smoke=${CMIP_SMOKE:-$(cd "$(dirname "$0")" && pwd)/smoke_test.sh}

log() { echo "pull_site: $*"; }
die() { echo "pull_site: ERROR $*" >&2; exit 1; }
usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

if command -v sha256sum >/dev/null; then
    sha() { sha256sum "$@"; }
else
    sha() { shasum -a 256 "$@"; }
fi

valid_stamp() { [[ "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; }

live_stamp() {
    local t
    t=$(readlink "$dest/current" 2>/dev/null) || return 0
    basename "$t"
}

field() { sed -n "s/^$1=//p" "$2" | head -n 1; }

# Copy and check one release; leaves it in $dest/releases/<stamp>. Every step
# checks its own status: pull() calls this in an `if`, where set -e is off.
install_release() {
    local stamp=$1 from="$src/releases/$1" tmp="$dest/releases/.tmp-$1" n sums
    if [[ -d "$dest/releases/$stamp" ]]; then
        return 0
    fi
    [[ -f "$from/BUILD_OK" ]] || die "$from/BUILD_OK not found"
    rm -rf "$tmp"
    # Apache (www-data) reads what cmipsite copies, whatever the modes on VAST
    if ! { rsync -rlt "$from/" "$tmp/" && chmod -R u=rwX,go=rX "$tmp"; }; then
        rm -rf "$tmp"; die "$stamp: copy failed"
    fi
    [[ "$(field stamp "$tmp/BUILD_OK")" == "$stamp" ]] || {
        rm -rf "$tmp"; die "$stamp: BUILD_OK stamp does not match"
    }
    if [[ -n "$(find "$tmp" -type l -print -quit)" ]]; then
        rm -rf "$tmp"; die "$stamp: release contains symlinks"
    fi
    n=$(find "$tmp/site" -type f | wc -l | tr -d ' ')
    [[ "$n" == "$(field files "$tmp/BUILD_OK")" ]] || {
        rm -rf "$tmp"; die "$stamp: $n files, BUILD_OK says $(field files "$tmp/BUILD_OK")"
    }
    sums=$(cd "$tmp" && sha SHA256SUMS | cut -d' ' -f1)
    [[ "$sums" == "$(field sums_sha256 "$tmp/BUILD_OK")" ]] || {
        rm -rf "$tmp"; die "$stamp: SHA256SUMS does not match BUILD_OK"
    }
    # print only failures (macOS sha256sum has no --quiet)
    if ! (cd "$tmp" && sha -c SHA256SUMS | { grep -v ': OK$' >&2 || true; }); then
        rm -rf "$tmp"; die "$stamp: checksum mismatch"
    fi
    if ! "$smoke" "$tmp"; then
        rm -rf "$tmp"; die "$stamp: smoke test failed"
    fi
    mv "$tmp" "$dest/releases/$stamp" || die "$stamp: rename failed"
    log "installed $stamp ($n files, commit $(field commit "$dest/releases/$stamp/BUILD_OK"))"
}

switch_to() {
    local stamp=$1
    ln -sfn "releases/$stamp" "$dest/.current.tmp"
    # GNU mv -T renames over the old link atomically; python3 elsewhere (tests)
    mv -T "$dest/.current.tmp" "$dest/current" 2>/dev/null ||
        python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' \
            "$dest/.current.tmp" "$dest/current"
    log "current -> $stamp"
}

prune() {
    local live s
    live=$(live_stamp)
    find "$dest/releases" -mindepth 1 -maxdepth 1 -type d -name '20*Z' -exec basename {} \; |
        LC_ALL=C sort -r | tail -n +"$((keep + 1))" | while read -r s; do
        [[ "$s" == "$live" ]] && continue
        rm -rf "${dest:?}/releases/$s"
        log "pruned $s"
    done
}

pull() {
    local latest live
    if [[ -f "$dest/HOLD" ]]; then
        log "HOLD set ($(head -n 1 "$dest/HOLD")); not switching"
        return 0
    fi
    [[ -r "$src/LATEST" ]] || die "$src/LATEST not readable (is /proj/cmip6 mounted at /vast/cmip6?)"
    latest=$(head -n 1 "$src/LATEST")
    valid_stamp "$latest" || die "LATEST holds an invalid stamp: '$latest'"
    live=$(live_stamp)
    [[ "$latest" == "$live" ]] && return 0
    if [[ -f "$dest/FAILED" && "$(cat "$dest/FAILED")" == "$latest" ]]; then
        log "LATEST $latest failed its checks earlier; waiting for a newer build"
        return 0
    fi
    if [[ ! -f "$src/releases/$latest/BUILD_OK" ]]; then
        log "LATEST $latest has no BUILD_OK; ignored"
        return 0
    fi
    if ! (install_release "$latest"); then
        echo "$latest" >"$dest/FAILED"
        die "$latest not installed; site stays on ${live:-nothing}"
    fi
    switch_to "$latest"
    rm -f "$dest/FAILED"
    prune
}

status() {
    local latest="(none)" s mark
    [[ -r "$src/LATEST" ]] && latest=$(head -n 1 "$src/LATEST")
    echo "current: $(live_stamp)"
    echo "LATEST:  $latest"
    [[ -f "$dest/HOLD" ]] && echo "HOLD:    $(head -n 1 "$dest/HOLD")"
    [[ -f "$dest/FAILED" ]] && echo "FAILED:  $(cat "$dest/FAILED")"
    echo "releases on this host:"
    find "$dest/releases" -mindepth 1 -maxdepth 1 -type d -name '20*Z' -exec basename {} \; |
        LC_ALL=C sort -r | while read -r s; do
        mark=" "
        [[ "$s" == "$(live_stamp)" ]] && mark="*"
        echo "  $mark $s  commit=$(field commit "$dest/releases/$s/BUILD_OK") catalog_version=$(field catalog_version "$dest/releases/$s/BUILD_OK")"
    done
}

case "${1:-}" in -h | --help) usage 0 ;; esac
[[ -d "$dest/releases" ]] || die "$dest/releases does not exist (run infra/vm/setup.sh)"

if [[ "${1:-}" == --status ]]; then
    status
    exit 0
fi

# one run at a time (the timer and a person can overlap)
if command -v flock >/dev/null; then
    exec 9>"$dest/.lock"
    flock -w 60 9 || die "another pull_site.sh is running"
fi

case "${1:-}" in
    "") pull ;;
    --hold)
        echo "hold since $(date -u +%FT%TZ) by ${SUDO_USER:-$(id -un)}" >"$dest/HOLD"
        log "HOLD set; current stays $(live_stamp)"
        ;;
    --resume)
        rm -f "$dest/HOLD" "$dest/FAILED"
        log "HOLD and FAILED cleared"
        pull
        ;;
    --rollback)
        stamp=${2:?--rollback needs a stamp (see --status)}
        valid_stamp "$stamp" || die "invalid stamp: $stamp"
        echo "rollback to $stamp at $(date -u +%FT%TZ) by ${SUDO_USER:-$(id -un)}" >"$dest/HOLD"
        install_release "$stamp"
        switch_to "$stamp"
        log "HOLD set; run --resume to go back to automatic updates"
        ;;
    -h | --help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 2 ;;
esac
