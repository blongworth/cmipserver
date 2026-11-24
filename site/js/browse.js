// @ts-check
// Dataset browser (site/browse.qmd). One state object, one render().
//
// Data (site/data/, from scripts/summarize_catalog.py):
//   catalog_summary.json  one row per dataset (SUMMARY_SCHEMA)
//   variables.json        store vocabulary rows; [] without --vocab
//   catalog_stats.json    totals per catalog
//   site_config.json      prefixes and settings from _variables.yml
//   files/*.json          file names per dataset, fetched for urls.txt
//
// URL state (kept compatible with the old OJS page and the Variables page):
//   ?<facet>=<value>&...&search=<text>   ("search", not "q": Quarto's site
//   search reads and removes ?q= on page load)

import {
  vendor, vendorCss, loadJson, dataUrl, isPlaceholder, fmt, plural, el,
  copyBlock, joinVocabulary, searchWords, put, size
} from "./common.js";

const MAX_SNIPPET = 50; // datasets in the Python snippet and path list
const TOP_N = 8; // facet values shown before "Show all"

/**
 * Facets in the order people narrow a search. `field` names an array field
 * (a dataset can have several variables or standard names); `more` facets
 * sit in the collapsed "More filters" section. Facets with nothing to pick
 * for the current results are hidden.
 */
const FACETS = [
  {key: "variable_id", label: "Variable", field: "variables"},
  {key: "standard_name", label: "Quantity (CF standard name)", field: "standard_names"},
  {key: "experiment_id", label: "Experiment"},
  {key: "source_id", label: "Source (model)"},
  {key: "product", label: "Product"},
  {key: "frequency", label: "Frequency"},
  {key: "table_id", label: "Table"},
  {key: "level", label: "Level"},
  {key: "member_id", label: "Member"},
  {key: "activity_id", label: "Activity", more: true},
  {key: "institution_id", label: "Institution", more: true},
  {key: "grid_label", label: "Grid", more: true},
  {key: "version", label: "Version", more: true}
];
const COLLECTION = {key: "collection", label: "Collection"};
const ALL_FACETS = [COLLECTION, ...FACETS];
const COLLECTION_LABELS = {cmip6: "CMIP6", reanalysis: "Reanalysis", ocean_reanalysis: "Ocean reanalysis"};
const collectionLabel = (c) => COLLECTION_LABELS[c] ?? c;

const values = (row, f) => (f.field ? row[f.field] : row[f.key] != null ? [row[f.key]] : []);

const $ = (id) => /** @type {HTMLElement} */ (document.getElementById(id));

async function main() {
  vendorCss("tabulator-tables/tabulator_bootstrap5.min.css");
  const [summary, vocab, stats, config, tabulator] = await Promise.all([
    loadJson("catalog_summary.json"),
    loadJson("variables.json", []),
    loadJson("catalog_stats.json"),
    loadJson("site_config.json"),
    import(vendor("tabulator-tables/tabulator_esm.min.js"))
  ]);
  const {rows, varInfo} = joinVocabulary(summary, vocab);
  for (const r of rows) {
    r.model = r.source_id ?? r.product; // CMIP6 model or reanalysis product
    r.cadence = r.table_id ?? r.frequency;
  }

  // Every value of every facet, for URL checks and "Show all".
  const known = new Map(ALL_FACETS.map((f) => [f.key, new Set()]));
  for (const r of rows) for (const f of ALL_FACETS) for (const v of values(r, f)) known.get(f.key)?.add(v);

  const state = {
    picks: new Map(ALL_FACETS.map((f) => [f.key, new Set()])),
    search: "",
    basket: new Map(), // dataset_id -> row, kept across searches
    showBasket: false,
    showAll: new Set(), // facets expanded past TOP_N
    facetText: new Map(), // facet key -> text in its filter box
    columns: new Map() // column field -> visible, set from the Columns menu
  };

  // ---- URL in ----
  const dropped = [];
  const params = new URLSearchParams(location.search);
  for (const [k, raw] of params) {
    if (k === "search") { state.search = raw; continue; }
    const set = state.picks.get(k);
    // variable_id from a multi-variable dataset (Variables page) is "a,b,c"
    const parts = k === "variable_id" ? raw.split(",") : [raw];
    for (const v of parts) {
      if (set && known.get(k)?.has(v)) set.add(v);
      else dropped.push(`${k}=${v}`);
    }
  }

  renderIntro(stats, config);
  if (dropped.length) {
    put($("browse-warning"), el("div", {class: "alert alert-warning py-2 small"},
      "Ignored link values not in the catalog: ",
      dropped.map((s, i) => [i ? ", " : "", el("code", {}, s)])));
  }

  // ---- search box ----
  const search = /** @type {HTMLInputElement} */ ($("browse-search"));
  search.value = state.search;
  let searchTimer = 0;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => { state.search = search.value; render(); }, 150);
  });

  // ---- table ----
  const Tabulator = tabulator.TabulatorFull;
  let syncing = false; // true while the page (not the user) changes the selection
  const columnDefs = [
    {formatter: "rowSelection", titleFormatter: "rowSelection", hozAlign: "center", headerHozAlign: "center",
     headerSort: false, resizable: false, width: 44, frozen: true, download: false},
    {field: "model", title: "model / product"},
    {field: "experiment_id", title: "experiment", auto: true},
    {field: "member_id", title: "member", auto: true},
    {field: "variable_id", title: "variable", tooltip: (e, cell) => cell.getRow().getData().var_tip ?? "",
     formatter: (cell) => {
       const vs = cell.getRow().getData().variables;
       return vs.length > 3 ? `${vs.slice(0, 3).join(", ")} +${vs.length - 3}` : vs.join(", ");
     }},
    {field: "long_name", title: "long name", maxWidth: 280, auto: true,
     tooltip: (e, cell) => cell.getRow().getData().var_tip ?? "", formatter: (cell) => cell.getValue() ?? "—"},
    {field: "cadence", title: "table / freq"},
    {field: "level", title: "level", auto: true},
    {field: "time_start", title: "start", formatter: (cell) => cell.getValue() ?? "—"},
    {field: "time_end", title: "end", formatter: (cell) => cell.getValue() ?? "—"},
    {field: "size_gb", title: "GB", hozAlign: "right", sorter: "number",
     formatter: (cell) => fmt(cell.getValue(), 1)},
    {field: "n_files", title: "files", hozAlign: "right", sorter: "number",
     formatter: (cell) => {
       const d = cell.getRow().getData();
       const a = el("a", {href: `dataset.html?id=${encodeURIComponent(d.dataset_id)}`,
         title: `Download links for the files in ${d.dataset_id}`}, `${fmt(d.n_files)} →`);
       a.addEventListener("click", (e) => e.stopPropagation());
       return a;
     }},
    // In the Columns menu, hidden at first.
    {field: "activity_id", title: "activity", optional: true},
    {field: "institution_id", title: "institution", optional: true},
    {field: "grid_label", title: "grid", optional: true},
    {field: "version", title: "version", optional: true},
    {field: "dataset_id", title: "dataset id", optional: true}
  ];
  const table = new Tabulator($("browse-table"), {
    data: [],
    index: "dataset_id",
    layout: "fitDataStretch",
    height: "60vh",
    renderVertical: "virtual",
    selectableRows: true,
    selectableRowsRangeMode: "click",
    placeholder: "No matching datasets",
    columnDefaults: {resizable: true, headerSort: true},
    initialSort: [{column: "model", dir: "asc"}],
    columns: columnDefs.map(({auto, optional, ...c}) => ({...c, visible: !optional}))
  });
  const built = new Promise((resolve) => table.on("tableBuilt", resolve));
  // One event per user action (also "select all"), with the rows it changed.
  table.on("rowSelectionChanged", (data, rows, selected = [], deselected = []) => {
    if (syncing) return;
    for (const r of selected) state.basket.set(r.getData().dataset_id, r.getData());
    for (const r of deselected) state.basket.delete(r.getData().dataset_id);
    renderBar(); renderGetData();
  });

  // ---- render ----
  let results = [];
  let counts = new Map();

  function compute() {
    const words = searchWords(state.search);
    const searched = words.length ? rows.filter((r) => words.every((w) => r.search_text.includes(w))) : rows;
    const active = ALL_FACETS.filter((f) => state.picks.get(f.key)?.size);
    counts = new Map(ALL_FACETS.map((f) => [f.key, new Map()]));
    const inc = (f, r) => {
      const m = counts.get(f.key);
      for (const v of values(r, f)) m.set(v, (m.get(v) ?? 0) + 1);
    };
    results = [];
    // A facet's counts are the results each value would give with the other
    // facets' picks: a row that fails only facet k counts toward k alone.
    for (const r of searched) {
      let failed = null;
      let fails = 0;
      for (const f of active) {
        const picks = state.picks.get(f.key);
        if (!values(r, f).some((v) => picks.has(v))) {
          failed = f;
          if (++fails > 1) break;
        }
      }
      if (fails === 0) {
        results.push(r);
        for (const f of ALL_FACETS) inc(f, r);
      } else if (fails === 1) {
        inc(failed, r);
      }
    }
  }

  function render() {
    const focusId = document.activeElement?.id;
    compute();
    renderCollections();
    renderChips();
    renderFacets();
    renderTable();
    renderBar();
    renderGetData();
    saveUrl();
    if (focusId && document.activeElement?.id !== focusId) document.getElementById(focusId)?.focus();
  }

  function setPick(key, value, on) {
    const set = state.picks.get(key);
    if (!set) return;
    if (on) set.add(value); else set.delete(value);
    render();
  }

  function renderCollections() {
    const c = counts.get("collection") ?? new Map();
    const picked = state.picks.get("collection");
    const total = [...c.values()].reduce((a, b) => a + b, 0);
    const button = (label, n, active, onclick) => el("button", {
      type: "button", class: `btn btn-sm ${active ? "btn-secondary" : "btn-outline-secondary"}${n === 0 && !active ? " opacity-50" : ""}`,
      "aria-pressed": active ? "true" : "false", onclick
    }, label, " ", el("span", {class: "badge-count"}, fmt(n)));
    put($("browse-collections"), 
      button("All", total, picked.size === 0, () => { picked.clear(); render(); }),
      ...[...known.get("collection")].sort().map((v) => button(collectionLabel(v), c.get(v) ?? 0,
        picked.size === 1 && picked.has(v), () => { picked.clear(); picked.add(v); render(); }))
    );
  }

  function renderChips() {
    const chips = [];
    for (const f of ALL_FACETS) {
      for (const v of state.picks.get(f.key)) {
        chips.push(el("span", {class: "browse-chip"}, `${f.label}: `, el("strong", {}, f.key === "collection" ? collectionLabel(v) : v),
          el("button", {type: "button", class: "browse-chip-x", "aria-label": `Remove ${f.label} ${v}`,
            onclick: () => setPick(f.key, v, false)}, "×")));
      }
    }
    const any = chips.length > 0 || state.search;
    const copy = el("button", {type: "button", class: "btn btn-sm btn-outline-secondary"}, "Copy link to this search");
    copy.addEventListener("click", () => navigator.clipboard.writeText(location.href).then(() => {
      copy.textContent = "Link copied";
      setTimeout(() => (copy.textContent = "Copy link to this search"), 1500);
    }));
    put($("browse-chips"), 
      ...(chips.length ? chips : [el("span", {class: "small text-body-secondary"}, "No filters: showing every dataset.")]),
      any ? el("button", {type: "button", class: "btn btn-sm btn-link", onclick: () => {
        for (const s of state.picks.values()) s.clear();
        state.search = ""; search.value = "";
        render();
      }}, "Clear all") : null,
      copy
    );
    const n = chips.length;
    $("browse-filters-toggle").textContent = n ? `Filters (${n})` : "Filters";
  }

  function facetLabel(f, v) {
    if (f.key !== "variable_id") return null;
    const info = varInfo.get(v);
    if (!info?.long_name) return null;
    return `${info.long_name}${info.units ? ` [${info.units}]` : ""}`;
  }

  function facetMatches(f, v, text) {
    if (!text) return true;
    const info = f.key === "variable_id" ? varInfo.get(v) : null;
    const hay = [v, info?.long_name, info?.standard_name].filter(Boolean).join(" ").replace(/[-_]/g, " ").toLowerCase();
    return searchWords(text).every((w) => hay.includes(w));
  }

  // Stable checkbox ids per value, so focus survives re-sorting.
  const ids = new Map();
  const valueId = (key, v) => {
    const k = `${key}\u0000${v}`;
    if (!ids.has(k)) ids.set(k, `facet-${key}-${ids.size}`);
    return ids.get(k);
  };

  // Facet sections are built once; render() refreshes their lists, so the
  // filter boxes keep focus and text while you type.
  const sections = new Map();
  function section(f) {
    if (sections.has(f.key)) return sections.get(f.key);
    const list = el("ul", {class: "facet-list", id: `facet-${f.key}-list`});
    const box = el("input", {type: "search", class: "form-control form-control-sm facet-filter",
      id: `facet-${f.key}-filter`, placeholder: `Filter ${f.label.toLowerCase()}…`, "aria-label": `Filter ${f.label} values`});
    box.addEventListener("input", () => { state.facetText.set(f.key, box.value); renderFacetList(f); });
    const summaryEl = el("summary", {}, el("span", {class: "facet-title"}, f.label), el("span", {class: "facet-picked"}));
    const details = el("details", {class: "facet", open: !f.more, "data-facet": f.key}, summaryEl, box, list);
    const s = {details, list, box, summaryEl};
    sections.set(f.key, s);
    return s;
  }

  function renderFacetList(f) {
    const s = section(f);
    const c = counts.get(f.key);
    const picked = state.picks.get(f.key);
    const text = state.facetText.get(f.key) ?? "";
    const all = [...known.get(f.key)].sort((a, b) => (c.get(b) ?? 0) - (c.get(a) ?? 0) || a.localeCompare(b));
    const nonzero = all.filter((v) => (c.get(v) ?? 0) > 0);
    s.box.hidden = all.length <= TOP_N;
    let shown;
    if (text) shown = all.filter((v) => facetMatches(f, v, text));
    else if (state.showAll.has(f.key)) shown = all;
    else shown = [...new Set([...all.filter((v) => picked.has(v)), ...nonzero.slice(0, TOP_N)])];
    const items = shown.map((v) => {
      const n = c.get(v) ?? 0;
      const id = valueId(f.key, v);
      const label = facetLabel(f, v);
      return el("li", {class: n === 0 && !picked.has(v) ? "facet-zero" : null},
        el("input", {type: "checkbox", class: "form-check-input", id, checked: picked.has(v),
          onchange: (e) => setPick(f.key, v, e.target.checked)}),
        el("label", {for: id, title: label ?? v},
          el("span", {class: "facet-value"}, v),
          label ? el("span", {class: "facet-desc"}, label) : null),
        el("span", {class: "facet-count"}, fmt(n)));
    });
    const rest = all.length - shown.length;
    if (!text && (rest > 0 || state.showAll.has(f.key)) && all.length > TOP_N) {
      const expanded = state.showAll.has(f.key);
      items.push(el("li", {class: "facet-more"}, el("button", {type: "button", class: "btn btn-link btn-sm p-0",
        onclick: () => { if (expanded) state.showAll.delete(f.key); else state.showAll.add(f.key); renderFacetList(f); }},
        expanded ? "Show fewer" : `Show all ${fmt(all.length)}`)));
    }
    if (text && shown.length === 0) items.push(el("li", {class: "small text-body-secondary"}, "No match"));
    put(s.list, items);
    s.summaryEl.querySelector(".facet-picked").textContent = picked.size ? ` (${picked.size})` : "";
    // Hide a facet with nothing to pick (e.g. CMIP6 facets for reanalysis),
    // never one with picks.
    s.details.hidden = picked.size === 0 && nonzero.length === 0;
    return s;
  }

  const more = el("details", {class: "facet-more-group"}, el("summary", {}, "More filters"));
  function renderFacets() {
    const main = FACETS.filter((f) => !f.more).map((f) => renderFacetList(f).details);
    const extra = FACETS.filter((f) => f.more).map((f) => renderFacetList(f).details);
    if (extra.some((d) => !d.hidden)) {
      more.hidden = false;
      if (more.childElementCount !== extra.length + 1) more.append(...extra);
    } else more.hidden = true;
    const panel = $("browse-facets");
    if (!panel.contains(more)) panel.replaceChildren(...main, more);
    if (extra.some((d) => state.picks.get(d.dataset.facet)?.size)) more.open = true;
  }

  async function renderTable() {
    const shown = state.showBasket ? [...state.basket.values()] : results;
    put($("browse-count"), 
      el("strong", {}, plural(shown.length, "dataset")), " · ",
      plural(shown.reduce((a, d) => a + d.n_files, 0), "file"), " · ",
      size(shown.reduce((a, d) => a + d.size_gb, 0)),
      state.showBasket ? el("span", {class: "ms-2 badge text-bg-light"}, "picked datasets") : null);
    await built;
    syncing = true;
    try {
      await table.replaceData(shown);
      const ids = shown.filter((d) => state.basket.has(d.dataset_id)).map((d) => d.dataset_id);
      if (ids.length) table.selectRow(ids);
      // Columns that are empty for every shown dataset are hidden, unless set
      // in the Columns menu.
      for (const c of columnDefs) {
        if (!c.field || !(c.auto || c.optional)) continue;
        const user = state.columns.get(c.field);
        const visible = user ?? (c.optional ? false : shown.some((d) => d[c.field] != null));
        const col = table.getColumn(c.field);
        if (col && col.isVisible() !== visible) visible ? col.show() : col.hide();
        const box = /** @type {HTMLInputElement|null} */ (document.getElementById(`col-${c.field}`));
        if (box) box.checked = visible;
      }
    } finally {
      syncing = false;
    }
  }

  // Columns menu
  put($("browse-columns-menu"), ...columnDefs.filter((c) => c.auto || c.optional).map((c) => {
    const id = `col-${c.field}`;
    const box = el("input", {type: "checkbox", class: "form-check-input", id, checked: !c.optional});
    box.addEventListener("change", () => { state.columns.set(c.field, box.checked); renderTable(); });
    return el("li", {class: "dropdown-item-text"}, box, " ", el("label", {for: id}, c.title));
  }));
  $("browse-csv").addEventListener("click", () => table.download("csv", "cmip_datasets.csv"));

  function chosen() {
    return state.basket.size ? [...state.basket.values()] : results;
  }

  function renderBar() {
    const n = state.basket.size;
    const ch = chosen();
    const files = ch.reduce((a, d) => a + d.n_files, 0);
    const gb = ch.reduce((a, d) => a + d.size_gb, 0);
    put($("browse-bar"), 
      el("span", {class: "browse-bar-text"},
        n ? [el("strong", {}, plural(n, "dataset")), " picked"] : ["No datasets picked: ", el("strong", {}, "Get data"), ` uses all ${plural(ch.length, "matching dataset")}`],
        `, ${plural(files, "file")}, ${size(gb)}.`),
      el("a", {class: "btn btn-sm btn-whoi", href: "#get-data"}, "Get data ↓"),
      n ? el("button", {type: "button", class: "btn btn-sm btn-outline-light", onclick: () => {
        state.showBasket = !state.showBasket; renderTable(); renderBar();
      }}, state.showBasket ? "Show search results" : "Show picked") : null,
      n ? el("button", {type: "button", class: "btn btn-sm btn-outline-light", onclick: () => {
        state.basket.clear(); state.showBasket = false; table.deselectRow(); render();
      }}, "Clear picks") : null
    );
  }

  // ---- Get data (same output as the OJS page) ----
  const internal = config.audience === "internal";
  const catalogDescriptor = (d) => `${config.catalog_path.replace(/[^/]*$/, "")}whoi_${d.product ?? "cmip6"}.json`;
  const groups = (list, key) => {
    const m = new Map();
    for (const d of list) {
      const k = key(d);
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(d);
    }
    return [...m];
  };
  function pythonSnippet(list) {
    const swap = internal
      ? ""
      : `\n# Outside VAST: read over HTTPS instead of ${config.vast_prefix}\n` +
        `cat.df["path"] = cat.df["path"].str.replace(\n` +
        `    "${config.vast_prefix}", "${config.https_prefix}", regex=False\n)\n`;
    // Known issue (docs/todo.md): a dataset of several files that holds
    // several variables (variable_id "so,thetao,zos") loads them as
    // coordinates, so turn them back into data variables.
    const multi = (rs) => rs.filter((d) => d.n_files > 1 && (d.variable_id ?? "").includes(","));
    const fixMulti = (rs) => {
      const ms = multi(rs);
      if (!ms.length) return "";
      return `
# datasets with several variables load them as coordinates (catalog page:
# "Several variables per file"); make them data variables again
for k, names in {
${ms.map((d) => `    "${d.dataset_id}": ${JSON.stringify(d.variable_id.split(",")).replace(/,/g, ", ")},`).join("\n")}
}.items():
    dsets[k] = dsets[k].reset_coords([n for n in names if n in dsets[k].coords])
`;
    };
    const block = (path, rs, load) => `cat = intake.open_esm_datastore("${path}")
${swap}
keys = [
${rs.map((d) => `    "${d.dataset_id}",`).join("\n")}
]
${load}${fixMulti(rs)}`;
    const gs = groups(list.slice(0, MAX_SNIPPET), catalogDescriptor);
    if (gs.length === 1) {
      const [path, rs] = gs[0];
      return `import intake

${block(path, rs, "dsets = {k: cat[k].to_dask() for k in keys}")}`;
    }
    return `import intake

dsets = {}
${gs.map(([path, rs]) => `
${block(path, rs, "dsets.update({k: cat[k].to_dask() for k in keys})")}`).join("\n")}`;
  }

  const shards = new Map();
  const shard = (s) => {
    if (!shards.has(s)) shards.set(s, fetch(dataUrl(s)).then((r) => r.json()));
    return shards.get(s);
  };
  async function urlList(list) {
    const take = [];
    let n = 0;
    for (const d of list) {
      if (n >= config.wget_max_files) break;
      take.push(d);
      n += d.n_files;
    }
    const lists = Object.assign({}, ...(await Promise.all([...new Set(take.map((d) => d.file_list))].map(shard))));
    const urls = take.flatMap((d) => (lists[d.dataset_id] ?? []).map((f) => `${config.https_prefix}/${d.relative_path}/${f}`));
    return {urls: urls.slice(0, config.wget_max_files), total: list.reduce((a, d) => a + d.n_files, 0)};
  }

  let gdToken = 0;
  async function renderGetData() {
    const token = ++gdToken;
    const list = chosen();
    const n = state.basket.size;
    put($("gd-summary"), 
      `${plural(list.length, "dataset")} ${n ? "picked" : "matching (none picked)"}, `,
      `${plural(list.reduce((a, d) => a + d.n_files, 0), "file")}, `,
      `${size(list.reduce((a, d) => a + d.size_gb, 0))}. See the `,
      el("a", {href: "access.html"}, "access page"), " for which method to use.");
    const capNote = list.length > MAX_SNIPPET
      ? el("p", {}, el("em", {}, `Showing the first ${MAX_SNIPPET} of ${fmt(list.length)} datasets. Narrow the filters or pick rows.`))
      : null;
    put($("gd-python"), capNote ?? "", copyBlock(pythonSnippet(list), "python"),
      isPlaceholder(config.catalog_path) ? el("p", {}, el("em", {}, "The catalog path is not final yet (placeholder).")) : "");
    put($("gd-paths"), ...(internal
      ? [capNote ?? "", copyBlock(list.slice(0, MAX_SNIPPET).map((d) => `${config.vast_prefix}/${d.relative_path}/`).join("\n")),
         el("p", {}, "On HPC these paths work as shown. Over the SMB share, see the ",
           el("a", {href: "access.html"}, "access page"), " for the share name.")]
      : [el("p", {}, "Storage paths are shown only on the internal site.")]));
    const {urls, total} = await urlList(list);
    if (token !== gdToken) return; // a newer render started
    const blob = URL.createObjectURL(new Blob([urls.join("\n") + "\n"], {type: "text/plain"}));
    put($("gd-https"), 
      isPlaceholder(config.https_prefix)
        ? el("p", {}, el("em", {}, "HTTPS downloads are not available yet; the URL prefix below is a placeholder.")) : "",
      total > config.wget_max_files
        ? el("p", {}, el("em", {}, `List capped at ${config.wget_max_files} of ${fmt(total)} files. For large requests use the cluster or SMB share`,
            config.globus_url ? [", or ", el("a", {href: config.globus_url}, "Globus")] : "",
            " (see the ", el("a", {href: "download-guide.html"}, "download guide"), ")."))
        : "",
      el("p", {}, "Save as ", el("code", {}, "urls.txt"), ", then run:"),
      copyBlock("wget --continue --force-directories --no-host-directories -i urls.txt", "bash"),
      el("p", {}, el("a", {href: blob, download: "urls.txt", id: "gd-urls-download"}, el("button", {type: "button"}, "Download urls.txt"))),
      copyBlock(urls.join("\n"))
    );
  }

  function saveUrl() {
    const p = new URLSearchParams();
    for (const f of ALL_FACETS) for (const v of state.picks.get(f.key)) p.append(f.key, v);
    if (state.search) p.set("search", state.search);
    const s = p.toString();
    history.replaceState(history.state, "", `${location.pathname}${s ? `?${s}` : ""}${location.hash}`);
  }

  render();
  $("browse").dataset.ready = "true";
}

function renderIntro(stats, config) {
  put($("browse-intro"), 
    el("p", {class: "mb-1"}, "Catalog ", el("strong", {}, config.catalog_version), ": ",
      `${fmt(stats.n_datasets)} datasets, ${fmt(stats.n_files)} files, ${fmt(stats.size_tb, 1)} TB. `,
      "Search or filter, tick datasets to collect them (picks stay as you search), then ",
      el("a", {href: "#get-data"}, "get data"), ". ",
      "Products not in the catalog are listed on ", el("a", {href: "holdings.html#other-holdings"}, "Other holdings"), "; ",
      "variables are defined on ", el("a", {href: "variables.html"}, "Variables"), ".",
      config.catalog_version === "sample" ? [" ", el("strong", {}, "This is a preview catalog: datasets from a synthetic sample are not real.")] : ""),
    el("p", {class: "small text-body-secondary mb-0"}, "Data sources: ",
      stats.catalogs.map((c, i) => [i ? "; " : "", `${c.name} (${plural(c.n_datasets, "dataset")}`,
        c.synthetic ? [", ", el("strong", {}, "synthetic sample")] : "", ")"]), ".")
  );
}

main().catch((err) => {
  console.error(err);
  put($("browse-intro"), el("div", {class: "alert alert-danger"},
    "The dataset browser could not load: ", String(err?.message ?? err)));
});
