# Site (Quarto)

Static site for `cmip.whoi.edu`. Plan: `../docs/static-site-plan.md`.

- **Quarto 1.10.19** (pinned; install the same version on HPC in project space).
- Python environment: the repo's uv project, `site` group (`uv sync --all-groups`).

```bash
# from the repo root
uv run scripts/make_sample_catalog.py --out tests/fixtures
uv run scripts/summarize_catalog.py --catalog tests/fixtures/sample_catalog.csv --out site/data
uv run quarto render site      # or: uv run quarto preview site
```

- `_variables.yml` holds hosts, prefixes, and addresses. `_TBD` values are
  placeholders. The pre-render step copies the browser's settings into
  `data/site_config.json`.
- `data/` holds the small committed catalog summary (sample for now).
- Notebooks (`notebooks/*.ipynb`) use `freeze: auto`. Re-execute them where
  the catalog is reachable and commit `_freeze/`; CI renders from frozen output.
  Set `WHOI_CMIP_CATALOG` (and optionally `WHOI_CMIP_DATA_ROOT`) to point the
  quickstart at another catalog.
- Remove `old_site_url` from `_variables.yml` and the "Old site" navbar entry in
  `_quarto.yml` when the old server is retired (task 6.3).
