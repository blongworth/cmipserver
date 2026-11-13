# Plan: make cmip.whoi.edu live

Date: 2026-11-13 (replaces the 2026-11-06 proposal). Status: **approved
design; scripts written, nothing deployed.** Steps 6–8 of
`docs/static-site-plan.md` (tasks 5.7, 5.8, 5.13, 6.2, 6.4). Day-to-day
operations: `docs/runbook-site.md`.

Decided (2026-11-13): the VM **pulls** releases from VAST; site builds run
**nightly and on demand**; **no staging host** (local preview, a smoke test
before every switch, and one-command rollback instead); **no containers** in
production (§6).

## 1. Overview

```
HPC: build_catalog.sbatch (existing; slow; nightly or as needed) -> catalog/live/*.csv
HPC: site_build.sbatch    (minutes; nightly via scrontab AND by hand any time)
       git worktree of origin/main -> summarize_catalog.py (live CSVs)
       -> write_site_config.py --production -> quarto render --profile production
       -> publish_site.sh -> /proj/cmip6/site/releases/<UTC stamp>/ + BUILD_OK, then LATEST
VM:  /proj/cmip6 mounted read-only at /vast/cmip6 (data/ for downloads, site/ for releases)
     cmip-pull.timer (every 5 min) -> pull_site.sh
       new LATEST with BUILD_OK? -> rsync to /srv/cmip/releases/<stamp>
       -> file count + SHA256SUMS + smoke_test.sh -> atomic swap of /srv/cmip/current
Apache: /             -> /srv/cmip/current/site
        /data/<p>.nc  -> /vast/cmip6/data/<p>.nc (read-only, rate-limited)
GitHub Actions: checks only (lint, tests, shellcheck, render, links, Apache vhosts).
```

The site build is separate from the catalog build. A page edit does not wait
for a 12 h catalog crawl: **deploy now** is

```bash
sbatch --export=ALL,PUBLISH=1 slurm/site_build.sbatch
```

and the site is live within about 5 minutes of the job ending. The nightly
run picks up catalog changes. A failed build never writes `LATEST`, so the
site stays on the last good release.

Catalogs: `site_build.sbatch` passes `--catalog-dir catalog/live --products
manifests/catalogs.yaml` to `summarize_catalog.py`, which reads
`<output>.csv` for every enabled product (today `cmip6`, `20crv3`). Enabling a
product in the manifest is enough; there is no list in the job. How often
non-CMIP6 products are checked and rebuilt is still undecided (regular or on
demand, per product); the site build uses each product's latest checked CSV
in `catalog/live`. `site/data/holdings.json` (Other holdings page) comes from
`summarize_other.py --site-json` and is committed when the inventory is re-run.

## 2. Decisions

| Decision | ID | Status / blocks |
|---|---|---|
| Pull or push deploy | — | **Pull** (2026-11-13) |
| Where releases live | — | **`/proj/cmip6/site`** (2026-11-13), so one read-only export of `/proj/cmip6` serves data and releases |
| Staging host | — | **None** (2026-11-13) |
| Public or WHOI-only | PI-4 | Open. Blocks access rules, TLS route (§3), IT review |
| TLS certificate | IT-9 | Open (§3). Blocks announcement |
| VM owner and patching | IT-8 | Open. What is 10.128.43.19? (§7) |
| VAST paths on the site | PI-5 | Open. External build (§5.3) |
| Catalog path on VAST | C-4 | `catalog_path` keeps its current value |
| Rate limits, security review | C-17 | Go-live |
| `_variables.yml` placeholders: `github_repo`, `help_email`, `smb_path` | — | Stay `_TBD` until decided; the build lists them on stderr |
| `https_prefix` | — | `<site_url>/data` in `site/_production.yml` (interim `http://`, §3a) |
| Snapshot naming (not blocking) | C-12 | — |

## 3. HTTPS certificates (IT-9)

Checked 2026-11-13 (read-only, from a Mac on the VPN):

- `cmip.whoi.edu` already resolves: an A record to **10.128.43.19**, a
  private address with a matching PTR. Ports 80 and 443 time out. A VM or
  reservation may exist with no web server or no firewall opening.
- `cmip6.whoi.edu` (128.128.216.85, public) is **HTTP only**, Apache 2.4.29 on
  Ubuntu 18.04 (EOL), WordPress. No certificate to reuse.
- `www.whoi.edu` uses an **SSL.com** certificate (WHOI's commercial CA).
  `whoi.edu` has **no CAA records**, so Let's Encrypt is not blocked by policy.
- With only a private IP, Let's Encrypt **HTTP-01 cannot work**.

| If PI-4 is... | Certificate | Renewal |
|---|---|---|
| WHOI-only (private IP, as now) | SSL.com **ACME with EAB** if IT has it (`certbot` with EAB credentials). Otherwise an IT-issued SSL.com certificate. | auto / yearly by hand |
| Public (public IP + firewall 80/443) | Let's Encrypt `certbot --apache -d cmip.whoi.edu` (HTTP-01). Confirm IT allows it. | auto (`certbot.timer`) |
| Either, if IT gives one DNS record | `_acme-challenge.cmip.whoi.edu` CNAME to an acme-dns zone (or a TXT API): Let's Encrypt DNS-01 with a private IP | auto |

A yearly manual certificate needs a calendar reminder. Whatever the route,
monitoring checks expiry and alerts at 21 days left.

### 3a. Interim HTTP (before the certificate)

`https://whoi.edu` sends `Strict-Transport-Security: max-age=31536000;
includeSubDomains` (not preloaded). Anyone who loaded `whoi.edu` in the last
year has `cmip.whoi.edu` forced to HTTPS by their browser; with no
certificate they get a hard error. So plain HTTP works for some people and
not others: **not announceable**. `wget`, `curl`, `xarray`/`fsspec` and
intake ignore HSTS, so `/data/` downloads work over HTTP.

- **Allowed:** a WHOI-only soft launch for Brett and a few test users, browsing
  `http://10.128.43.19/` (an IP is not covered by HSTS) or a fresh browser
  profile.
- **Config:** `infra/apache/cmip-http-interim.conf` (`setup.sh` default): the
  same site on `:80`, no `https` redirect, **no HSTS** (HSTS before TLS works
  locks users out for its max-age). The production build uses
  `site_url: http://cmip.whoi.edu` and `https_prefix:
  http://cmip.whoi.edu/data` (`site/_production.yml`, and `site-url` in
  `site/_quarto-production.yml`; a test keeps them equal).
- **Cutover:** install the certificate; `sudo infra/vm/setup.sh --https`
  (enables `cmip.conf`: 443 plus the 80→443 redirect); switch both files to
  `https://`, merge, and run the site build. HSTS starts at `max-age=300`;
  raise it to a year after a week without problems.

## 4. HPC

**Environment (service account, project space):** Quarto **1.10.19** on
`PATH` (the job refuses another version; bump it with CI), `uv`, and a clone
of this repo used only for `git fetch` (public repo or read-only deploy key).

**`slurm/site_build.sbatch`** (header has usage and the `scrontab` line):

1. `git fetch`; `git worktree add --detach <tmp> origin/main`. Real
   `site/data/` and production settings are written only in the worktree,
   which is removed at exit.
2. `summarize_catalog.py --catalog-dir $CATALOG_DIR --products
   manifests/catalogs.yaml --out site/data` (or `CATALOGS=...` for the
   sample catalog).
3. `write_site_config.py --production site/_production.yml --set
   catalog_version=<date of the newest catalog CSV>`: rewrites
   `_variables.yml` in the worktree (comments kept), so shortcodes and
   `data/site_config.json` agree.
4. `quarto render site --profile production`. Notebooks execute here, where
   the catalog is reachable (fresh worktree, so no stale `_freeze/`).
5. `scripts/publish_site.sh` (dry run unless `PUBLISH=1`): copies `_site/` to
   `releases/.tmp-<stamp>/site/`, adds `site/build.json` (stamp, commit,
   catalog version; for monitoring), writes `SHA256SUMS` and then `BUILD_OK`
   (stamp, commit, catalog_version, files, sums_sha256), renames to
   `releases/<stamp>/`, then replaces `LATEST` by rename. Keeps 10 releases.
   Under `/proj/cmip6` it accepts only `/proj/cmip6/site` itself (it
   prunes with `rm -rf`, so a mistyped destination must not reach the data).

Resources: 2 CPUs, 8 GB, 1 h, `--mail-type=FAIL`.

## 5. VM (Ubuntu 24.04+)

Everything on the VM is in the repo under `infra/`. `infra/vm/setup.sh`
(idempotent; by hand or by IT) rebuilds it from scratch.

### 5.1 System

- Packages: `apache2 rsync python3 certbot python3-certbot-apache
  unattended-upgrades`; with `--public` also `libapache2-mod-qos fail2ban`.
- `/etc/fstab` (IT; `setup.sh` checks, does not edit): `/vast/cmip6` ←
  `/proj/cmip6`, `ro,nosuid,nodev,noexec`. One mount: `data/` for downloads,
  `site/` for releases. Apache serves only `data/**/*.nc` from it.
- User `cmipsite` (system, no shell, group `www-data`) owns `/srv/cmip`.
  `cmip-pull.timer` runs `pull_site.sh` as `cmipsite` every 5 minutes: no
  root, no sudo; the unit can write only `/srv/cmip`.
- `pull_site.sh` copies with `rsync -rlt`, then `chmod go=rX`, so Apache can
  read the release whatever its modes on VAST; it refuses symlinks, checks
  the file count and every checksum, runs `smoke_test.sh`, and only then
  renames the release into place and swaps `current` (`ln -sfn` + `mv -T`).
  A failed release is recorded in `FAILED` and not retried (one alert, not
  one every 5 minutes). Keeps 5 releases plus the live one.
- Firewall (IT): 80/443 per PI-4; 22 from admin hosts only.

### 5.2 Apache

`infra/apache/common.conf` (shared) is included by `cmip.conf` (HTTPS) or
`cmip-http-interim.conf` (§3a). `infra/apache/ci_test.sh` checks both in CI
with real requests.

- `/` → `/srv/cmip/current/site`: `-Indexes +SymLinksIfOwnerMatch` (only the
  `current` link, owned by `cmipsite` like its target), `AllowOverride None`.
- Downloads: `AliasMatch "^/data/(.+\.nc)$" /vast/cmip6/data/$1`. Only `.nc`
  URLs reach VAST, because `/data/` is also the site's own data directory
  (`site_config.json`, the catalog summary); a plain `Alias /data` would hide
  it. `/vast/cmip6/data`: `Options None` (no listing, no symlinks),
  `.nc` only, `application/x-netcdf`, byte ranges on, no compression,
  `mod_ratelimit` 20 MB/s per connection. `/data/` → 403; non-`.nc` → 404.
  Symlinked files in the store are not served; check the catalog has none
  before go-live.
- `mod_qos` 10 connections per IP only if public (commented in `cmip.conf`).
- Headers: `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
  CSP `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval';
  style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors
  'none'` (Quarto's OJS runtime needs the `unsafe-*`). HSTS only on 443.
- Cache: HTML, JSON, XML 5 minutes; `vendor/` and `site_libs/` 1 day (their
  paths carry no version); `.nc` 1 day.
- Access per PI-4: `Require all granted` behind a WHOI-only address, or
  `Require ip <WHOI ranges>` if public.
- Logs hold IPs; agree retention with IT (C-17). fail2ban only if public.

### 5.3 External build (if PI-5 says no VAST paths)

Drop `vast_prefix` from `site_config.json` when `audience: external`, make
the `vast_prefix` text on `about`, `access`, `catalog`, `index` depend on the
audience, and give external users an HTTPS catalog instead of the prefix swap.

## 6. Containers: not in production

- The VM serves static files and a read-only mount from Apache. A container
  would add a runtime, image rebuilds for CVEs, and NFS bind mounts with UID
  mapping, for nothing `apt` plus unattended-upgrades doesn't cover. IT also
  patches plain Ubuntu VMs more easily than Docker hosts (IT-8).
- The build is already reproducible: `uv.lock` plus a pinned Quarto (same
  version as CI). HPC has no Docker; Apptainer would be one more thing to
  update.
- Later, if installing Quarto on HPC becomes a problem: an Apptainer image for
  the **build only**. Not now.

## 7. Requests to IT/HPC (drafts for Brett; not sent)

1. **VM:** What is 10.128.43.19 (`cmip.whoi.edu`)? Is it ours? We need Ubuntu
   24.04, 2 vCPU / 4 GB / 20 GB, IT patching (IT-8), SSH from admin hosts.
2. **TLS (IT-9):** Is SSL.com ACME with EAB available for `cmip.whoi.edu`? Or
   can IT add a CNAME for `_acme-challenge.cmip.whoi.edu` so Let's Encrypt
   DNS-01 works? If neither, please issue a certificate. `whoi.edu` sends
   HSTS with `includeSubDomains`, so plain HTTP fails in many browsers: this
   blocks launch.
3. **Network (PI-4):** internal only, or a public IP with 80/443 open.
4. **Storage:** create `/proj/cmip6/site` (under 1 GB), writable by the HPC
   service account (only that directory); export `/proj/cmip6` **read-only**
   to the VM.
5. **HPC:** a service account with `scrontab` and outbound HTTPS to GitHub.
6. **Monitoring:** uptime check, certificate expiry (21 days), and
   `build.json` older than 48 h; an optional mail relay for
   `cmip-pull.service` failures (`OnFailure=` in the unit).

## 8. Rollout

1. Done 2026-11-13 (local): `publish_site.sh` dry run and publish, then
   `pull_site.sh` and `smoke_test.sh`, against a production-profile render of
   the live catalogs; Apache vhosts checked in an Ubuntu 24.04 container.
2. HPC: `site_build.sbatch` with the sample catalog (dry run), then with
   `PUBLISH=1` to `/proj/cmip6/site`.
3. VM (once IT delivers): `sudo infra/vm/setup.sh` (interim HTTP vhost, timer).
   Check the first pull and smoke test. Soft launch to test users by IP.
4. Certificate: `setup.sh --https`, rebuild with `https://` URLs, HSTS 300 s
   → 1 year after a week.
5. Go-live checks (§9). IT security review (C-17), then the announcement
   (draft for Brett). `cmip6.whoi.edu` stays up until task 6.3.

## 9. Go-live checks

- `http://` redirects to `https://`; headers present (CI checks these on
  the vhost; repeat on the VM).
- A dataset page's `.nc` link downloads; `curl -r 0-99` returns 206.
- `/data/` returns 403; a non-`.nc` store path returns 404.
- Rate limits trigger.
- Headless Chrome: filters, counts, URL state, dataset links, no other
  hosts, no CSP errors in the console.
- Off-site access matches PI-4.
- `pull_site.sh --rollback <stamp>` and `--resume` round trip.
- A broken build leaves `LATEST` alone and the site on the last good release.

## Appendix: push variant (not chosen)

- No `/proj/cmip6/site`; the VM mounts `/proj/cmip6` for downloads only.
- `publish_site.sh` would `rsync -a --delete --delay-updates _site/
  cmipdeploy@cmip.whoi.edu:releases/<stamp>/`.
- VM user `cmipdeploy`, key limited in `authorized_keys`:
  `command="/usr/bin/rrsync -wo /srv/cmip/incoming",restrict ssh-ed25519 ...`.
  The VM timer still checks and switches, so the key can't change the live
  site directly.
- Needs an HPC-to-VM SSH opening (IT-10) and protection of the key on HPC.
