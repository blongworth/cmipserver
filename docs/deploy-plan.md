# Plan: make cmip.whoi.edu live

Date: 2026-11-06. Status: **proposal, not started.** Steps 6–8 of
`docs/static-site-plan.md` (tasks 4.1, 5.7, 5.8, 5.13, 6.2, 6.4).

**Needs approval:** the VM **pulls** each build from VAST instead of HPC
pushing it over SSH (static-site-plan §2, A.2). No deploy key, no HPC-to-VM
connection (IT-10 goes away), and the VM runs only Apache and rsync. The push
variant is in the appendix; everything else is the same.

## 1. Overview

```
HPC nightly job (service account, fresh checkout of main)
  build_catalog.py -> check_catalog.py -> summarize_catalog.py -> quarto render
  publish_site.sh  -> VAST <site dir>/releases/<stamp>/ + BUILD_OK, then LATEST

VM (Ubuntu, Apache), VAST mounted read-only:
  /vast/cmip6      <- /proj/cmip6 (data)
  /vast/cmip-site  <- <site dir> (outside /proj/cmip6)
  pull_site.sh (every 15 min): copy new release -> smoke test -> staging -> current
  https://cmip.whoi.edu/       -> /srv/cmip/current
  https://cmip.whoi.edu/data/  -> /vast/cmip6 (.nc only, no listing, rate limited)
  staging site                 -> /srv/cmip/staging (WHOI-only)

GitHub Actions: checks only.
```

Catalogs in the nightly job: every enabled product in
`manifests/catalogs.yaml` (`build_catalog.py --manifest`). Generic non-CMIP6
products (`builder: generic`) also need `--ncinfo` pointing at the
`check_nc.py` output for their tree; files with no ncinfo row are left out
(invalid) until `check_nc.py` is re-run on them. `summarize_catalog.py` gets one
`--catalog` per enabled product's CSV (`whoi_cmip6`, `whoi_20crv3`, then
`whoi_era5`, `whoi_glorys12v1`, ... as they are enabled) plus
`--products manifests/catalogs.yaml` for the dataset pages' citations.

## 2. Decisions needed

| Decision | ID | Blocks |
|---|---|---|
| Pull or push deploy | new | Scripts, IT requests |
| Public or WHOI-only | PI-4 | Access rules, TLS, IT review |
| VAST paths on the site | PI-5 | External build (§5.3) |
| Catalog path on VAST | C-4 | `catalog_path`, nightly job |
| Rate limits, security review | C-17 | Go-live |
| VM owner and patching | IT-8 | VM setup |
| TLS certificate | IT-9 | VM setup |
| Staging host name or port | — | VM setup |
| `_variables.yml` placeholders: `github_repo`, `help_email`, `catalog_path`, `https_prefix` (proposal `https://cmip.whoi.edu/data`) | — | Rollout |
| Snapshot naming (not blocking) | C-12 | — |

## 3. Requests (drafts for Brett to send)

- **Storage:** a VAST site directory outside `/proj/cmip6`, writable by the
  HPC service account. Export it and `/proj/cmip6` read-only to the VM.
- **HPC:** a service account for a nightly job (`scrontab` or cron), with
  outbound HTTPS to GitHub.
- **IT:** DNS for `cmip.whoi.edu`; firewall 443/80 per PI-4, SSH from admin
  hosts only; TLS certificate; staging name; security review (C-17); VM
  patching (IT-8); uptime monitoring; optional mail relay for alerts.

## 4. HPC

**Environment (project space, service account):**

- Quarto 1.10.19 (same as CI), `uv`, a repo clone used only for `git fetch`.
- GitHub read access: public repo or a read-only deploy key.

**Nightly build rules:**

- Build in a fresh checkout (`git worktree add --detach <tmp> origin/main`),
  so writing real `site/data/` never dirties the clone.
- Production settings (`catalog_version` = build date, `audience`,
  `catalog_path`, `https_prefix`) come from the build: a Quarto profile
  plus a production values file read by `write_site_config.py`.
- Notebooks are executed by the nightly render on HPC, where the catalog is
  reachable (fresh checkout, so no stale `_freeze/`). They are committed without
  outputs; CI renders with `--no-execute`.

**Code to write (in this repo):**

| File | Runs on | Does |
|---|---|---|
| `scripts/build_catalog.py` (4.1) | HPC | ecgtools scan -> catalog CSV + JSON with `master_id`, `size_bytes`; write temp, then rename |
| `scripts/check_catalog.py` | HPC | Fail on schema errors or large swings vs the last catalog (e.g. >5% fewer datasets). Report, never fix. |
| `slurm/nightly.sbatch` | HPC | All steps, `set -euo pipefail`, `--mail-type=FAIL` |
| `scripts/publish_site.sh` | HPC | `_site/` -> `releases/<UTC stamp>/`; write `BUILD_OK` (stamp, commit, catalog version, file count, checksum), then `LATEST`. Keep 14. Dry run unless `--publish`. |
| `infra/vm/pull_site.sh` | VM | New `LATEST` with `BUILD_OK` -> rsync to `/srv/cmip/releases/<stamp>/`, check count, smoke test, switch `staging` symlink; promote to `current` unless `HOLD`. Keep 7. Also `--promote`, `--rollback <stamp>`. |
| `infra/vm/smoke_test.sh` | VM | Key pages exist; summary and config JSON parse; rows > 0; `vendor/` hashes match |
| `infra/vm/cmip-pull.{service,timer}` | VM | Every 15 min as `cmipsite` |
| `infra/apache/cmip.conf` (5.13) | VM | §5.2, for IT review |
| `docs/runbook-site.md` (6.4) | — | §7 |

Tests: pytest for `check_catalog.py` and `publish_site.sh --dry-run`;
`shellcheck` in CI.

## 5. VM (Ubuntu 24.04+)

### 5.1 System

```bash
sudo apt install apache2 rsync libapache2-mod-qos unattended-upgrades fail2ban
sudo a2enmod ssl headers ratelimit qos && sudo a2dissite 000-default
sudo useradd --system --home /srv/cmip --shell /usr/sbin/nologin cmipsite
sudo install -d -o cmipsite -g www-data -m 2755 /srv/cmip /srv/cmip/releases
```

- `/etc/fstab`: `/vast/cmip6` and `/vast/cmip-site`, `ro,nosuid,nodev,noexec`.
  Check that `www-data` can read a `.nc` file and `cmipsite` can read releases.
- `ufw`: 443 and 80 per PI-4; 22 from admin hosts.

### 5.2 Apache

- `:80` redirects to `https://`.
- `/` -> `/srv/cmip/current`: `Options -Indexes SymLinksIfOwnerMatch`,
  `AllowOverride None`. Verify only the docroot symlink is followed.
- `/data` -> `/vast/cmip6`: `Options -Indexes -FollowSymLinks`,
  `Require all denied`, then allow `\.nc$` only. `application/x-netcdf`,
  range requests on, no compression.
- Rate limits (tune from logs): `mod_ratelimit` 20 MB/s per connection,
  `mod_qos` 10 connections per IP.
- Headers: HSTS, `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
  CSP `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'`
  (Quarto's OJS runtime and inline scripts need the `unsafe-*`).
- Cache: `vendor/`, `site_libs/` long; HTML and `data/` `max-age=300`.
- Staging: same, docroot `/srv/cmip/staging`, WHOI-only, `X-Robots-Tag: noindex`.
- Access per PI-4: `Require all granted`, or `Require ip <WHOI ranges from IT>`
  or WHOI SSO.
- fail2ban on the access log; agree log retention (logs hold IPs).

### 5.3 External build (if PI-5 says no VAST paths)

Drop `vast_prefix` from `site_config.json` when `audience: external`, make
the `vast_prefix` text on `about`, `access`, `catalog`, `index` depend on the
audience, and give external users an HTTPS catalog instead of the prefix swap.

### 5.4 TLS

Public: `certbot --apache -d cmip.whoi.edu`. WHOI-only: IT certificate or
ACME DNS challenge (IT-9).

## 6. Rollout

1. HPC dry runs with the sample catalog; then `publish_site.sh --publish`.
2. Staging only, manual promotion (`pull_site.sh --promote`); run §8 checks.
3. Real catalog when the reorganization allows (5.7); check size and load
   time at ~28k datasets (C-6).
4. IT security review (C-17).
5. DNS (6.2); production live. `cmip6.whoi.edu` untouched until 6.3.
6. After two clean weeks, automatic promotion (`HOLD` stops it).
7. Announcement (draft for Brett).

## 7. Operations

- **Rollback:** `pull_site.sh --rollback <stamp>` (sets `HOLD`; remove to resume).
- **Page edits:** merge to `main`, live next night; urgent: run the job by hand.
- **Alerts:** Slurm mail on failure; build date older than 48 h (uptime check
  or HPC check); VM pull errors in `journalctl -u cmip-pull`.
- **Upgrades:** Quarto on HPC and CI together (recheck OJS library versions
  in `browse.qmd`); `vendor_js.py --update`; Ubuntu via unattended-upgrades.
- **Disk:** tens of MB per release; 14 on VAST, 7 on the VM.
- **Logs:** usage summaries from Apache logs (static-site-plan §4.9).

## 8. Go-live checks

- Headers present; `http://` redirects.
- A dataset page's `.nc` link downloads; `curl -r 0-99` returns 206.
- `/data/` directory and non-`.nc` file return 403.
- Rate limits trigger.
- Headless Chrome: filters, counts, URL state, dataset links, no other hosts.
- Off-site access matches PI-4.
- Rollback and back works.
- A broken build leaves `LATEST` alone and the site on the last good release.

## Appendix: push variant

- No VAST site directory or `/vast/cmip-site` mount.
- `publish_site.sh` runs
  `rsync -a --delete --delay-updates _site/ cmipdeploy@cmip.whoi.edu:releases/<stamp>/`.
- VM user `cmipdeploy`, key limited in `authorized_keys`:
  `command="/usr/bin/rrsync -wo /srv/cmip/incoming",restrict ssh-ed25519 ...`.
  The VM timer still smoke-tests and switches symlinks, so the key can't
  change the live site directly.
- Needs IT-10 and protection of the key on HPC.
