# How we built the EIP Projects Map

A note-to-self capturing the *why*, the *what*, and the *how* of this
dashboard, so the next person (or the next-quarter version of you) can
pick it up cold.

---

## What this is

A single-page-app dashboard at `html/projects-map.html` showing every
project in the **Lake Tahoe Environmental Improvement Program (EIP)**
that has a known location (~657 of ~1,073 projects). Built as part of a
data + scripts + SPA repo on GitHub Pages — no build tools, no
node_modules, just one HTML file, a Python ETL script, and committed
JSON snapshots.

Live URL pattern: `https://<org>.github.io/EIP/html/projects-map.html`

---

## Inspiration

Three reference points shaped this build:

1. **[restoretahoe.org](https://restoretahoe.org/)** — the parent
   public-facing brand site. Confirmed our color palette (Lexend Deca,
   EIP Blue / Navy / Orange / Green plus the Bright Blue
   `#1863DC` CTA color used heavily on the parent site). Inspired
   the four-Focus-Area framing and the "by-the-numbers" pattern we
   may add later.

2. **[LakeTahoeInfo's EIP Project Map](https://eip.laketahoeinfo.org/Results/EipProjectMap)** —
   the system we're complementing (not replacing). Built on Leaflet
   with `L.MakiMarkers.icon()` teardrop pins + `leaflet.markercluster`.
   We mimicked the pin shape (Maki-style SVG inlined as data URIs) so
   users moving between maps see consistent symbology.

3. **TRPA's `Planning/LongRange/` repo** — the in-house template for
   the data + scripts + SPA repo model: `data/`, `scripts/`, `html/`
   side-by-side, with GitHub Pages serving from `/html`.

---

## Repo layout

```
EIP/
├── README.md                              # repo overview + run instructions
├── data/                                  # committed nightly snapshots
│   ├── simple.json                        # raw response from GetProjectSimpleLocations…
│   ├── detailed.geojson                   # raw response from GetProjectDetailedLocations…
│   └── projects.geojson                   # derived dashboard feed (657 features × 27 properties)
├── scripts/
│   ├── Build_EIP_ProjectLocations.py      # ETL: pulls Lake Tahoe Info, derives projects.geojson
│   └── .gitignore                         # excludes the .log only — JSON outputs are committed
├── html/
│   ├── index.html                         # EIP-branded landing page
│   └── projects-map.html                  # the dashboard SPA
├── docs/
│   └── BUILD.md                           # this file
└── .github/workflows/
    └── refresh-data.yml                   # nightly cron → re-runs the script and commits deltas
```

---

## Data pipeline

The Python script makes three Lake Tahoe Info web service calls and
derives one dashboard-ready feed.

| Source endpoint                                                | Output                  | Records |
|---------------------------------------------------------------|-------------------------|---------|
| `GetProjectSimpleLocationAndGeospatialAssociations/JSON/{key}` | `data/simple.json`      | ~1,073  |
| `GetProjectDetailedLocationsAsFeatureCollection/JSON/{key}`    | `data/detailed.geojson` | ~8,506  |
| `GetProject/JSON/{key}/{eip}` *(per-project, parallel)*         | merged into `projects.geojson` | 657 |

`projects.geojson` carries 27 properties per feature — basics
(ProjectID, EIPProjectNumber, ProjectName, geometry) plus 22 enriched
fields from `GetProject`: EIPFocusArea, EIPProgram, EIPActionPriority,
ProjectDescription, Stage, LeadImplementer, ProjectRegion / State /
Jurisdiction / Watershed, PlanningStartDate / ImplementationStartDate /
EndDate, EstimatedTotalCost, ProjectThresholdCategories,
TMDLPollutantSourceCategory, IsEIPProject / IsTransportationProject /
IsLakeClarityProject, ProjectSummaryUrl, ProjectFactSheetUrl.

The script:
- Drops the curated 11-project filter for the JSON outputs (every
  project with a location ends up in the snapshot)
- Derives **EIP Focus Area** from the EIP # 2-digit prefix (`01.*` →
  Watersheds & Water Quality, etc.)
- Parallelizes ~657 `GetProject` calls with 8 workers (≈30s total)
- Gates the legacy ArcGIS Pro / GDB step behind `HAVE_ARCPY` so the
  script runs headless on the GitHub Action runner

`.github/workflows/refresh-data.yml` runs the script at 09:00 UTC daily,
diffs `data/*.json`, and commits any deltas back to `main`.

---

## Tech stack

All from CDN — no bundlers, no `node_modules`. Modeled on TRPA's
canonical **trpa-dashboard-stack** skill.

| Library                          | Version | Purpose                                  |
|----------------------------------|---------|------------------------------------------|
| ArcGIS Maps SDK for JavaScript   | 4.31    | Map view, scene view, layers, popups, widgets |
| Calcite Components               | 5.0     | UI shell, blocks, switches, popovers, actions |
| Lexend Deca (Google Fonts)       | —       | Primary EIP brand typeface              |
| shp-write (`@mapbox/shp-write`)  | 0.3.2   | Lazy-loaded only when user clicks Export Shapefile |

**Dropped from the standard TRPA stack**:
- **AG Grid** — UMD bundle collides with ArcGIS's Dojo AMD loader
  (`multipleDefine` error). We use a vanilla list + checkbox rows
  instead.
- **Plotly.js** — no charts in v1.

**Auth note**: ArcGIS's v2 Basemap Styles API (`arcgis/human-geography`,
etc.) requires an API key. We use the legacy free tier:
- 2D: `gray-vector` (Light Gray Canvas)
- 3D: `topo-vector` (with `ground: "world-elevation"` for terrain)
- BasemapToggle alternate: `hybrid` (imagery + reference labels)

---

## Brand & design

Driven by the **trpa-eip-brand** skill. Quick palette reference:

| Token                 | Hex       | Used for                            |
|-----------------------|-----------|-------------------------------------|
| `--eip-blue`          | `#007DC3` | Primary brand, links, water theme   |
| `--eip-green`         | `#6EBE44` | Forest Health, success, exports     |
| `--eip-orange`        | `#F16022` | Reset, accents, transportation      |
| `--eip-navy`          | `#0B1F41` | Headings, dark surfaces, text       |
| `--eip-bright-blue`   | `#1863DC` | Secondary CTA (parent-site convention) |
| `--eip-page-bg`       | `#F9F9F9` | Page background                     |
| `--eip-body-text`     | `#212121` | Body copy (softer than pure black)  |

**Type**: Lexend Deca Bold (headings), Lexend Deca Regular (body).
Calder Dark (label/section markers — substitutes to Raleway).

**Pin symbology**: Maki-style teardrop SVGs, inlined as data URIs,
colored by Focus Area. Mirrors LakeTahoeInfo's existing convention so
users see consistent markers across both sites.

---

## Features (with implementation notes)

### Map
- **2D ⇄ 3D toggle** — shares the same `Map` instance between MapView
  and SceneView; widgets get rebuilt on swap. SceneView uses
  `goTo({ target: BASIN_EXTENT, tilt: 55 })` to fit the basin
  programmatically, which is robust across viewport sizes.
- **Basemap toggle** — Calcite-rendered widget swaps between
  `gray-vector` (2D) / `topo-vector` (3D) and `hybrid` (aerial).
- **Clustering toggle** — ArcGIS `featureReduction` with `mode`
  aggregation on `Category`, so clusters paint in the dominant Focus
  Area's color.
- **Detailed footprints toggle** — lazy-loads `data/detailed.geojson`
  (~6.8 MB) only when user opts in; renders polygons / lines colored
  by Focus Area beneath the points.
- **TRPA Boundary** — `FeatureLayer` from
  `Boundaries/FeatureServer/4`, drawn as a faint navy outline beneath
  everything.
- **Map widgets** (top-left): Home, Fullscreen, app-wide Reset
  (Calcite reset action), **Map Layers Expand** (the layer toggles
  shuttle in/out of a Calcite Expand on view swap to preserve handlers).
- **Top-right**: Legend (Expand), Print (Expand — service URL still
  TODO).
- **Bottom-right**: BasemapToggle.

### Filters (left panel — Calcite Shell Panel)
- **Primary, always-visible pills** (saturated when active, outlined
  when inactive, navy text on white off-state for AA contrast):
  - **EIP Focus Area** — 4 pills, brand colors per area
  - **Stage** — 4 pills (Planning/Design, Implementation,
    Post-Implementation, Completed), each with a stage color
- **Secondary, collapsed Calcite Blocks** (uniform `#5a6577`
  gray-navy pills):
  - **Project type** — EIP / Transportation / Lake Clarity flags
  - **Program** — 10 sub-program pills derived from data
  - **Jurisdiction** — 7 jurisdiction pills
  - **Watershed** — 59 pills with a search input + scrollable area
- **Reset filters** — rounded EIP-Orange pill at the bottom

Pill text color is computed at build time via `readableTextOn(rgba)`
(YIQ luminance) so saturated swatches always have readable contrast.

### Selection & export (right panel)
- Per-row checkbox selection (persists across filter changes)
- **Show only selected** toggle — flips both list AND map to the
  selection set (preview-your-export mode)
- **Export selection** — green pill that triggers GeoJSON + CSV +
  Shapefile downloads sequentially (shapefile lib loaded on demand
  with `define` masked to dodge AMD/UMD collision)
- **Copy GeoJSON** — outlined blue pill, points-only via
  `navigator.clipboard.writeText`
- **Include polygons & lines** checkbox — adds detailed footprint
  geometry to the export when the footprints layer is loaded

### Rich popup
Custom HTML `content` function on the points layer's `popupTemplate`:
- Focus Area pill + Stage pill (color-coded)
- Project name (heading) + EIP # · Program (sub-meta)
- Project description with **Show more / Show less** toggle
- Fact grid: Lead implementer · Watershed · Jurisdiction · Timeline ·
  Estimated cost · Threshold category · Action priority
- Action buttons: **Project page** (LakeTahoeInfo) +
  **Fact sheet** (eip.laketahoeinfo.org PDF)

### Layout shell
- **Calcite Shell** with `slot="header"` / `slot="panel-start"` /
  default-slot map / `slot="panel-end"`
- **Vertical side tabs** ("FILTERS" / "PROJECTS") at the panel/map
  boundary — click to collapse/expand the Calcite Shell Panel
- Right-tab uses `writing-mode: sideways-lr` so characters face the
  map (reader tilts head left, away from the panel — the GitHub /
  VS Code right-tab convention)

### Header
- EIP Navy bar, EIP Orange accent stripe, Lexend Deca title
- **Snapshot status** indicator (live ping confirms upstream API
  reachability without swapping snapshot data)
- **About** info popover (Calcite Action + Calcite Popover)

---

## Skills that drove the build

Loaded via `Skill` at the start of implementation:

1. **`anthropic-skills:trpa-eip-brand`** — palette, fonts, logo rules,
   relationship to TRPA agency brand
2. **`anthropic-skills:trpa-dashboard-stack`** — required libraries,
   CDN URLs, single-file page template, coding patterns
3. **`anthropic-skills:trpa-data-engineering`** — Python ETL
   conventions, logging, gating arcpy

Used during review passes:

4. **`design:accessibility-review`** — WCAG 2.1 AA audit framework
5. **`design:ux-copy`** — microcopy / button-label review

Pre-existing project memory that overrode general skill guidance:
- **`feedback_trpa_html_constraints`** — flagged the AG Grid /
  ArcGIS AMD loader collision, the `[hidden] { display: none !important }`
  reset requirement, and the LongRange Calcite version pin (the v5
  combo with ArcGIS 4.31 turned out to work fine for new builds —
  the pin was specific to LongRange's older base).

---

## Running locally

```bash
# 1. Refresh the data snapshots (optional — committed snapshots are usually fresh)
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" scripts\Build_EIP_ProjectLocations.py
# Or with plain Python (skips GDB step, only writes data/*.json):
python scripts/Build_EIP_ProjectLocations.py

# 2. Serve the SPA from the repo root
python -m http.server 8000

# 3. Open
# http://localhost:8000/html/projects-map.html
```

The `live-refresh` ping in the dashboard will fail with a CORS error
in the browser console — that's expected. The committed snapshot is
the canonical source; the GitHub Action keeps it fresh.

---

## Deploying

GitHub Pages is configured to serve from `main` branch / `/html`
folder. Push to main and Pages picks it up.

Pages URL pattern:
- Personal fork: `https://trpa-mason.github.io/EIP/html/projects-map.html`
- Once moved to org: `https://trpa-agency.github.io/EIP/html/projects-map.html`

---

## Extending

### Add a new tool / dashboard page
1. Create `html/<new-tool>.html` using the existing
   `projects-map.html` as a template (Calcite Shell + ArcGIS skeleton)
2. Add a card to `html/index.html`'s landing grid
3. Match EIP brand tokens at the top of the file's `:root`

### Add a new filter dimension
1. Add a property to `data/projects.geojson` (modify
   `build_projects_geojson` in the Python script and re-run)
2. Add a `<calcite-block>` in the sidebar's `filters-section`
3. Add a Set to `state` and call `setupPillFilter()` in init
4. Extend `passesFilters()` and `arcgisWhereClause()` to AND-include
   the new dimension

### Add a print layout
The Print widget is wired in but uses a placeholder `printServiceUrl`.
Build a print service in ArcGIS Server with your custom layout, then
update the `PRINT_SERVICE_URL` constant near the top of the script
block in `projects-map.html`.

---

## Known caveats

- **`Region / State / Jurisdiction / Watershed` are null on the
  simple-locations endpoint** — that's why we layer in `GetProject`
  during the snapshot build to populate them.
- **CORS blocks the live-refresh path in the browser** — the
  `laketahoeinfo.org/WebServices/...` endpoints don't include
  `Access-Control-Allow-Origin: *`. Snapshot is canonical; live ping
  only confirms reachability for the status indicator.
- **14 projects have non-standard numeric EIP IDs** (mostly
  Caltrans/transportation) — they fall into "Uncategorized" because
  the prefix-based Focus Area derivation can't classify them.
- **Calcite v2 Basemap Styles need an API key** — we don't use them.
  If we ever want actual Esri Human Geography basemap, set
  `esriConfig.apiKey = "<key>"` and switch to `arcgis/human-geography`.
- **Print service URL is a placeholder** — needs a real ArcGIS Server
  print task before the Print widget actually exports anything.
- **Shapefile export requires `@mapbox/shp-write` from unpkg** —
  loaded on demand only when the user clicks Export. If unpkg is down,
  Shapefile export fails (GeoJSON + CSV still work).
