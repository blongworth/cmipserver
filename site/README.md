# Site (Quarto)

Static site for `cmip.whoi.edu`. Plan: `../docs/static-site-plan.md`.

- **Quarto 1.10.19** (pinned; install the same version on HPC in project space).
- Python environment: the repo's uv project, `site` group (`uv sync --all-groups`).

```bash
# from the repo root
L=/proj/cmip6/catalog/live   # ~/mnt/cmip6/catalog/live on a mac with the mount
uv run scripts/summarize_catalog.py --catalog $L/whoi_cmip6.csv --catalog $L/whoi_20crv3.csv --out site/data
uv run quarto render site      # or: uv run quarto preview site
```

- `_variables.yml` holds hosts, prefixes, and addresses. `_TBD` values are
  placeholders. The pre-render step copies the browser's settings into
  `data/site_config.json`.
- `data/` holds the committed catalog summary (CMIP6 + reanalysis, ~60 MB).
- `vendor/` holds pinned third-party JavaScript, CSS, and fonts (Tom Select,
  Tabulator, Arquero, the OJS stdlib pieces the browser uses, and the cosmo
  font), so pages load nothing from CDNs. `vendor/vendor.yml` lists each file
  with its version, source URL, and sha256. `uv run scripts/vendor_js.py`
  downloads and checks them; after bumping a version, run it with `--update`
  to record the new hashes, then commit the files. `theme.scss` and
  `fonts.css` stop cosmo from loading Google Fonts.
- Notebooks (`notebooks/*.ipynb`) use `freeze: auto`. Re-execute them where
  the catalog is reachable and commit `_freeze/`; CI renders from frozen output.
  Set `WHOI_CMIP_CATALOG` (and optionally `WHOI_CMIP_DATA_ROOT`) to point the
  quickstart at another catalog.
- Remove `old_site_url` from `_variables.yml` and the "Old site" navbar entry in
  `_quarto.yml` when the old server is retired (task 6.3).
