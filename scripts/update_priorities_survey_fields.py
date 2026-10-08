"""Give the EIP Funding Priorities picks survey room for long pick lists.

The tool (maps repo, eip/funding-priorities.html) writes each reviewer's picks and notes into two text
fields that Survey123 published at 4,000 characters. ArcGIS Online does not change the length of an
existing text field on a hosted layer (updateDefinition answers success and keeps 4,000), so this adds
two new 50,000-character fields, picks_text and notes_text, to the parent hosted layer and exposes them
in the two views that sit on it: the Survey123 form view the tool submits to, and the public read view
the tool reads from. Field names on the original pair do not change, so the form keeps working; the
tool writes the full text to the new fields and a trimmed copy to the old ones.

Run it once from the arcgispro-py3 environment while ArcGIS Pro is signed in to trpa.maps.arcgis.com
(GIS("home") reuses that sign-in; no password in this file). Safe to run again: it skips what exists.

    python scripts/update_priorities_survey_fields.py
"""
import logging
import sys

from arcgis.features import FeatureLayer
from arcgis.gis import GIS

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

PARENT_ITEM = "0a58e883e3d0405aa9827815f58b677a"     # "EIP Funding Priorities picks", the hosted layer under both views
SERVICE = "https://services5.arcgis.com/fXXSUzHD5JjcOt1v/arcgis/rest/services"
FORM_VIEW = f"{SERVICE}/survey123_06f26df8a2ab4ddab59c4f82ee06d8e5_form/FeatureServer/0"   # what the form and the tool submit to
PUBLIC_VIEW = f"{SERVICE}/EIP_Funding_Priorities_picks_public_view/FeatureServer/0"       # what the tool reads, no email
LONG = 50000
NEW_FIELDS = [
    {"name": "picks_text", "type": "esriFieldTypeString", "alias": "Picks (full text: list, priority, request, project number; one per line)", "length": LONG, "nullable": True, "editable": True},
    {"name": "notes_text", "type": "esriFieldTypeString", "alias": "Notes (full text: project number: criteria and note; one per line)", "length": LONG, "nullable": True, "editable": True},
]
ALIASES = {
    "your_ranked_picks_project_numbe": "Your picks (first 4,000 characters; full text in picks_text)",
    "notes": "Notes (first 4,000 characters; full text in notes_text)",
}
WATCH = list(ALIASES) + [f["name"] for f in NEW_FIELDS]


def show(url, gis, label):
    lyr = FeatureLayer(url, gis)
    rows = [(f["name"], f.get("alias"), f.get("length")) for f in lyr.properties["fields"] if f["name"] in WATCH]
    log.info("%s: %s", label, rows)
    return {r[0]: r for r in rows}


def relabel(url, gis, label):
    lyr = FeatureLayer(url, gis)
    changes = [{"name": f["name"], "alias": ALIASES[f["name"]]} for f in lyr.properties["fields"] if f["name"] in ALIASES and f.get("alias") != ALIASES[f["name"]]]
    if changes:
        log.info("%s relabel: %s", label, lyr.manager.update_definition({"fields": changes}))


def expose_in_view(url, gis, label):
    """Add the new parent fields to a view's field list (a view shows only the source fields it names)."""
    lyr = FeatureLayer(url, gis)
    present = {f["name"] for f in lyr.properties["fields"]}
    todo = [f for f in NEW_FIELDS if f["name"] not in present]
    if not todo:
        log.info("%s already shows the new fields", label)
        return
    admin = dict(lyr.manager.properties)
    table = dict(admin["adminLayerInfo"]["viewLayerDefinition"]["table"])
    src_fields = list(table.get("sourceLayerFields", []))
    for f in todo:
        src_fields.append({"name": f["name"], "alias": f["alias"], "source": f["name"]})
    table["sourceLayerFields"] = src_fields
    log.info("%s add fields: %s", label, lyr.manager.update_definition({"viewLayerDefinition": {"table": table}}))


def main():
    gis = GIS("home")
    log.info("signed in as %s", gis.users.me.username)
    item = gis.content.get(PARENT_ITEM)
    parent_url = item.layers[0].url
    parent_props = FeatureLayer(parent_url, gis).properties
    log.info("parent layer: %s (%s) isView=%s", parent_url, parent_props.get("name"), parent_props.get("isView"))
    if parent_props.get("isView"):
        log.error("item %s is itself a view; find the hosted layer beneath it", PARENT_ITEM)
        return 1

    have = show(parent_url, gis, "parent before")
    show(FORM_VIEW, gis, "form view before")
    show(PUBLIC_VIEW, gis, "public view before")

    missing = [f for f in NEW_FIELDS if f["name"] not in have]
    if missing:
        log.info("parent addToDefinition: %s", FeatureLayer(parent_url, gis).manager.add_to_definition({"fields": missing}))
    else:
        log.info("new fields already on the parent layer")

    for url, label in ((FORM_VIEW, "form view"), (PUBLIC_VIEW, "public view")):
        try:
            expose_in_view(url, gis, label)
        except Exception as exc:
            log.warning("%s: could not add fields automatically (%s); add picks_text and notes_text under the view's Fields in ArcGIS Online", label, exc)

    for url, label in ((parent_url, "parent"), (FORM_VIEW, "form view"), (PUBLIC_VIEW, "public view")):
        try:
            relabel(url, gis, label)
        except Exception as exc:
            log.warning("%s relabel failed: %s", label, exc)

    after = {label: show(url, gis, f"{label} after") for url, label in ((parent_url, "parent"), (FORM_VIEW, "form view"), (PUBLIC_VIEW, "public view"))}
    bad = [label for label, rows in after.items() if not all(n in rows and (rows[n][2] or 0) >= LONG for n in ("picks_text", "notes_text"))]
    if bad:
        log.error("new fields missing or short on: %s", ", ".join(bad))
        return 1
    log.info("done: picks_text and notes_text (%s characters) are on the parent layer and both views", LONG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
