# Plan: replace the OJS pages with plain JavaScript modules

Status: **in progress.** Browse is done (2026-11-23, with the layout redesign:
`site/js/common.js`, `site/js/browse.js`, `site/browse.qmd`; headless test
`tests/test_browse_page.py`). Tom Select, Arquero and d3 were removed from
`vendor/`. Still OJS: `dataset.qmd`, `holdings.qmd`, `index.qmd`,
`catalog.qmd`, `variables.qmd`. Step 1 uses headless-Chrome probes (no new
dependency), not Playwright.

## Why

Five pages use Observable JS (OJS) cells: `browse.qmd` (14 cells, ~470
lines), `dataset.qmd` (7), `holdings.qmd` (8), `index.qmd` (2), `catalog.qmd`
(1). OJS is mostly glue here: Tom Select, Tabulator and the Get data panel are
imperative code wrapped to "behave as an Observable input". Costs seen so far:

- Every page shadows `html` (and Browse also `d3`, `Inputs`) so Quarto's
  OJS runtime does not fetch them from jsDelivr, which the CSP blocks.
  `catalog.qmd` missed this until 2026-11-20.
- Quarto adds `btn-quarto` to buttons in OJS output (overridden in
  `theme.scss`).
- Reactive cells that are hard to follow and debug; helpers copied between
  pages (`copyBlock`, `reportUrl`, `isPlaceholder`, the `vendor()` loader).

## Target

Quarto still builds the pages (layout, navbar, search, theme, blog). Each
interactive page keeps a `.qmd` with its text and an empty mount point, and
loads one ES module:

```
site/js/
  common.js     config + JSON loading, vendor(), copyBlock, reportUrl,
                isPlaceholder, formatting, URL-state helpers
  browse.js     facets (Tom Select), table (Tabulator), search, Get data
  dataset.js    dataset page
  holdings.js   holdings tables, Other holdings
  home.js       stats band; catalog.js: per-catalog table
```

```html
<div id="browse"></div>
<script type="module" src="js/browse.js"></script>
```

- No build step and no Node on HPC: modules are served as-is from `site/js/`
  (add it to `resources` in `_quarto.yml`). Vendored libraries are imported
  from `vendor/` (Tabulator already ships an ESM build; Tom Select and
  Arquero load as globals or via their ESM builds, pinned in `vendor.yml`).
  `d3`, `htl` and `@observablehq/inputs` are dropped if nothing else needs
  them (Browse uses `d3.sum`/`d3.groups` and `Inputs.search`, which are a few
  lines each).
- Type checking without a build: `// @ts-check` plus JSDoc types (editors
  check as you type). Optional later: `tsc --noEmit` in CI, which needs Node
  only in CI, never on HPC.
- State: one plain object per page (filters, search text, selection), one
  `render()` that updates counts, table and Get data panel. URL state
  (`?source_id=...&search=...`) and the downloaded `urls.txt` stay exactly
  the same, so existing links and scripts keep working.

## Steps

1. **Safety net first.** A headless browser test of the current Browse and
   dataset pages, run in CI against `quarto render --no-execute` output with
   the test fixtures: filters and counts, URL state round trip, a dataset
   page's file list and `urls.txt`, no request to another host. Options:
   Playwright for Python as a dev dependency (simplest in pytest), or
   headless Chrome `--dump-dom` probes (no new dependency, weaker). The same
   test is the go-live check in `docs/deploy-plan.md` §8.
2. **`common.js` + `catalog.js` + `home.js`**: smallest pages, proves the
   module loading, CSP and Quarto integration.
3. **`dataset.js`**: one data fetch, no reactivity; compare output with the
   OJS page for a CMIP6, an ERA5 and a generic-product dataset.
4. **`holdings.js`.**
5. **`browse.js`**, behind a temporary second page (`browse-next.qmd`) until
   the step 1 test passes on both; then swap and delete the OJS version.
6. Clean up: remove the OJS-only vendored files (`d3`, `htl`,
   `@observablehq/inputs`) if unused, the `btn-quarto` override in
   `theme.scss`, and the OJS notes in `site/README.md`.

Each step is a separate commit and can ship on its own; the site works with a
mix of OJS and module pages.

## Effort and risk

Roughly 2-3 days in total, most of it Browse (facet counts, Tom Select
syncing, Tabulator selection, Get data snippets). Main risk is subtle Browse
regressions (facet counts, URL state); step 1 covers them. No data, catalog
or deploy changes.

## Decisions needed

- Playwright (new dev dependency) or headless-Chrome probes for step 1.
- Whether CI may install Node for an optional `tsc --noEmit` check.
