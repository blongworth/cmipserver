# Test fixtures

- `sample_catalog.{csv,json}`, `sample_data/`: **synthetic** catalog and tiny
  NetCDF files from `scripts/make_sample_catalog.py`. Regenerate with
  `uv run scripts/make_sample_catalog.py --out tests/fixtures`; a test fails if
  the committed copy is stale.
- `whoi_20crv3.{csv,json}`: **real** catalog of the 20CRv3 monthly means on
  VAST (10 files, 10 datasets, ~41 GB), copied verbatim from
  `/proj/cmip6/catalog/next/` (built 2026-11-11 by `scripts/build_catalog.py`
  from the `20crv3` product in `manifests/catalogs.yaml`). Paths point at
  `/proj/cmip6/data/reanalysis/20crv3/`; the files are not in the repo.
  Refresh after installing more 20CRv3 files:

  ```bash
  uv run scripts/build_catalog.py --manifest manifests/catalogs.yaml \
      --product 20crv3 --out /proj/cmip6/catalog/next   # on HPC
  cp /proj/cmip6/catalog/next/whoi_20crv3.{csv,json} tests/fixtures/
  ```

  Then regenerate `site/data` (see `site/README.md`).
- `20crv3_land.nc`: **real** 20CRv3 land-sea mask (`land`, 0 = water,
  1 = land, same 1° grid as the monthly means), downloaded 2026-11-11 from
  <https://downloads.psl.noaa.gov/Datasets/20thC_ReanV3/timeInvariantSI/land.nc>
  (39,913 bytes, sha256
  `993a3cb194df9f1ff5e4ad5004d04e07f6a3ea1700bc683ae66abb87bed222ef`). Used by
  the 20CRv3 notebook's map. Not on the store or in the catalog.
