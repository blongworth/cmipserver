#!/usr/bin/env bash
# Set up (or re-apply) the cmip.whoi.edu VM from this repo. Ubuntu 24.04 LTS.
# Idempotent: safe to run again after changing anything under infra/.
#
#   sudo infra/vm/setup.sh            HTTP vhost for the soft launch (§3a)
#   sudo infra/vm/setup.sh --https    HTTPS vhost (certificate must be installed)
#   sudo infra/vm/setup.sh --public   also install mod_qos and fail2ban (PI-4)
#
# Does not edit /etc/fstab or the firewall: IT provides the VAST exports and
# the firewall rules; this checks the mounts and stops if they are missing.
# Expected mounts (read-only):
#   <VAST>:/proj/cmip6      /vast/cmip6      nfs  ro,nosuid,nodev,noexec  0 0
#   <VAST>:<site dir>       /vast/cmip-site  nfs  ro,nosuid,nodev,noexec  0 0

set -euo pipefail

https=0
public=0
for a in "$@"; do
    case "$a" in
        --https) https=1 ;;
        --public) public=1 ;;
        -h | --help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $a" >&2; exit 2 ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
apache_src=$(cd "$here/../apache" && pwd)
say() { echo "== $*"; }

say packages
pkgs=(apache2 rsync python3 certbot python3-certbot-apache unattended-upgrades)
((public)) && pkgs+=(libapache2-mod-qos fail2ban)
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${pkgs[@]}"

say mounts
for m in /vast/cmip6 /vast/cmip-site; do
    if ! mountpoint -q "$m"; then
        echo "$m is not mounted; add it to /etc/fstab (see the header) and mount it" >&2
        exit 1
    fi
done

say "user cmipsite and /srv/cmip"
id cmipsite >/dev/null 2>&1 ||
    useradd --system --home-dir /srv/cmip --shell /usr/sbin/nologin --gid www-data cmipsite
install -d -o cmipsite -g www-data -m 0755 /srv/cmip /srv/cmip/releases
runuser -u cmipsite -- test -r /vast/cmip-site/LATEST ||
    echo "warning: cmipsite cannot read /vast/cmip-site/LATEST (no release published yet?)" >&2

say scripts
install -d -m 0755 /usr/local/lib/cmip-site
install -m 0755 "$here/pull_site.sh" "$here/smoke_test.sh" /usr/local/lib/cmip-site/

say apache
install -d -m 0755 /etc/apache2/cmip
install -m 0644 "$apache_src/common.conf" /etc/apache2/cmip/common.conf
install -m 0644 "$apache_src/cmip.conf" "$apache_src/cmip-http-interim.conf" \
    /etc/apache2/sites-available/
a2enmod -q alias headers mime ratelimit
((https)) && a2enmod -q ssl
((public)) && a2enmod -q qos
a2dissite -q 000-default || true
if ((https)); then
    a2dissite -q cmip-http-interim || true
    a2ensite -q cmip
else
    a2dissite -q cmip || true
    a2ensite -q cmip-http-interim
fi

say "systemd timer"
install -m 0644 "$here/cmip-pull.service" "$here/cmip-pull.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cmip-pull.timer

say "first pull"
systemctl start cmip-pull.service ||
    echo "warning: first pull failed; see journalctl -u cmip-pull" >&2

apachectl configtest
systemctl reload-or-restart apache2
say "done: $(runuser -u cmipsite -- /usr/local/lib/cmip-site/pull_site.sh --status | head -n 1)"
