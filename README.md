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
It reads the Lake Tahoe Info API at `internalapi.laketahoeinfo.org` (the anonymous
REST backend behind the re-platformed Project Tracker; no API key) and writes
five files into `data/`:

| File                          | Source                                                                 |
|-------------------------------|------------------------------------------------------------------------|
| `data/simple.json`            | raw `/projects` response, every project in the Tracker                 |
| `data/projects.geojson`       | one Point per project in `/eip-projects/mapped-point/feature-collection`, with the legacy GetProject field names plus `SecuredFunding`, `UnfundedNeed`, `Tags` from `/eip-projects/by-number/{eip}` and funding sources from `/projects/{id}/expenditures` |
| `data/projects_aspatial.json` | the same properties for projects with no point (regional, basin-wide, or unlocated) |
| `data/detailed.geojson`       | `Detail` features from `/projects/{id}/location-as-feature-collection`, dissolved to one feature per project and geometry type |
| `data/detailed_eips.json`     | EIP numbers that have a footprint                                      |

The old `www.laketahoeinfo.org/WebServices/...{key}` routes went away with the
2026 LT Info re-platform (they now return the app shell HTML), which is what
stalled the nightly refresh between July and October 2026.

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
- **Snapshot data**: paints from `data/projects.geojson`; the GitHub Action
  below keeps the snapshot current nightly.

### Features

- **EIP Focus Area filter** — every project is categorized into one of
  the four official LakeTahoeInfo Focus Areas, derived from the
  EIP-number prefix (`01.*` → Watersheds and Water Quality, `02.*` →
  Forest Health, `03.*` → Sustainable Recreation and Transportation,
  `04.*` → Science, Stewardship, and Accountability).
- **Rich popup** — each project carries the fields fetched from the
  Tracker API at build time: focus area, program, action priority, stage,
  description, lead implementer, watershed, jurisdiction, timeline
  (planning → implementation → end), estimated cost, secured funding,
  unfunded need, threshold categories, tags, and links to the project
  page and fact sheet.
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

- **Stages**: the API returns every project, including `Deferred` and
  `Terminated` (about 330 projects the old feed left out). Consumers that
  want the active program should filter on stage.
- **Regional projects** (basin-wide, a state, a jurisdiction) are kept out
  of `projects.geojson` on purpose. The Tracker gives them a centroid of
  the whole area, which reads as a false precise location. They are in
  `projects_aspatial.json` with `ProjectLocationGroup` and
  `ProjectLocationArea` set.
- **Category / Focus Area** comes from the Tracker's taxonomy via the
  per-project detail call; the 38 non-EIP projects have none and fall into
  "Uncategorized".
- **`TMDLPollutantSourceCategory`** is kept in the schema for compatibility
  but the new API does not expose it, so it is always null.
- **Funding fields are self-reported** by implementers. `UnfundedNeed` is
  `EstimatedTotalCost - SecuredFunding` as the Tracker computes it.
