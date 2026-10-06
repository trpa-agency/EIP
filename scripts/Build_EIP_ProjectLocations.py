"""
Build_EIP_ProjectLocations.py
Mason Bindl, Tahoe Regional Planning Agency

Pulls EIP project data from the Lake Tahoe Info API (internalapi.laketahoeinfo.org,
the anonymous REST backend behind eip.laketahoeinfo.org) and writes:

1. Snapshot files (committed to git, consumed by the SPA at /html/projects-map.html
   and by the tools in trpa-agency/maps):
       <repo>/data/simple.json           - raw /projects response (all projects)
       <repo>/data/detailed.geojson      - project footprints, dissolved per project
       <repo>/data/detailed_eips.json    - EIP #s that have a footprint
       <repo>/data/projects.geojson      - dashboard feed: one Point per project with a
                                           map location, legacy GetProject field names plus
                                           SecuredFunding / UnfundedNeed / Tags
       <repo>/data/projects_aspatial.json - same properties for projects with no point
                                           (regional / basin-wide / unlocated)

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
DETAILED_EIPS_PATH = DATA_DIR / "detailed_eips.json"   # tiny array of EIP # that have polygons/lines/points (used by the dashboard to classify list rows)

# ------------------------------------------------------------------------
# Lake Tahoe Info endpoints
#
# LT Info was re-platformed in 2026: the Angular front end at
# eip.laketahoeinfo.org talks to an anonymous REST API at
# internalapi.laketahoeinfo.org, and the old /WebServices/...{API_KEY}
# routes now return the app shell HTML. No key is needed for the GET
# routes below (everything the public Tracker pages show).
# ------------------------------------------------------------------------
API_BASE = os.environ.get("LTINFO_API_BASE", "https://internalapi.laketahoeinfo.org").rstrip("/")
PROJECTS_URL = f"{API_BASE}/projects"                                        # every project, core fields
MAPPED_POINTS_URL = f"{API_BASE}/eip-projects/mapped-point/feature-collection"  # one point per project with a map location
REGIONAL_URL = f"{API_BASE}/eip-projects/regional/feature-collection"       # basin-wide / state / jurisdiction projects
PROJECT_DETAIL_URL = f"{API_BASE}/eip-projects/by-number/{{eip}}"           # focus area, program, action priority, thresholds, funding
EXPENDITURES_URL = f"{API_BASE}/projects/{{pid}}/expenditures"              # per-year rows by funding source
LOCATION_FC_URL = f"{API_BASE}/projects/{{pid}}/location-as-feature-collection"  # simple + detailed geometry for one project
FACT_SHEET_URL_TEMPLATE = "https://eip.laketahoeinfo.org/projects/fact-sheet/{eip}"
PROJECT_URL_TEMPLATE = "https://laketahoeinfo.org/projects/{project_id}"   # LT Info project detail page
REQUEST_WORKERS = 8                               # parallel per-project calls (polite to LT Info)

# Fields written to every projects.geojson / projects_aspatial.json record.
# Names are the legacy GetProject names so the dashboard, the locator, and the
# Funding Priorities tool keep working; NEW_FIELDS extend the schema with what
# the new API adds (secured funding and unfunded need were not available before).
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
NEW_FIELDS = [
    "SecuredFunding",        # dollars the implementer reports as secured
    "UnfundedNeed",          # EstimatedTotalCost - SecuredFunding, as the Tracker computes it
    "Tags",                  # "; "-joined project tags (e.g. "Climate Resilience; LTRA")
    "ProjectLocationGroup",  # regional projects: Region / State / Jurisdiction
    "ProjectLocationArea",   # regional projects: Basin-wide, California, Placer County, CA, ...
    "LastModificationDate",  # ISO timestamp from the Tracker
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
# Per-project enrichment against the new API
# ------------------------------------------------------------------------
def _get_json(url: str, session: requests.Session, timeout: int = 20, retries: int = 2):
    """GET a JSON document. Returns None on 404 or after `retries` failures,
    so one bad project never fails the nightly run."""
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == retries:
                log.warning(f"GET failed after {retries + 1} tries: {url} ({e})")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def _fetch_many(keys, url_for, max_workers: int = REQUEST_WORKERS, label: str = ""):
    """Parallel GET for a list of keys. Returns {key: json} for the ones that
    came back; failures are logged by _get_json and skipped."""
    out = {}
    keys = [k for k in keys if k is not None]
    if not keys:
        return out
    t0 = time.time()
    with requests.Session() as session:
        session.headers["User-Agent"] = "TRPA-EIP-snapshot/2.0 (+https://github.com/trpa-agency/EIP)"
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_get_json, url_for(k), session): k for k in keys}
            for fut in as_completed(futures):
                data = fut.result()
                if data is not None:
                    out[futures[fut]] = data
    log.info(f"{label or 'fetch'}: {len(out):,}/{len(keys):,} returned data ({time.time() - t0:.1f}s)")
    return out


def fetch_all_project_details(eip_numbers):
    """eip-projects/by-number for every project number -> {eip: record}.
    The record carries focus area, program, action priority, threshold
    categories, estimated cost, secured funding, unfunded need, and the
    reported expenditure total."""
    return _fetch_many(sorted(set(eip_numbers)), lambda e: PROJECT_DETAIL_URL.format(eip=e), label="Project details")


def fetch_all_project_expenditures(project_ids):
    """projects/{id}/expenditures for every project id -> {id: rows}. Each
    row is one funding source in one calendar year (DisplayName,
    SectorName, ExpenditureAmount)."""
    return _fetch_many(sorted(set(project_ids)), lambda i: EXPENDITURES_URL.format(pid=i), label="Expenditures")


def summarize_expenditures(rows):
    """Collapse per-year expenditure rows into two filterable props.
    FundingSources is semicolon-joined (source names contain commas,
    e.g. 'Proposition 1 (CTC)' vs 'California Tahoe Conservancy, Prop 68').
    Sources are listed even when every row for them is $0 (a committed
    source that has not spent yet is still a partner on the project)."""
    if not rows:
        return {"FundingSources": None, "TotalExpenditure": None}
    sources = sorted({
        str(r.get("DisplayName") or r.get("FundingSource") or "").strip()
        for r in rows
        if (r.get("DisplayName") or r.get("FundingSource"))
    })
    total = 0.0
    any_amount = False
    for r in rows:
        v = r.get("ExpenditureAmount", r.get("Expenditure"))
        if v is None:
            continue
        try:
            total += float(v)
            any_amount = True
        except (TypeError, ValueError):
            continue
    return {
        "FundingSources": "; ".join(s for s in sources if s) or None,
        "TotalExpenditure": round(total, 2) if any_amount else None,
    }


def project_url_for(rec: dict, pid_str: str) -> str:
    """LT Info project detail page for a project id."""
    return PROJECT_URL_TEMPLATE.format(project_id=pid_str) if pid_str else ""


def _focus_label(detail: dict, eip: str) -> str:
    """'01 - Watersheds and Water Quality' from the detail record, falling
    back to the EIP-number prefix when the project has no focus area."""
    if detail and detail.get("EIPFocusAreaName"):
        num = detail.get("EIPFocusAreaNumber")
        return f"{int(num):02d} - {detail['EIPFocusAreaName']}" if num is not None else detail["EIPFocusAreaName"]
    return focus_area_from_eip(eip)


def _year(v):
    """Coerce a year-ish value to int or None (the API already sends ints)."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

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
    """Build the 'simple' frame: one row per project with the core fields
    from /projects plus a representative point for projects that have a
    map location. Replaces the retired GetProjectSimpleLocationAndGeospatialAssociations
    feed; the raw /projects response is kept in data/simple.json.

    Projects whose only location is regional (Basin-wide, a state, a
    jurisdiction) are kept ASPATIAL on purpose: the Tracker's regional
    feature collection gives them a centroid of the whole area, which
    reads as a false precise location on a map. Their group and area are
    carried in ProjectLocationGroup / ProjectLocationArea instead.
    """
    log.info(f"Fetching projects: {PROJECTS_URL}")
    resp = requests.get(PROJECTS_URL, timeout=120)
    resp.raise_for_status()
    SIMPLE_JSON_PATH.write_text(resp.text, encoding="utf-8")
    log.info(f"Wrote raw JSON to {SIMPLE_JSON_PATH} ({len(resp.text):,} bytes)")
    projects = resp.json()
    if not isinstance(projects, list) or not projects:
        log.warning(f"Unexpected /projects shape (type={type(projects).__name__}); returning empty frame")
        return pd.DataFrame()

    log.info(f"Fetching mapped points: {MAPPED_POINTS_URL}")
    mapped = requests.get(MAPPED_POINTS_URL, timeout=120).json()
    coords = {}
    for f in (mapped.get("features") or []):
        g = f.get("geometry") or {}
        pid = (f.get("properties") or {}).get("ProjectID")
        if g.get("type") == "Point" and pid is not None and g.get("coordinates"):
            coords[pid] = (float(g["coordinates"][1]), float(g["coordinates"][0]))   # (lat, lon)
    log.info(f"Mapped points: {len(coords):,} projects with a point location")

    log.info(f"Fetching regional projects: {REGIONAL_URL}")
    regional = requests.get(REGIONAL_URL, timeout=120).json()
    regional_area = {}
    for f in (regional.get("features") or []):
        p = f.get("properties") or {}
        if p.get("ProjectID") is not None:
            regional_area[p["ProjectID"]] = (p.get("ProjectLocationGroup"), p.get("ProjectLocationArea"))
    log.info(f"Regional projects: {len(regional_area):,}")

    rows = []
    for p in projects:
        pid = p.get("ProjectID")
        lat, lon = coords.get(pid, (None, None))
        grp, area = regional_area.get(pid, (None, None))
        rows.append({
            # legacy column names the GDB step and build_projects_geojson key on
            "ProjectID": pid,
            "EIPProjectNumber": str(p.get("ProjectNumber") or "").strip(),
            "ProjectName": p.get("ProjectName") or "",
            "Latitude": lat,
            "Longitude": lon,
            "NoLocation": lat is None,
            "Region": p.get("Region") or "",
            "State": p.get("StateProvince") or "",
            "Jurisdiction": p.get("Jurisdiction") or "",
            "Watershed": p.get("Watershed") or "",
            # core fields carried through to the outputs
            "LeadImplementerName": p.get("LeadImplementerName"),
            "Stage": p.get("Stage"),
            "IsEIPProject": p.get("IsEIPProject"),
            "IsLakeClarityProject": p.get("IsLakeClarityProject"),
            "IsTransportationProject": p.get("IsTransportationProject"),
            "PlanningDesignStartYear": p.get("PlanningDesignStartYear"),
            "ImplementationStartYear": p.get("ImplementationStartYear"),
            "CompletionYear": p.get("CompletionYear"),
            "EstimatedTotalCost": p.get("EstimatedTotalCost"),
            "SecuredFunding": p.get("SecuredFunding"),
            "UnfundedNeed": p.get("UnfundedNeed"),
            "ProjectDescription": p.get("ProjectDescription") or "",
            "Tags": "; ".join(sorted({t.get("TagName") for t in (p.get("Tags") or []) if t.get("TagName")})) or None,
            "ProjectLocationGroup": grp,
            "ProjectLocationArea": area,
            "LastModificationDate": p.get("LastModificationDate"),
            "NumberOfReportedExpenditureRecords": p.get("NumberOfReportedExpenditureRecords") or 0,
        })
    df = pd.DataFrame(rows)
    df = df[df["EIPProjectNumber"] != ""].copy()
    stages = df["Stage"].value_counts().to_dict()
    log.info(f"Projects frame: {len(df):,} rows; stages={stages}")
    log.info(f"  with point location: {int((~df['NoLocation']).sum()):,}; regional only: "
             f"{int((df['NoLocation'] & df['ProjectLocationGroup'].notna()).sum()):,}; no location at all: "
             f"{int((df['NoLocation'] & df['ProjectLocationGroup'].isna()).sum()):,}")
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
GEOM_TYPE_BUCKETS = {
    "Polygon": "Polygon", "MultiPolygon": "Polygon",
    "LineString": "Line", "MultiLineString": "Line",
    "Point": "Point", "MultiPoint": "Point",
}


def dissolve_detailed_features(fc):
    """Aggregate the raw detailed features by (EIPProjectNumber, geom-type
    bucket) so each project ends up with at most three features (polygon,
    line, point). Without this step the upstream feed has a few projects
    contributing thousands of fragment features each (e.g. one project
    has 4,005, another 2,071) which overwhelms the dashboard map even
    with a selection-scoped filter applied.

    Uses geopandas (already in arcgispro-py3 + on the GitHub Action runner
    via the requirements install). Falls back to passing the raw FC
    through if geopandas isn't importable, so the script still completes.
    """
    if not isinstance(fc, dict) or not fc.get("features"):
        return fc
    try:
        import geopandas as gpd  # noqa: WPS433 (lazy import)
    except ImportError:
        log.warning("geopandas unavailable — skipping detailed-feature dissolve "
                    "(footprints layer may render thousands of fragment polygons)")
        return fc

    raw_n = len(fc["features"])
    try:
        gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326")
    except Exception as e:
        log.warning(f"GeoDataFrame.from_features failed ({e}); skipping dissolve")
        return fc
    if gdf.empty or "EIPProjectNumber" not in gdf.columns:
        return fc

    # Bucket geometry types so a project's many small Polygons collapse to
    # one MultiPolygon, its many LineStrings to one MultiLineString, etc.
    gdf["_geom_bucket"] = gdf.geometry.geom_type.map(GEOM_TYPE_BUCKETS).fillna("Other")
    # Drop unknown geometry types (rare; just a safety net)
    gdf = gdf[gdf["_geom_bucket"] != "Other"]
    if gdf.empty:
        return fc

    # Coerce EIPProjectNumber to string for stable grouping
    gdf["EIPProjectNumber"] = gdf["EIPProjectNumber"].astype(str)

    # Dissolve combines geometries via shapely.unary_union per group.
    # by=[...] keeps those columns; aggfunc="first" keeps the first row's
    # other props (ProjectName, ProjectID — same per project) which is what
    # we want.
    try:
        dissolved = gdf.dissolve(
            by=["EIPProjectNumber", "_geom_bucket"],
            aggfunc="first",
            as_index=False,
        )
    except Exception as e:
        log.warning(f"dissolve failed ({e}); returning raw FC")
        return fc

    # Drop the bucket helper column before serialising
    dissolved = dissolved.drop(columns=["_geom_bucket"])
    out = json.loads(dissolved.to_json())
    log.info(f"Dissolved detailed features: {raw_n:,} → {len(out['features']):,} "
             f"(grouped by EIPProjectNumber + geometry type)")
    return out


def fetch_detailed(df_simple):
    """Assemble the detailed-footprint FeatureCollection. The new API has no
    anonymous bulk footprint route, so this calls
    projects/{id}/location-as-feature-collection for every project that has
    a point or regional location and keeps the 'Detail' layer features
    (polygons, lines, points drawn by the implementer). Each feature gets
    EIPProjectNumber / ProjectName / ProjectID properties so the dissolve
    and the dashboard's footprints layer work as before."""
    if df_simple.empty:
        return None
    located = df_simple[(~df_simple["NoLocation"]) | df_simple["ProjectLocationGroup"].notna()]
    by_pid = {int(r.ProjectID): (r.EIPProjectNumber, r.ProjectName) for r in located.itertuples()}
    log.info(f"Fetching detailed locations for {len(by_pid):,} located projects...")
    fcs = _fetch_many(list(by_pid), lambda i: LOCATION_FC_URL.format(pid=i), label="Location feature collections")

    features = []
    for pid, pfc in fcs.items():
        eip, name = by_pid[pid]
        for f in (pfc.get("features") or []) if isinstance(pfc, dict) else []:
            props = f.get("properties") or {}
            if props.get("LocationType") != "Detail" or not f.get("geometry"):
                continue
            features.append({
                "type": "Feature",
                "geometry": f["geometry"],
                "properties": {"EIPProjectNumber": eip, "ProjectName": name, "ProjectID": pid,
                               "LayerName": props.get("LayerName")},
            })
    fc = {"type": "FeatureCollection", "features": features}
    log.info(f"Detailed locations: {len(features):,} raw features across "
             f"{len({f['properties']['EIPProjectNumber'] for f in features}):,} projects")

    # Compute the unique-EIP index BEFORE dissolving (so it counts the
    # set of projects that have any detailed geometry — same answer
    # either way, but cheaper from the raw list)
    detailed_eips = sorted({
        str((f.get("properties") or {}).get("EIPProjectNumber") or "").strip()
        for f in features
        if (f.get("properties") or {}).get("EIPProjectNumber")
    })
    DETAILED_EIPS_PATH.write_text(
        json.dumps(detailed_eips, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info(f"Wrote {len(detailed_eips):,} unique EIP #s to {DETAILED_EIPS_PATH}")

    # Dissolve fragment features per project (8,506 → ~800) before saving
    fc = dissolve_detailed_features(fc)

    DETAILED_GEOJSON_PATH.write_text(
        json.dumps(fc, ensure_ascii=False),
        encoding="utf-8",
    )
    size_kb = DETAILED_GEOJSON_PATH.stat().st_size / 1024
    log.info(f"Wrote dissolved GeoJSON to {DETAILED_GEOJSON_PATH} ({size_kb:,.0f} KB)")

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

    # Pass 2 — per-project detail (focus area, program, action priority,
    # thresholds, funding) for ALL projects, one parallel batch
    all_rows = list(r for r, _, _ in spatial) + aspatial
    all_eips = sorted({str(r.get(pn_key, "")).strip() for r in all_rows if r.get(pn_key)})
    log.info(f"Fetching project details for {len(all_eips)} EIP numbers...")
    details = fetch_all_project_details(all_eips)

    # Pass 2b — expenditures by funding source, only where the Tracker says
    # there are rows (saves ~a third of the calls)
    pids = sorted({int(r["ProjectID"]) for r in all_rows
                   if r.get("ProjectID") is not None and (r.get("NumberOfReportedExpenditureRecords") or 0) > 0})
    log.info(f"Fetching expenditures for {len(pids)} projects...")
    expenditures = fetch_all_project_expenditures(pids)

    def _clean(val):
        if isinstance(val, float) and (val != val):   # NaN
            return None
        if hasattr(val, "item"):                      # numpy scalar
            val = val.item()
        return val

    def build_props(r, rec):
        pn = str(r.get(pn_key, "")).strip()
        pid = r.get(pid_key) if pid_key else None
        pid_str = str(int(pid)) if pid is not None and not pd.isna(pid) else ""
        rec = rec or {}
        state = _clean(r.get("State")) or None
        props = {
            "ProjectID": pid_str,
            "EIPProjectNumber": pn,
            "ProjectName": str(r.get(name_key, "")) if name_key else "",
            "Category": _focus_label(rec, pn),
            "ProjectURL": project_url_for(rec, pid_str),
            # legacy GetProject field names, filled from the new API
            "EIPFocusArea": rec.get("EIPFocusAreaName"),
            "EIPProgram": rec.get("EIPProgramName"),
            "EIPActionPriority": rec.get("EIPActionPriorityName"),
            "ProjectDescription": rec.get("ProjectDescription") or _clean(r.get("ProjectDescription")) or "",
            "ProjectThresholdCategories": rec.get("ThresholdCategories"),
            "LeadImplementer": rec.get("LeadImplementerName") or _clean(r.get("LeadImplementerName")),
            "PlanningStartDate": _year(rec.get("PlanningDesignStartYear", _clean(r.get("PlanningDesignStartYear")))),
            "ImplementationStartDate": _year(rec.get("ImplementationStartYear", _clean(r.get("ImplementationStartYear")))),
            "EndDate": _year(rec.get("CompletionYear", _clean(r.get("CompletionYear")))),
            "Stage": rec.get("Stage") or _clean(r.get("Stage")),
            "ProjectRegion": _clean(r.get("Region")) or None,
            "ProjectState": state,
            "ProjectJurisdiction": _clean(r.get("Jurisdiction")) or None,
            "ProjectWatershed": _clean(r.get("Watershed")) or None,
            "ProjectSummaryUrl": project_url_for(rec, pid_str),
            "ProjectFactSheetUrl": FACT_SHEET_URL_TEMPLATE.format(eip=pn),
            "TMDLPollutantSourceCategory": None,     # not exposed by the new API
            "EstimatedTotalCost": _clean(rec.get("EstimatedTotalCost", _clean(r.get("EstimatedTotalCost")))),
            "EstimatedAnnualOperatingCost": _clean(rec.get("EstimatedAnnualOperatingCost")),
            "IsEIPProject": bool(_clean(r.get("IsEIPProject"))),
            "IsTransportationProject": bool(_clean(r.get("IsTransportationProject"))),
            "IsLakeClarityProject": bool(_clean(r.get("IsLakeClarityProject"))),
        }
        for field in NEW_FIELDS:
            props[field] = _clean(rec.get(field, _clean(r.get(field))))
        # Funding sources and total spent from the expenditure rows; the
        # Tracker's own ReportedExpenditure total wins when present.
        spend = summarize_expenditures(expenditures.get(int(pid)) if pid is not None and not pd.isna(pid) else None)
        if rec.get("ReportedExpenditure") is not None:
            spend["TotalExpenditure"] = _clean(rec.get("ReportedExpenditure"))
        props.update(spend)
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

        # 2. Always: fetch detailed footprints, write data/detailed.geojson
        fc = fetch_detailed(df_simple)

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
