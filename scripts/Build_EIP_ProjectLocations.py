"""
Build_EIP_ProjectLocations.py
Mason Bindl, Tahoe Regional Planning Agency

Pulls EIP project locations from two Lake Tahoe Info web services and writes:

1. Snapshot files (committed to git, consumed by the SPA at /html/projects-map.html):
       <repo>/data/simple.json         - raw simple-endpoint response (all projects)
       <repo>/data/detailed.geojson    - raw detailed-endpoint response (all projects)
       <repo>/data/projects.geojson    - derived dashboard feed: Point FeatureCollection
                                         with flattened Region/State/Jurisdiction/Watershed
                                         metadata for every project that has a location

2. ArcGIS feature classes in C:\\GIS\\Scratch.gdb (only when arcpy is available;
   filtered to the curated EIP_PROJECTS list for desktop GIS users):
       EIP_Projects_SimpleLocations    (point   - centroid + geospatial associations)
       EIP_Projects_DetailedPolygons   (polygon - project footprints, if any)
       EIP_Projects_DetailedLines      (polyline)
       EIP_Projects_DetailedPoints     (point)

Default interpreter (when run interactively or under Task Scheduler):
    C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe

When run on a CI runner or any non-arcpy environment, the GDB step is skipped
and only the data/*.json outputs are produced.
"""
import json
import logging
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

try:
    import arcpy
    HAVE_ARCPY = True
except ImportError:
    arcpy = None
    HAVE_ARCPY = False

# ------------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = SCRIPT_DIR / "Build_EIP_ProjectLocations.log"
SIMPLE_JSON_PATH = DATA_DIR / "simple.json"
DETAILED_GEOJSON_PATH = DATA_DIR / "detailed.geojson"
PROJECTS_GEOJSON_PATH = DATA_DIR / "projects.geojson"
ASPATIAL_JSON_PATH = DATA_DIR / "projects_aspatial.json"

# ------------------------------------------------------------------------
# Lake Tahoe Info endpoints
# ------------------------------------------------------------------------
API_KEY = "e17aeb86-85e3-4260-83fd-a2b32501c476"
SIMPLE_URL = (
    f"https://www.laketahoeinfo.org/WebServices/"
    f"GetProjectSimpleLocationAndGeospatialAssociations/JSON/{API_KEY}"
)
DETAILED_URL = (
    f"https://www.laketahoeinfo.org/WebServices/"
    f"GetProjectDetailedLocationsAsFeatureCollection/JSON/{API_KEY}"
)
GET_PROJECT_URL = (
    f"https://www.laketahoeinfo.org/WebServices/GetProject/JSON/{API_KEY}/{{eip}}"
)
PROJECT_URL_TEMPLATE = "https://www.laketahoeinfo.org/Project/Detail/{project_id}"

# Fields from GetProject we copy into projects.geojson properties.
# (Lat/Lon/ProjectURL come from the simple endpoint; we don't re-set them.)
GET_PROJECT_FIELDS = [
    "EIPFocusArea",          # canonical focus-area name (e.g. "Watersheds and Water Quality")
    "EIPProgram",            # sub-program (e.g. "Stormwater Management Program")
    "EIPActionPriority",     # human-readable action priority text
    "ProjectDescription",    # long description
    "ProjectThresholdCategories",
    "LeadImplementer",
    "PlanningStartDate",
    "ImplementationStartDate",
    "EndDate",
    "Stage",                 # Planning / Implementation / Completed / etc.
    "ProjectRegion",
    "ProjectState",
    "ProjectJurisdiction",
    "ProjectWatershed",
    "ProjectSummaryUrl",     # https://www.laketahoeinfo.org/Project/Detail/{ID}
    "ProjectFactSheetUrl",   # https://eip.laketahoeinfo.org/Project/FactSheet/{EIP}
    "TMDLPollutantSourceCategory",
    "EstimatedTotalCost",
    "EstimatedAnnualOperatingCost",
    "IsEIPProject",
    "IsTransportationProject",
    "IsLakeClarityProject",
]

# ------------------------------------------------------------------------
# GDB output (arcpy-only)
# ------------------------------------------------------------------------
GDB_FOLDER = r"C:\GIS"
GDB_NAME = "Scratch.gdb"
GDB = os.path.join(GDB_FOLDER, GDB_NAME)

FC_SIMPLE = "EIP_Projects_SimpleLocations"
FC_DETAIL_POLY = "EIP_Projects_DetailedPolygons"
FC_DETAIL_LINE = "EIP_Projects_DetailedLines"
FC_DETAIL_POINT = "EIP_Projects_DetailedPoints"

# ------------------------------------------------------------------------
# Curated subset (used only by the GDB feature classes)
# ------------------------------------------------------------------------
EIP_PROJECTS = [
    # Forest Health
    {"eip": "02.02.02.0015", "category": "Forest Health",
     "name": "Nevada Understory Burning Program"},
    {"eip": "02.01.01.0025", "category": "Forest Health",
     "name": "Nevada Urban Lot and Forest Enhancement"},
    {"eip": "02.01.01.0163", "category": "Forest Health",
     "name": "Tunnel Creek Hazardous Fuels Reduction"},
    {"eip": "02.01.01.0154", "category": "Forest Health",
     "name": "North Lake Tahoe Division: CWPP Implementation and Completion Project"},
    {"eip": "02.01.01.0124", "category": "Forest Health",
     "name": "NLTFPD Urban Defense Zone Hazardous Fuels Reduction"},
    # Watershed Restoration and Water Quality
    {"eip": "01.02.01.00.70", "category": "Watershed Restoration and Water Quality",
     "name": "Upper Truckee River Johnson Meadow Restoration Project"},
    {"eip": "01.01.01.0216", "category": "Watershed Restoration and Water Quality",
     "name": "Stormwater Management Tools for the Lake Tahoe Basin"},
    {"eip": "01.01.01.0045", "category": "Watershed Restoration and Water Quality",
     "name": "Kings Beach Watershed Improvement Project (Implementation)"},
    {"eip": "01.01.01.0194", "category": "Watershed Restoration and Water Quality",
     "name": "North Tahoe Recreational Access WQ Improvements (Implementation)"},
    {"eip": "01.01.01.0221", "category": "Watershed Restoration and Water Quality",
     "name": "Areawide Assessments and Drainage Master Planning"},
    # Other
    {"eip": "03.01.02.0122", "category": "Other",
     "name": "Emerald Bay Gateway Project (Planning and Environmental Documentation)"},
]

EIP_AMBIGUOUS = {
    "01.02.01.00.70": ["01.02.01.00.70", "01.02.01.0070"],
}

SKIPPED_PROJECTS = [
    "TRPA Sustainable Recreation Threshold Update (EIP = 'Various')",
]

# ------------------------------------------------------------------------
# EIP Focus Areas (sourced from eip.laketahoeinfo.org/EIPFocusArea)
# Every EIP project number begins with a 2-digit Focus Area prefix.
# ------------------------------------------------------------------------
EIP_FOCUS_AREAS = {
    "01": "01 - Watersheds and Water Quality",
    "02": "02 - Forest Health",
    "03": "03 - Sustainable Recreation and Transportation",
    "04": "04 - Science, Stewardship, and Accountability",
}


def focus_area_from_eip(eip_number: str) -> str:
    """Map an EIP project number to its Focus Area label by 2-digit prefix.
    Returns '' for unrecognized prefixes (so the dashboard groups them
    under 'Uncategorized')."""
    if not eip_number:
        return ""
    prefix = str(eip_number).strip()[:2]
    return EIP_FOCUS_AREAS.get(prefix, "")


# ------------------------------------------------------------------------
# GetProject enrichment — pulls per-project rich fields used by the popup
# ------------------------------------------------------------------------
def _eip_lookup_candidates(eip: str):
    """Return EIP # variants to try when GetProject returns 404.
    The simple endpoint sometimes uses '01.02.01.00.70' but GetProject
    only accepts the canonical '01.02.01.0070'."""
    candidates = [eip]
    parts = eip.split(".")
    if len(parts) == 5:
        # Try collapsing the last two segments into one (zero-padded to 4)
        try:
            tail = parts[-2] + parts[-1]
            candidates.append(".".join(parts[:-2] + [tail.zfill(4)]))
        except Exception:
            pass
    return candidates


def fetch_project_details(eip: str, session: requests.Session, timeout: int = 15):
    """Return GetProject record for one EIP # (dict) or None on failure."""
    for candidate in _eip_lookup_candidates(eip):
        url = GET_PROJECT_URL.format(eip=candidate)
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                if not data:
                    continue
                return data[0]
            return data
        except (requests.RequestException, ValueError):
            continue
    return None


def fetch_all_project_details(eip_numbers, max_workers: int = 8):
    """Parallel-fetch GetProject for every EIP #. Returns dict EIP -> record.
    EIPs that 404 / time out are silently skipped (caller logs the count)."""
    out = {}
    if not eip_numbers:
        return out
    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(fetch_project_details, eip, session): eip
                for eip in eip_numbers
            }
            for fut in as_completed(futures):
                eip = futures[fut]
                rec = fut.result()
                if rec is not None:
                    out[eip] = rec
    return out


def project_url_for(rec: dict, pid_str: str) -> str:
    """Prefer ProjectSummaryUrl from GetProject, fall back to the templated URL."""
    if rec:
        url = rec.get("ProjectSummaryUrl")
        if url:
            return url
    return PROJECT_URL_TEMPLATE.format(project_id=pid_str) if pid_str else ""

# ------------------------------------------------------------------------
# API schema candidates (resilient to future key renames)
# ------------------------------------------------------------------------
PROJECT_ID_KEYS = ["ProjectID", "projectID", "ProjectId", "ID", "Id"]
PROJECT_NUMBER_KEYS = ["EIPProjectNumber", "ProjectNumber", "projectNumber"]
PROJECT_NAME_KEYS = ["ProjectName", "projectName", "Name"]
LATITUDE_KEYS = ["Latitude", "latitude", "Lat", "Y"]
LONGITUDE_KEYS = ["Longitude", "longitude", "Lon", "Long", "X"]
NO_LOCATION_KEYS = ["NoLocation", "noLocation"]
ASSOCIATION_KEYS = {
    "Region": ["Region", "Regions"],
    "State": ["State"],
    "Jurisdiction": ["Jurisdiction", "Jurisdictions"],
    "Watershed": ["Watershed", "Watersheds"],
}

log = logging.getLogger("Build_EIP_ProjectLocations")


# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------
def setup_logging():
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(console)
    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)


def ensure_gdb():
    if arcpy.Exists(GDB):
        log.info(f"GDB exists: {GDB}")
    else:
        if not os.path.isdir(GDB_FOLDER):
            os.makedirs(GDB_FOLDER, exist_ok=True)
            log.info(f"Created folder: {GDB_FOLDER}")
        arcpy.management.CreateFileGDB(GDB_FOLDER, GDB_NAME)
        log.info(f"Created GDB: {GDB}")
    arcpy.env.overwriteOutput = True


def pick_key(d, candidates):
    """Return the first candidate key that appears in dict-like d, else None."""
    for k in candidates:
        if k in d:
            return k
    return None


def flatten_association(value):
    """Flatten a nested association (list of dicts / list of strings) into
    a pipe-delimited string. Returns '' for missing."""
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("Name") or item.get("name") or item.get("DisplayName")
                if name:
                    parts.append(str(name))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "|".join(parts)
    if isinstance(value, dict):
        name = value.get("Name") or value.get("name") or value.get("DisplayName")
        return str(name) if name else json.dumps(value, ensure_ascii=False)
    return str(value)


def expected_eip_set():
    """Return a set of all EIP candidate strings we want to match against the API."""
    s = set()
    for p in EIP_PROJECTS:
        eip = p["eip"]
        if eip in EIP_AMBIGUOUS:
            for c in EIP_AMBIGUOUS[eip]:
                s.add(c)
        else:
            s.add(eip)
    return s


def curated_category_lookup():
    """Map every EIP candidate (including ambiguous variants) to its category."""
    lookup = {p["eip"]: p["category"] for p in EIP_PROJECTS}
    for eip, candidates in EIP_AMBIGUOUS.items():
        cat = lookup.get(eip, "")
        for c in candidates:
            lookup.setdefault(c, cat)
    return lookup


# ------------------------------------------------------------------------
# Simple endpoint
# ------------------------------------------------------------------------
def fetch_simple():
    log.info(f"Fetching simple locations: {SIMPLE_URL}")
    resp = requests.get(SIMPLE_URL, timeout=60)
    resp.raise_for_status()
    SIMPLE_JSON_PATH.write_text(resp.text, encoding="utf-8")
    log.info(f"Wrote raw JSON to {SIMPLE_JSON_PATH} ({len(resp.text):,} bytes)")

    data = resp.json()
    if isinstance(data, dict):
        for k in ("Results", "results", "Projects", "projects", "data"):
            if k in data and isinstance(data[k], list):
                data = data[k]
                break
    if not isinstance(data, list):
        log.warning(f"Unexpected simple-response shape (type={type(data).__name__}); "
                    "returning empty frame")
        return pd.DataFrame()

    df = pd.DataFrame(data)
    if df.empty:
        log.warning("Simple endpoint returned zero rows")
        return df
    log.info(f"Simple endpoint: {len(df):,} rows, columns={list(df.columns)}")
    log.info(f"First row sample: "
             f"{json.dumps(df.head(1).to_dict(orient='records')[0], default=str)[:1200]}")
    return df


def filter_simple(df):
    if df.empty:
        return df, []
    pn_key = pick_key(df.columns, PROJECT_NUMBER_KEYS)
    if pn_key is None:
        log.error(f"Could not find project-number column in: {list(df.columns)}")
        return df.iloc[0:0], [p["eip"] for p in EIP_PROJECTS]

    wanted = expected_eip_set()
    df[pn_key] = df[pn_key].astype(str)
    matched = df[df[pn_key].isin(wanted)].copy()

    got = set(matched[pn_key].tolist())
    missing = []
    for p in EIP_PROJECTS:
        eip = p["eip"]
        candidates = EIP_AMBIGUOUS.get(eip, [eip])
        hit = next((c for c in candidates if c in got), None)
        if hit is None:
            missing.append(p["eip"])
        elif eip in EIP_AMBIGUOUS and hit != eip:
            log.warning(f"Ambiguous EIP {eip!r} matched API as {hit!r} -- treat {hit!r} "
                        "as canonical for future runs")

    log.info(f"Curated filter: matched {len(matched)} of {len(EIP_PROJECTS)} expected; "
             f"missing={missing}")
    return matched, missing


def build_points_fc(df_matched):
    """Write the curated-subset point feature class to the scratch GDB. arcpy-only."""
    if df_matched.empty:
        log.warning("No matched simple records -- skipping simple point FC")
        return

    pn_key = pick_key(df_matched.columns, PROJECT_NUMBER_KEYS)
    name_key = pick_key(df_matched.columns, PROJECT_NAME_KEYS)
    lat_key = pick_key(df_matched.columns, LATITUDE_KEYS)
    lon_key = pick_key(df_matched.columns, LONGITUDE_KEYS)
    if lat_key is None or lon_key is None:
        log.error(f"Missing lat/long column; cannot build {FC_SIMPLE}. "
                  f"Columns: {list(df_matched.columns)}")
        return

    cat_lookup = curated_category_lookup()

    rows = []
    for _, r in df_matched.iterrows():
        pn = str(r.get(pn_key, ""))
        try:
            lat = float(r[lat_key])
            lon = float(r[lon_key])
        except (TypeError, ValueError):
            log.warning(f"Skipping {pn}: non-numeric lat/long ({r.get(lat_key)}, "
                        f"{r.get(lon_key)})")
            continue

        assoc_flat = {}
        for field, keys in ASSOCIATION_KEYS.items():
            k = pick_key(r.index, keys)
            assoc_flat[field] = flatten_association(r.get(k) if k else None)

        assoc_json = json.dumps(
            {k: (v if not isinstance(v, (pd.Series,)) else v.tolist())
             for k, v in r.items()},
            default=str, ensure_ascii=False,
        )
        if len(assoc_json) > 9900:
            assoc_json = assoc_json[:9900] + "...TRUNCATED"

        rows.append({
            "EIPProjectNumber": pn[:20],
            "ProjectName": str(r.get(name_key, ""))[:255] if name_key else "",
            "Category": cat_lookup.get(pn, "")[:50],
            "Latitude": lat,
            "Longitude": lon,
            "Region": assoc_flat.get("Region", "")[:255],
            "State": assoc_flat.get("State", "")[:100],
            "Jurisdiction": assoc_flat.get("Jurisdiction", "")[:255],
            "Watershed": assoc_flat.get("Watershed", "")[:255],
            "GeospatialAssociationsJSON": assoc_json,
        })

    out_fc = os.path.join(GDB, FC_SIMPLE)
    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)
    sr = arcpy.SpatialReference(4326)
    arcpy.management.CreateFeatureclass(
        out_path=GDB, out_name=FC_SIMPLE, geometry_type="POINT",
        spatial_reference=sr,
    )
    schema = [
        ("EIPProjectNumber", "TEXT", 20),
        ("ProjectName", "TEXT", 255),
        ("Category", "TEXT", 50),
        ("Latitude", "DOUBLE", None),
        ("Longitude", "DOUBLE", None),
        ("Region", "TEXT", 255),
        ("State", "TEXT", 100),
        ("Jurisdiction", "TEXT", 255),
        ("Watershed", "TEXT", 255),
        ("GeospatialAssociationsJSON", "TEXT", 10000),
    ]
    for fname, ftype, flen in schema:
        if ftype == "TEXT":
            arcpy.management.AddField(out_fc, fname, ftype, field_length=flen)
        else:
            arcpy.management.AddField(out_fc, fname, ftype)

    cursor_fields = ["SHAPE@XY"] + [s[0] for s in schema]
    with arcpy.da.InsertCursor(out_fc, cursor_fields) as ic:
        for row in rows:
            ic.insertRow([
                (row["Longitude"], row["Latitude"]),
                row["EIPProjectNumber"],
                row["ProjectName"],
                row["Category"],
                row["Latitude"],
                row["Longitude"],
                row["Region"],
                row["State"],
                row["Jurisdiction"],
                row["Watershed"],
                row["GeospatialAssociationsJSON"],
            ])
    log.info(f"Wrote {len(rows):,} points to {out_fc}")


# ------------------------------------------------------------------------
# Detailed endpoint
# ------------------------------------------------------------------------
def fetch_detailed():
    log.info(f"Fetching detailed locations: {DETAILED_URL}")
    resp = requests.get(DETAILED_URL, timeout=120)
    resp.raise_for_status()
    DETAILED_GEOJSON_PATH.write_text(resp.text, encoding="utf-8")
    log.info(f"Wrote raw GeoJSON to {DETAILED_GEOJSON_PATH} ({len(resp.text):,} bytes)")

    fc = resp.json()
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        log.warning(f"Detailed endpoint did not return a GeoJSON FeatureCollection "
                    f"(got type={fc.get('type') if isinstance(fc, dict) else type(fc).__name__})")
        return None
    features = fc.get("features") or []
    log.info(f"Detailed endpoint: {len(features):,} features fetched")
    return fc


def filter_detailed(fc):
    if fc is None or not fc.get("features"):
        return None, []
    wanted = expected_eip_set()

    pn_key = None
    for f in fc["features"]:
        props = f.get("properties") or {}
        k = pick_key(props, PROJECT_NUMBER_KEYS)
        if k is not None:
            pn_key = k
            break
    if pn_key is None:
        log.error("Could not find project-number property on any detailed feature; "
                  "cannot filter")
        return None, [p["eip"] for p in EIP_PROJECTS]
    log.info(f"Detailed features use property key {pn_key!r} for EIP #")

    filtered = []
    for f in fc["features"]:
        props = f.get("properties") or {}
        pn = str(props.get(pn_key, ""))
        if pn in wanted:
            filtered.append(f)

    got = {str((f.get("properties") or {}).get(pn_key, "")) for f in filtered}
    missing = []
    for p in EIP_PROJECTS:
        candidates = EIP_AMBIGUOUS.get(p["eip"], [p["eip"]])
        if not any(c in got for c in candidates):
            missing.append(p["eip"])

    hist = {}
    for f in filtered:
        g = f.get("geometry") or {}
        t = g.get("type", "None")
        hist[t] = hist.get(t, 0) + 1
    log.info(f"Detailed filter: matched {len(filtered)} features "
             f"(by EIP match, multiple features per project are expected); "
             f"missing EIPs={missing}; geometry histogram={hist}")

    out = {"type": "FeatureCollection", "features": filtered}
    return out, missing


POLY_TYPES = {"Polygon", "MultiPolygon"}
LINE_TYPES = {"LineString", "MultiLineString"}
POINT_TYPES = {"Point", "MultiPoint"}


def _split_by_geom(filtered_fc):
    polys, lines, points, other = [], [], [], []
    for f in filtered_fc["features"]:
        t = (f.get("geometry") or {}).get("type")
        if t in POLY_TYPES:
            polys.append(f)
        elif t in LINE_TYPES:
            lines.append(f)
        elif t in POINT_TYPES:
            points.append(f)
        else:
            other.append(t)
    if other:
        log.warning(f"Skipped {len(other)} features with unsupported geometry types: "
                    f"{sorted(set(other))}")
    return polys, lines, points


def _geojson_to_fc(features, geom_type, fc_name):
    """Write a bucket of GeoJSON features to a temp file and convert to a gdb FC."""
    if not features:
        log.info(f"No {geom_type} features -- skipping {fc_name}")
        return
    out_fc = os.path.join(GDB, fc_name)
    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".geojson", delete=False,
                                      encoding="utf-8")
    try:
        json.dump({"type": "FeatureCollection", "features": features}, tmp,
                  ensure_ascii=False)
        tmp.close()
        try:
            arcpy.conversion.JSONToFeatures(tmp.name, out_fc, geometry_type=geom_type)
            count = int(arcpy.management.GetCount(out_fc).getOutput(0))
            log.info(f"JSONToFeatures -> {out_fc} ({count:,} features)")
        except Exception as primary_err:
            log.warning(f"JSONToFeatures failed for {fc_name} ({primary_err}); "
                        "using geopandas GPKG fallback")
            try:
                import geopandas as gpd
                gdf = gpd.read_file(tmp.name)
                gpkg = tmp.name.replace(".geojson", ".gpkg")
                gdf.to_file(gpkg, layer=fc_name, driver="GPKG")
                src = os.path.join(gpkg, fc_name)
                arcpy.conversion.ExportFeatures(src, out_fc)
                count = int(arcpy.management.GetCount(out_fc).getOutput(0))
                log.info(f"GPKG fallback -> {out_fc} ({count:,} features)")
            except Exception:
                log.exception(f"Fallback path also failed for {fc_name}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def build_detailed_fcs(filtered_fc):
    if filtered_fc is None or not filtered_fc.get("features"):
        log.warning("No filtered detailed features -- skipping detailed FCs")
        return
    polys, lines, points = _split_by_geom(filtered_fc)
    _geojson_to_fc(polys, "POLYGON", FC_DETAIL_POLY)
    _geojson_to_fc(lines, "POLYLINE", FC_DETAIL_LINE)
    _geojson_to_fc(points, "POINT", FC_DETAIL_POINT)


# ------------------------------------------------------------------------
# Dashboard feed: derive projects.geojson from the unfiltered simple frame
# ------------------------------------------------------------------------
def build_projects_geojson(df_simple):
    """Derive a Point FeatureCollection covering EVERY project that has a location.

    Each feature carries:
        - identity: ProjectID, EIPProjectNumber, ProjectName
        - filtering: Category (Focus Area derived from prefix)
        - rich popup fields fetched from GetProject (see GET_PROJECT_FIELDS)
        - convenience links: ProjectURL, ProjectFactSheetUrl
    """
    if df_simple.empty:
        log.warning("Simple frame is empty -- skipping projects.geojson")
        return

    pn_key = pick_key(df_simple.columns, PROJECT_NUMBER_KEYS)
    pid_key = pick_key(df_simple.columns, PROJECT_ID_KEYS)
    name_key = pick_key(df_simple.columns, PROJECT_NAME_KEYS)
    lat_key = pick_key(df_simple.columns, LATITUDE_KEYS)
    lon_key = pick_key(df_simple.columns, LONGITUDE_KEYS)
    nl_key = pick_key(df_simple.columns, NO_LOCATION_KEYS)

    if lat_key is None or lon_key is None or pn_key is None:
        log.error("Missing required columns for projects.geojson "
                  f"(pn_key={pn_key}, lat_key={lat_key}, lon_key={lon_key})")
        return

    # Pass 1 — split records into spatial (have valid coords) and aspatial
    # (NoLocation flag set, or coords missing/invalid). Aspatial records still
    # get included in projects_aspatial.json so they show up in CSV exports.
    spatial = []     # list of (row, lat, lon)
    aspatial = []    # list of row
    for _, r in df_simple.iterrows():
        if nl_key and bool(r.get(nl_key)):
            aspatial.append(r)
            continue
        try:
            lat = float(r[lat_key])
            lon = float(r[lon_key])
        except (TypeError, ValueError):
            aspatial.append(r)
            continue
        if pd.isna(lat) or pd.isna(lon):
            aspatial.append(r)
            continue
        spatial.append((r, lat, lon))

    log.info(f"Records split: {len(spatial):,} spatial · {len(aspatial):,} aspatial "
             f"(basin-wide / no specific location)")

    # Pass 2 — parallel-fetch GetProject for ALL projects (one batch)
    all_eips = sorted({str(r.get(pn_key, "")).strip() for r in
                       (list(r for r, _, _ in spatial) + aspatial)
                       if r.get(pn_key)})
    t0 = time.time()
    log.info(f"Fetching GetProject for {len(all_eips)} EIP numbers...")
    details = fetch_all_project_details(all_eips, max_workers=8)
    log.info(f"GetProject: {len(details):,}/{len(all_eips):,} returned data "
             f"({time.time() - t0:.1f}s)")

    def build_props(r, rec):
        pn = str(r.get(pn_key, "")).strip()
        pid = r.get(pid_key) if pid_key else None
        pid_str = str(pid).strip() if pid is not None and not pd.isna(pid) else ""
        props = {
            "ProjectID": pid_str,
            "EIPProjectNumber": pn,
            "ProjectName": str(r.get(name_key, "")) if name_key else "",
            "Category": focus_area_from_eip(pn),
            "ProjectURL": project_url_for(rec, pid_str),
        }
        # Merge in the rich GetProject fields. Use None for missing so
        # downstream consumers (popup, exports) have a consistent schema.
        for field in GET_PROJECT_FIELDS:
            val = rec.get(field) if rec else None
            if isinstance(val, float) and (val != val):   # NaN
                val = None
            props[field] = val
        return props

    # Pass 3 — projects.geojson (spatial features for the map)
    features = []
    for r, lat, lon in spatial:
        rec = details.get(str(r.get(pn_key, "")).strip())
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": build_props(r, rec),
        })
    PROJECTS_GEOJSON_PATH.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    enriched_spatial = sum(1 for f in features if f["properties"].get("Stage") is not None)
    log.info(
        f"Wrote {len(features):,} features to {PROJECTS_GEOJSON_PATH} "
        f"({enriched_spatial:,} enriched with GetProject data)"
    )

    # Pass 4 — projects_aspatial.json (no-location records, for CSV exports)
    aspatial_records = []
    for r in aspatial:
        rec = details.get(str(r.get(pn_key, "")).strip())
        aspatial_records.append(build_props(r, rec))
    ASPATIAL_JSON_PATH.write_text(
        json.dumps(aspatial_records, ensure_ascii=False),
        encoding="utf-8",
    )
    enriched_aspatial = sum(1 for p in aspatial_records if p.get("Stage") is not None)
    log.info(
        f"Wrote {len(aspatial_records):,} records to {ASPATIAL_JSON_PATH} "
        f"({enriched_aspatial:,} enriched with GetProject data)"
    )


# ------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------
def main():
    setup_logging()
    t0 = time.time()
    log.info("=" * 70)
    log.info(f"Build_EIP_ProjectLocations starting (arcpy={'yes' if HAVE_ARCPY else 'no'})")
    log.info(f"Curated subset: {len(EIP_PROJECTS)} EIP projects (used by GDB only)")
    for s in SKIPPED_PROJECTS:
        log.info(f"Skipped by design (no EIP#): {s}")

    try:
        # 1. Always: fetch simple, write data/simple.json, derive data/projects.geojson
        df_simple = fetch_simple()
        build_projects_geojson(df_simple)

        # 2. Always: fetch detailed, write data/detailed.geojson (raw, all projects)
        fc = fetch_detailed()

        # 3. arcpy-only: build curated GDB feature classes
        if HAVE_ARCPY:
            ensure_gdb()
            df_matched, simple_missing = filter_simple(df_simple)
            build_points_fc(df_matched)
            filtered, detailed_missing = filter_detailed(fc) if fc else (None, [])
            build_detailed_fcs(filtered)
        else:
            log.info("arcpy unavailable -- skipping GDB feature class build")
            simple_missing = []
            detailed_missing = []

        log.info("-" * 70)
        log.info("Summary")
        log.info(f"  Simple FC missing EIPs : {simple_missing}")
        log.info(f"  Detailed missing EIPs  : {detailed_missing}")
        log.info(f"  Elapsed                : {time.time() - t0:.1f}s")
        log.info("Done.")
    except Exception:
        log.exception("Pipeline failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
