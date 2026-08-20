import json
import sys
import requests


# -----------------------------------------------------------
# USER CONFIGURATION (CHANGE THESE AS NEEDED)
# -----------------------------------------------------------

PORTAL_BASE_URL   = "https://<your-org>.maps.arcgis.com"
SERVICE_NAME      = "<your-feature-service>"
TOKEN             = "<your-token>"

POINT_LAYER_ID    = "<point-layer-id>"
LINE_LAYER_ID     = "<line-layer-id>"
POLY_LAYER_ID     = "<polygon-layer-id>"

PRIMARY_KEY       = "Notam_ID"

# REST URLs
POINT_URL = f"{PORTAL_BASE_URL}/server/rest/services/{SERVICE_NAME}/FeatureServer/{POINT_LAYER_ID}"
LINE_URL  = f"{PORTAL_BASE_URL}/server/rest/services/{SERVICE_NAME}/FeatureServer/{LINE_LAYER_ID}"
POLY_URL  = f"{PORTAL_BASE_URL}/server/rest/services/{SERVICE_NAME}/FeatureServer/{POLY_LAYER_ID}"


# -----------------------------------------------------------
# HELPERS: QUERY LAYER
# -----------------------------------------------------------

def query_existing(url, token):
    """Query existing records to build a dict {Notam_ID -> OBJECTID}"""
    query_url = f"{url}/query"

    params = {
        "f": "json",
        "token": token,
        "where": "1=1",
        "outFields": f"{PRIMARY_KEY},OBJECTID"
    }

    r = requests.get(query_url, params=params).json()

    mapping = {}

    if "features" in r:
        for feat in r["features"]:
            attrs = feat["attributes"]
            nid = attrs.get(PRIMARY_KEY)
            oid = attrs.get("OBJECTID")
            if nid is not None:
                mapping[nid] = oid

    return mapping


# -----------------------------------------------------------
# FEATURE FLATTENING AND GEOMETRY CLASSIFICATION
# -----------------------------------------------------------

def flatten_and_classify(feature):
    core = feature["properties"]["coreNOTAMData"]

    notamEvent = core.get("notamEvent", {})
    notam      = core.get("notam", {})
    translations = core.get("notamTranslation", [])

    # translation fields
    local_text = None
    icao_text  = None

    for t in translations:
        if t["type"] == "LOCAL_FORMAT":
            local_text = t.get("simpleText")
        elif t["type"] == "ICAO":
            icao_text = t.get("formattedText")

    # attributes
    attrs = {}
    for k, v in notamEvent.items():
        attrs[f"event_{k}"] = v
    for k, v in notam.items():
        attrs[k] = v

    # store translations
    attrs["localText"] = local_text
    attrs["icaoText"]  = icao_text

    # ensure primary key present
    attrs[PRIMARY_KEY] = notam.get("id")

    # classify geometry
    geomType  = None
    geometry  = None

    try:
        g = feature["geometry"]["geometries"][0]
        geomType = g["type"]
    except:
        g = None

    if g:
        if geomType == "Point":
            lon, lat = g["coordinates"]
            geometry = {
                "x": lon,
                "y": lat,
                "spatialReference": {"wkid": 4326}
            }
        elif geomType == "LineString":
            geometry = {
                "paths": [g["coordinates"]],
                "spatialReference": {"wkid": 4326}
            }
        elif geomType == "Polygon":
            geometry = {
                "rings": g["coordinates"],
                "spatialReference": {"wkid": 4326}
            }

    return geomType, attrs, geometry


# -----------------------------------------------------------
# SPLIT INTO POINT / LINE / POLYGON
# -----------------------------------------------------------

def split_by_geometry(json_data):
    pts, lines, polys = [], [], []

    for f in json_data["data"]["geojson"]:
        geomType, attrs, geom = flatten_and_classify(f)

        pkg = {"attributes": attrs, "geometry": geom}

        if geomType == "Point":
            pts.append(pkg)
        elif geomType == "LineString":
            lines.append(pkg)
        elif geomType == "Polygon":
            polys.append(pkg)
        else:
            pts.append(pkg)

    return pts, lines, polys


# -----------------------------------------------------------
# BUILD ADD / UPDATE / MARK OLD SETS
# -----------------------------------------------------------

def build_edit_batches(new_features, existing_mapping):
    adds    = []
    updates = []
    new_ids = set()

    for feat in new_features:
        nid = feat["attributes"].get(PRIMARY_KEY)
        new_ids.add(nid)

        if nid in existing_mapping:
            # Update existing row
            feat["attributes"]["OBJECTID"] = existing_mapping[nid]
            updates.append(feat)
        else:
            # Add new row
            adds.append(feat)

    return adds, updates, new_ids


def build_old_status_updates(existing_mapping, new_ids):
    """Existing IDs NOT present in new JSON → mark status='Old'."""
    olds = []
    for nid, oid in existing_mapping.items():
        if nid not in new_ids:
            olds.append({
                "attributes": {
                    "OBJECTID": oid,
                    "status": "Old"
                }
            })
    return olds


# -----------------------------------------------------------
# APPLY EDITS
# -----------------------------------------------------------

def apply_edits(url, token, adds=None, updates=None):
    ae = f"{url}/applyEdits"

    payload = {
        "f": "json",
        "token": token,
        "adds": json.dumps(adds or []),
        "updates": json.dumps(updates or [])
    }

    r = requests.post(ae, data=payload)
    r.raise_for_status()
    return r.json()


def check_edit_result(result, label):
    """Print any errors in an applyEdits response. Returns True if everything succeeded."""
    if "error" in result:
        print(f"  ERROR ({label}): {result['error']}")
        return False

    ok = True
    for key in ("addResults", "updateResults", "deleteResults"):
        for item in result.get(key, []):
            if not item.get("success", True):
                print(f"  ERROR ({label}, {key}): {item.get('error')}")
                ok = False

    return ok


# -----------------------------------------------------------
# MAIN WORKFLOW
# -----------------------------------------------------------

def process_layer(new_feats, layer_url):
    # Step 1 — query existing records
    existing = query_existing(layer_url, TOKEN)

    # Step 2 — build add/update sets
    adds, updates, new_ids = build_edit_batches(new_feats, existing)

    # Step 3 — mark old NOTAMs
    olds = build_old_status_updates(existing, new_ids)

    # Step 4 — send adds + updates
    result_1 = apply_edits(layer_url, TOKEN, adds=adds, updates=updates)
    ok_1 = check_edit_result(result_1, "adds/updates")

    # Step 5 — send old-updates
    result_2 = apply_edits(layer_url, TOKEN, adds=[], updates=olds)
    ok_2 = check_edit_result(result_2, "mark-old")

    return {
        "adds": result_1,
        "olds": result_2,
        "success": ok_1 and ok_2
    }


# -----------------------------------------------------------
# DRIVER
# -----------------------------------------------------------

def main():
    with open("Untitled-1.json", "r") as f:
        data = json.load(f)

    pts, lines, polys = split_by_geometry(data)

    print("Processing Points...")
    pts_result = process_layer(pts, POINT_URL)

    print("Processing Lines...")
    line_result = process_layer(lines, LINE_URL)

    print("Processing Polygons...")
    poly_result = process_layer(polys, POLY_URL)

    all_ok = pts_result["success"] and line_result["success"] and poly_result["success"]

    if all_ok:
        print("Done.")
    else:
        print("Done — completed with errors, see above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
