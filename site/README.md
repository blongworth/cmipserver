# Site (Quarto)

Static site for `cmip.whoi.edu`. Plan: `../docs/static-site-plan.md`.

- **Quarto 1.10.19** (pinned; install the same version on HPC in project space).
- Python environment: the repo's uv project, `site` group (`uv sync --all-groups`).

```bash
# from the repo root
L=/proj/cmip6/catalog/live   # ~/mnt/cmip6/catalog/live on a mac with the mount
uv run scripts/summarize_catalog.py --catalog $L/whoi_cmip6.csv --catalog $L/whoi_20crv3.csv --products manifests/catalogs.yaml --out site/data
# add --vocab $L/whoi_cmip_vocabulary.csv once the vocabulary is in live
uv run quarto render site      # or: uv run quarto preview site
```

- `_variables.yml` holds hosts, prefixes, and addresses. `_TBD` values are
  placeholders. The pre-render step copies the browser's settings into
  `data/site_config.json`.
- `data/` is generated and not committed: the catalog summary from
  `summarize_catalog.py` (above; `variables.json`, the store vocabulary for
  the Variables page and the browser's search, is empty without `--vocab`)
  and `site_config.json` from the pre-render
  step. The exception is `holdings.json` (Other holdings page), which comes
  from an occasional inventory scan via `summarize_other.py` and is committed. For a quick preview without
  the store, build it from the test fixtures:
  `uv run scripts/summarize_catalog.py --catalog tests/fixtures/sample_catalog.csv --catalog tests/fixtures/whoi_20crv3.csv --products manifests/catalogs.yaml --out site/data`
- `vendor/` holds pinned third-party JavaScript, CSS, and fonts (Tom Select,
  Tabulator, Arquero, the OJS stdlib pieces the browser uses, and the cosmo
  font), so pages load nothing from CDNs. `vendor/vendor.yml` lists each file
  with its version, source URL, and sha256. `uv run scripts/vendor_js.py`
  downloads and checks them; after bumping a version, run it with `--update`
  to record the new hashes, then commit the files. `theme.scss` and
  `fonts.css` stop cosmo from loading Google Fonts.
- Notebook pages also need require.js, jQuery, plotly.js and MathJax, which
  Quarto and plotly inject as CDN URLs. The post-render step
  `scripts/localize_site_scripts.py` points those at `vendor/` (each file's
  `url` and `aliases` in `vendor.yml`) and fails the render if any other
  external script or stylesheet is left. After upgrading Quarto or the plotly
  Python package, a failing render names the new URL: add it to `vendor.yml`
  and run `vendor_js.py --update`.
- Notebooks (`notebooks/*.ipynb`) are committed without outputs (quarto writes
  outputs back into them, so strip before committing). `quarto render` executes
  them where the catalog is reachable (hydra, or a Mac with the mount) and
  caches results in `_freeze/` (`freeze: auto`; untracked and ignored). CI
  renders with `--no-execute`, so its pages have code but no output.
  Set `WHOI_CMIP_CATALOG` (and optionally `WHOI_CMIP_DATA_ROOT`) to point the
  quickstart at another catalog.
- Remove `old_site_url` from `_variables.yml` and the "Old site" navbar entry in
  `_quarto.yml` when the old server is retired (task 6.3).
