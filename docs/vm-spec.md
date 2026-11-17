# VM specification for cmip.whoi.edu

Status: **draft for Brett to review and send to IT.** Derived from
`docs/deploy-plan.md` (§3, §5). Items marked **TBD** are open decisions; do
not fill them with guesses.

## Purpose

One Linux VM serves a static website (`https://cmip.whoi.edu`) and read-only
HTTPS downloads of NetCDF files held on VAST. No database, CMS, PHP, or search
service. The site is built nightly on HPC and copied to the VM; the VM only
runs Apache and rsync.

## 1. Questions for IT

| # | Question | ID |
|---|---|---|
| 1 | Who owns and patches the VM? Is config management (Puppet, Ansible, Salt) already in use, and should our settings go through it? | IT-8 |
| 2 | TLS: answered. A Let's Encrypt certificate is in `/etc/letsencrypt/live/cmip.whoi.edu/`. Confirm renewal works and reloads Apache. | IT-9 |
| 3 | Can the VM mount VAST read-only (NFS export or other)? Export host and path: **TBD** | |
| 4 | SMB share name for user laptops (maps to the data directory): **TBD** | |
| 5 | Public or WHOI-only access, and WHOI IP ranges or SSO if restricted | PI-4 |
| 6 | Security review and rate-limit expectations before go-live | C-17 |
| 7 | DNS for `cmip.whoi.edu` and a staging name (or port): **TBD** | |
| 8 | Monitoring (uptime, disk) and log retention (access logs contain IPs) | |

## 2. VM

- Ubuntu 24.04 LTS or newer. Small (2 vCPU, 4 GB RAM is plenty); local disk
  of a few GB for the site (tens of MB per release, 7 kept).
- Packages: `apache2`, `rsync`, `libapache2-mod-qos`, `unattended-upgrades`,
  `fail2ban`; Apache modules `ssl`, `headers`, `ratelimit`, `qos`.
  Disable the default site.
- Automatic security updates on. (The old server's unpatched Apache is the
  reason for this rebuild.)
- Firewall: 443 and 80 per PI-4; SSH from admin hosts only.
- A system user `cmipsite` (no login shell) owns `/srv/cmip` and runs the
  pull job. It has no write access to any VAST mount.

## 3. Storage mounts (read-only)

| Mount point | Source | Options |
|---|---|---|
| `/vast/cmip6` | VAST `10.175.9.41:/proj/cmip6` (whole export) | `ro,nosuid,nodev,noexec,nconnect=8,_netdev,nofail` |
| `/vast/cmip-site` | VAST site directory, outside `/proj/cmip6` | `ro,nosuid,nodev,noexec` |

- Enforce read-only on the **export** (per-client read-only policy for the VM)
  as well as in `/etc/fstab`, so the VM cannot write even if local options
  change.
- fstab line (was `defaults,nconnect=8,_netdev,nofail`):
  `10.175.9.41:/proj/cmip6 /vast/cmip6 nfs ro,nosuid,nodev,noexec,nconnect=8,_netdev,nofail 0 0`
  After editing, run `systemctl daemon-reload` and
  `mount -o remount,ro /vast/cmip6`.
- Check: `findmnt -no OPTIONS /vast/cmip6` shows `ro`; `touch` on the mount
  fails with "Read-only file system"; `www-data` can read a `.nc` file and
  `cmipsite` can read the releases.
- The export is all of `/proj/cmip6`, so Apache must serve only
  `/vast/cmip6/data`. `catalog/` and other trees stay out of the web root.

## 4. Apache

- `:80` redirects to `https://`.
- `/` serves `/srv/cmip/current` (a symlink to the live release):
  `Options -Indexes SymLinksIfOwnerMatch`, `AllowOverride None`.
- `/data` is an `Alias` to `/vast/cmip6/data` (`/proj/cmip6/data`):
  - `Options -Indexes -FollowSymLinks`; deny by default, allow `\.nc$` only.
  - Content type `application/x-netcdf`; range requests on; no compression.
- Rate limits (tune from logs): `mod_ratelimit` 20 MB/s per connection,
  `mod_qos` 10 connections per IP.
- Headers: HSTS, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, CSP
  `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'`
  (the site's dataset browser needs the `unsafe-*` script sources).
- Cache: long for `vendor/` and `site_libs/`; `max-age=300` for HTML and
  `data/`.
- Staging: same config, docroot `/srv/cmip/staging`, WHOI-only,
  `X-Robots-Tag: noindex`.
- `apachectl configtest` before every reload.

## 5. Deploy mechanism (pull)

- HPC publishes each build to the VAST site directory. A timer on the VM
  (`cmip-pull.timer`, every 15 min, as `cmipsite`) copies a new release to
  `/srv/cmip/releases/<stamp>/`, smoke-tests it, then switches `staging` and
  (unless a `HOLD` file exists) `current` symlinks.
- No HPC-to-VM connection or deploy key is needed (IT-10 does not apply).
- Rollback is one command and sets `HOLD`.
- Scripts are in this repo under `infra/vm/` (not yet written; see
  `docs/deploy-plan.md` §4).

## 6. Acceptance checks

- `http://` redirects to `https://`; headers present.
- A `.nc` link under `/data` downloads, and `curl -r 0-99` returns 206.
- `/data/` listing and any non-`.nc` file return 403.
- Rate limits trigger.
- Mount is read-only (section 3).
- Access matches PI-4 from on and off the WHOI network.
- A failed build leaves the site on the last good release.

## 7. What this unblocks in the repo

Once answered, these placeholders in `site/_variables.yml` can be set:
`smb_path` (question 4) and `https_prefix` (proposal
`https://cmip.whoi.edu/data`, once section 4 is in place).
