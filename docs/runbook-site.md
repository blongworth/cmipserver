# Runbook: cmip.whoi.edu

Operations for the static site. Design and setup: `docs/deploy-plan.md`.
Releases live in `/proj/cmip6/site` (on the VM: `/vast/cmip6/site`, read-only);
`<clone>` is the service account's repo clone on HPC.

## Change a page

1. Preview locally: `uv run quarto preview site`.
2. Merge to `main` (CI must pass).
3. Wait for the nightly build, or deploy now from `<clone>`:

   ```bash
   sbatch --export=ALL,PUBLISH=1 slurm/site_build.sbatch
   ```

   Live about 5 minutes after the job ends. Check `https://cmip.whoi.edu/build.json`.

## Is it working?

On the VM:

```bash
/usr/local/lib/cmip-site/pull_site.sh --status   # current, LATEST, HOLD, FAILED, releases
journalctl -u cmip-pull --since today            # pull log
systemctl list-timers cmip-pull.timer
```

On HPC: `logs/site_build_<job>.out`; Slurm mails on failure.

## Bad release

```bash
sudo -u cmipsite /usr/local/lib/cmip-site/pull_site.sh --status
sudo -u cmipsite /usr/local/lib/cmip-site/pull_site.sh --rollback <stamp>  # sets HOLD
```

Fix on `main`, run the site build, then:

```bash
sudo -u cmipsite /usr/local/lib/cmip-site/pull_site.sh --resume   # clears HOLD, pulls LATEST
```

`--hold` stops updates without switching. A rollback to a stamp no longer on
the VM copies it from VAST (10 kept there, 5 on the VM).

## A release failed its checks

`pull_site.sh` logs the reason (count, checksum, or smoke test) and writes the
stamp to `/srv/cmip/FAILED`; the site stays on the previous release and the
same build is not retried. Look at the log, fix, and run a new build. To retry
the same build (e.g. after a VAST hiccup): `pull_site.sh --resume`.

## A build failed on HPC

Nothing changes on the site (`LATEST` is written only after a complete
release). Read the log, fix, re-run. Common causes: a catalog CSV missing
from `catalog/live` for an enabled product; Quarto version on `PATH` differs
from the one the job pins.

## Alerts and checks

- Slurm mail on a failed build.
- Uptime monitor on `/`; `build.json` older than 48 h; certificate expiry
  (alert at 21 days).
- `cmip-pull.service` failures: journal, plus mail if `OnFailure=` is set up.

## Upgrades

- OS: unattended-upgrades / IT.
- Quarto: bump in `.github/workflows/check.yml`, `slurm/site_build.sbatch`
  (`QUARTO_VERSION`), `site/README.md`, and on HPC together, about twice a
  year. Recheck the OJS library versions in `browse.qmd`.
- Vendored JS: `uv run scripts/vendor_js.py --update` after a version bump.
- Certificate: renews itself (certbot), or yearly by hand if IT-issued.
- VM config: edit under `infra/`, merge, copy `infra/` to the VM (or `git
  pull` in a checkout there), then `sudo infra/vm/setup.sh` (add `--https`
  once TLS is live).

## Disk

Tens of MB per release: 10 on VAST, 5 (plus the live one) on the VM.
