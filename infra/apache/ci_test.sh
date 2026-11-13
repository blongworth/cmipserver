#!/usr/bin/env bash
# Install the vhosts on a DISPOSABLE Debian/Ubuntu host (the CI runner, or a
# throwaway container) and check them with real requests. Never run this on
# the VM: it overwrites /etc/apache2 and /srv/cmip and /vast/cmip6 with fakes.
#
#   sudo CI=true infra/apache/ci_test.sh <rendered site dir, e.g. site/_site>
#
# Checks both vhosts: config syntax; pages served; /data/<path>.nc served from
# the fake store with byte ranges (206); /data/ and non-.nc store files not
# served; the site's own data/*.json still served; headers; HSTS only on HTTPS.

set -euo pipefail

[[ "${CI:-}" == true ]] || { echo "refusing: set CI=true on a disposable host" >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
site=$(cd "${1:?usage: ci_test.sh <rendered site dir>}" && pwd)
here=$(cd "$(dirname "$0")" && pwd)

command -v apache2ctl >/dev/null ||
    { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q apache2 curl openssl; } >/dev/null

# fake release, store, and certificate at the production paths
rm -rf /srv/cmip && mkdir -p /srv/cmip/releases/20260101T000000Z
cp -r "$site" /srv/cmip/releases/20260101T000000Z/site
ln -sfn releases/20260101T000000Z /srv/cmip/current
mkdir -p /vast/cmip6/data/cmip6/x
head -c 1000 /dev/urandom >/vast/cmip6/data/cmip6/x/tas.nc
echo secret >/vast/cmip6/data/cmip6/x/notes.txt
crt=/etc/letsencrypt/live/cmip.whoi.edu
mkdir -p "$crt"
[[ -f "$crt/privkey.pem" ]] || openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
    -subj /CN=cmip.whoi.edu -keyout "$crt/privkey.pem" -out "$crt/fullchain.pem" 2>/dev/null

mkdir -p /etc/apache2/cmip
cp "$here/common.conf" /etc/apache2/cmip/common.conf
cp "$here/cmip.conf" "$here/cmip-http-interim.conf" /etc/apache2/sites-available/
a2enmod -q alias headers mime ratelimit ssl
a2dissite -q 000-default || true

fail=0
check() { # check <description> <expected> <actual>
    if [[ "$2" == "$3" ]]; then echo "ok    $1"; else echo "FAIL  $1: want '$2', got '$3'"; fail=1; fi
}
code() { curl -sk -o /dev/null -w '%{http_code}' "$@"; }
header() { curl -skI "$2" | tr -d '\r' | sed -n "s/^$1: //Ip" | head -n 1; }

run_checks() { # run_checks <base url> <https 0/1>
    local b=$1
    check "$b/ page" 200 "$(code "$b/")"
    check "$b/browse.html" 200 "$(code "$b/browse.html")"
    check "$b/data/site_config.json (site data)" 200 "$(code "$b/data/site_config.json")"
    check "$b/data/cmip6/x/tas.nc" 200 "$(code "$b/data/cmip6/x/tas.nc")"
    check ".nc range request" 206 "$(code -r 0-99 "$b/data/cmip6/x/tas.nc")"
    check ".nc content type" application/x-netcdf "$(header Content-Type "$b/data/cmip6/x/tas.nc")"
    check "$b/data/ listing" 403 "$(code "$b/data/")"
    check "$b/data/cmip6/x/ listing" 404 "$(code "$b/data/cmip6/x/")"
    check "non-.nc store file" 404 "$(code "$b/data/cmip6/x/notes.txt")"
    check "traversal" 400 "$(code --path-as-is "$b/data/../../etc/passwd.nc")"
    check "nosniff" nosniff "$(header X-Content-Type-Options "$b/")"
    check "CSP set" yes "$([[ -n "$(header Content-Security-Policy "$b/")" ]] && echo yes || echo no)"
    if (($2)); then
        check "HSTS on https" "max-age=300" "$(header Strict-Transport-Security "$b/")"
    else
        check "no HSTS on http" "" "$(header Strict-Transport-Security "$b/")"
    fi
}

echo "== interim HTTP vhost"
a2dissite -q cmip || true
a2ensite -q cmip-http-interim
apache2ctl configtest
apache2ctl -k restart 2>/dev/null || apache2ctl -k start
sleep 1
run_checks http://localhost 0

echo "== HTTPS vhost"
a2dissite -q cmip-http-interim
a2ensite -q cmip
apache2ctl configtest
apache2ctl -k restart
sleep 1
check "http redirects" 301 "$(code http://localhost/browse.html)"
check "redirect target" https://cmip.whoi.edu/browse.html "$(header Location http://localhost/browse.html)"
run_checks https://localhost 1

exit "$fail"
