# Lake Tahoe EIP

TRPA's repository for the **Environmental Improvement Program** — data
pipelines, snapshot datasets, and a single-page-app dashboard published
via GitHub Pages.

## What's in this repo

```
EIP/
├── data/                            # snapshot datasets, refreshed nightly
│   ├── simple.json                  # raw simple-locations response
│   ├── detailed.geojson             # raw detailed-locations FeatureCollection
│   └── projects.geojson             # derived dashboard feed (centroids + metadata)
├── scripts/
│   └── Build_EIP_ProjectLocations.py   # ETL: pulls Lake Tahoe Info, writes data/, optional GDB
├── html/                            # single-page-app, served via GitHub Pages
│   ├── index.html                   # landing page (Tools & Dashboards)
│   └── projects-map.html            # EIP Projects Map (ArcGIS Maps SDK + Calcite)
└── .github/workflows/
    └── refresh-data.yml             # nightly refresh of data/ snapshots
```

## Live site

GitHub Pages serves from **`main` branch / `/html`**:

> https://trpa-mason.github.io/EIP/

Move to `https://trpa-agency.github.io/EIP/` once the repo lands under the
agency org.

## Running the data pipeline

The pipeline lives in [`scripts/Build_EIP_ProjectLocations.py`](scripts/Build_EIP_ProjectLocations.py).
It writes three files into `data/`:

| File                       | Source                                                    | Filter |
|----------------------------|-----------------------------------------------------------|--------|
| `data/simple.json`         | `GetProjectSimpleLocationAndGeospatialAssociations` JSON  | none   |
| `data/detailed.geojson`    | `GetProjectDetailedLocationsAsFeatureCollection` GeoJSON  | none   |
| `data/projects.geojson`    | derived from `simple.json` + per-project `GetProject` enrichment (Point FC, dashboard feed) | none   |

When `arcpy` is available (ArcGIS Pro Python environment), the script also
writes four feature classes into `C:\GIS\Scratch.gdb` for a curated subset
of EIP projects — useful for desktop GIS work but not consumed by the SPA.
On a CI runner without `arcpy`, the GDB step is skipped cleanly.

### From the command line

```bash
# ArcGIS Pro Python (writes data/*.json AND scratch GDB feature classes)
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" scripts\Build_EIP_ProjectLocations.py

# Plain Python (data/*.json only — needs `pandas` and `requests`)
python scripts/Build_EIP_ProjectLocations.py
```

A timestamped run log is written to `scripts/Build_EIP_ProjectLocations.log`.

## The dashboard

[`html/projects-map.html`](html/projects-map.html) is a single-file SPA:

- **ArcGIS Maps SDK 4.31** for the map and feature rendering
- **Calcite Components 5.0** for UI primitives
- **EIP brand**: Lexend Deca, EIP Blue / Green / Orange / Navy palette
- **Hybrid data loading**: paints from `data/projects.geojson` first, then
  refreshes silently from the live `laketahoeinfo.org` REST endpoint. If
  CORS blocks the live call (likely), the snapshot is the source of truth
  and the GitHub Action below keeps it current.

### Features

- **EIP Focus Area filter** — every project is categorized into one of
  the four official LakeTahoeInfo Focus Areas, derived from the
  EIP-number prefix (`01.*` → Watersheds and Water Quality, `02.*` →
  Forest Health, `03.*` → Sustainable Recreation and Transportation,
  `04.*` → Science, Stewardship, and Accountability).
- **Rich popup** — each project carries 22 fields fetched from
  LakeTahoeInfo's `GetProject` endpoint at build time: focus area &
  sub-program, stage, description, lead implementer, watershed,
  jurisdiction, timeline (planning → implementation → end), estimated
  cost, threshold categories, action priority, and links to the project
  page and PDF fact sheet.
- **Search** by project name or EIP #.
- **Per-row selection** — checkbox on every list row + "Select visible"
  and "Clear" pills. Selection persists across filter changes so you can
  cherry-pick across categories.
- **Project footprints layer** — toggle in the sidebar lazy-loads
  `data/detailed.geojson` (~6.8 MB) and renders polygons + lines
  alongside the points, colored by Focus Area.
- **Exports** — three formats, all driven from the active selection
  (or the filtered set when nothing is checked):
  - **GeoJSON** — single FeatureCollection. Drop into ArcGIS Pro via
    the JSON To Features GP tool.
  - **Shapefile (.zip)** — splits selection by geometry type into
    separate shapefiles bundled into one zip. Browser-side via
    `shp-write` from CDN. ArcGIS Pro imports natively.
  - **CSV** — point centroids with Latitude / Longitude columns. Import
    into ArcGIS via Display XY Data.
  - **Include polygons & lines** checkbox — when the footprints layer
    is loaded, exports include each selected project's footprint
    geometry alongside its centroid.

### Local preview

```bash
# from the repo root
python -m http.server 8000

# then open
# http://localhost:8000/html/index.html
```

## Nightly data refresh

[`.github/workflows/refresh-data.yml`](.github/workflows/refresh-data.yml)
runs the python script daily at 09:00 UTC, regenerates the three files in
`data/`, and commits any deltas. No manual step needed once it's enabled.

## Data caveats

- **Region / State / Jurisdiction / Watershed** fields exist on the API
  schema but come back null for nearly every project. The dashboard
  doesn't expose them as filters until the upstream API populates them
  (or we layer in a spatial join).
- **Category / Focus Area** is derived deterministically from the
  EIP-number 2-digit prefix and the four official EIP Focus Areas at
  `eip.laketahoeinfo.org/EIPFocusArea`. ~14 projects with non-standard
  numeric IDs (mostly Caltrans/transportation) fall into "Uncategorized".
- **CORS**: the `laketahoeinfo.org/WebServices/...` endpoints may not
  include `Access-Control-Allow-Origin: *`. If the live refresh fails
  in the browser, that's expected — the snapshot keeps the dashboard
  up-to-date via the nightly Action.
