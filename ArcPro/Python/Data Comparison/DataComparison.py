import arcpy
import datetime
import traceback
from math import sqrt

# Global ignore fields (case-insensitive)
ignore_fields = {"globalid", "lastupdate", "createdate", "editor"}

# Fields that cannot be recreated
oid_like_fields = {"objectid", "oid", "fid"}

# Esri-managed fields to exclude
esri_managed_fields = {"shape_length", "shape_area"}

print("Cell 1 loaded at:", datetime.datetime.now(), "Globals Set")
# Return centroid as (X, Y)
def get_centroid(geom):
    if geom is None:
        return None
    c = geom.centroid
    return (c.X, c.Y)


# Normalize Pub attributes to Work schema
def normalize_pub_attrs(pub_attrs, work_fields, pub_fields):
    out = []
    for f in work_fields:
        if f in pub_fields:
            out.append(pub_attrs[pub_fields.index(f)])
        else:
            out.append("")
    return out


# Parse date safely
def parse_dt(val):
    if val in (None, "", " "):
        return None
    try:
        return datetime.datetime.fromisoformat(val)
    except:
        try:
            return datetime.datetime.strptime(val, "%Y-%m-%d")
        except:
            return None


# Case-insensitive field index lookup
def find_index(fields, name):
    name = name.lower()
    for i, f in enumerate(fields):
        if f.lower() == name:
            return i
    return None


print("Cell 2 loaded at:", datetime.datetime.now(), "Utility functions ready")

class KDTree:
    def __init__(self, points):
        # points = [(x, y, idx)]
        self.points = points

    # Return (index, distance)
    def nearest(self, x, y):
        best_idx = None
        best_dist = float("inf")
        for px, py, idx in self.points:
            d = sqrt((px - x)**2 + (py - y)**2)
            if d < best_dist:
                best_dist = d
                best_idx = idx
        return best_idx, best_dist

print("Cell 3 loaded at:", datetime.datetime.now(), "KDTree ready")

def load_features(fc):

    if not arcpy.Exists(fc):
        print("[ERROR] Feature class does not exist:", fc)
        return [], []

    desc = arcpy.Describe(fc)
    shape_field = desc.shapeFieldName.lower()

    allowed_fields = []
    for f in arcpy.ListFields(fc):
        lname = f.name.lower()

        if lname == shape_field:
            continue
        if lname in ignore_fields:
            continue
        if lname in oid_like_fields:
            continue
        if lname in esri_managed_fields:
            continue

        allowed_fields.append(f.name)

    features = []
    try:
        with arcpy.da.SearchCursor(fc, allowed_fields + ["SHAPE@"]) as cur:
            for row in cur:
                attrs = row[:-1]
                geom = row[-1]
                features.append((attrs, geom))
    except:
        print("[ERROR] Failed loading features:", fc)
        print(traceback.format_exc())
        return [], []

    return features, allowed_fields

print("Cell 4 executed at:", datetime.datetime.now())

### Cell 5 | LEVELID Mode with use_keys support

def process_layer_with_levelid(work_fc, pub_fc, result_gbd, layer_name,
                               tolerance, use_keys=True):

    # Load filtered features
    work_features, work_fields = load_features(work_fc)
    pub_features, pub_fields  = load_features(pub_fc)

    if not work_features and not pub_features:
        return (layer_name, 0, 0, 0, 0)

    # Locate LEVELID
    w_idx = find_index(work_fields, "levelid")
    p_idx = find_index(pub_fields, "levelid")

    if w_idx is None or p_idx is None:
        print("[ERROR] LEVELID missing:", layer_name)
        return (layer_name, 0, 0, 0, 0)

    # Group by LEVELID
    work_groups = {}
    for attrs, geom in work_features:
        lv = attrs[w_idx]
        work_groups.setdefault(lv, []).append((attrs, geom))

    pub_groups = {}
    for attrs, geom in pub_features:
        lv = attrs[p_idx]
        pub_groups.setdefault(lv, []).append((attrs, geom))

    # Prepare output FC
    out_fc = f"{result_gbd}/{layer_name}_Changes"
    if arcpy.Exists(out_fc):
        arcpy.Delete_management(out_fc)

    sp_ref = arcpy.Describe(work_fc).spatialReference
    shape_type = arcpy.Describe(work_fc).shapeType

    arcpy.management.CreateFeatureclass(
        result_gbd,
        f"{layer_name}_Changes",
        shape_type,
        spatial_reference=sp_ref
    )

    arcpy.AddField_management(out_fc, "OPERATION", "TEXT")
    for f in work_fields:
        arcpy.AddField_management(out_fc, f, "TEXT")

    insert_fields = ["OPERATION"] + work_fields + ["SHAPE@"]

    add = edit = delete = duplicate = 0

    with arcpy.da.InsertCursor(out_fc, insert_fields) as icur:

        for lv in (work_groups.keys() | pub_groups.keys()):

            w_list = work_groups.get(lv, [])
            p_list = pub_groups.get(lv, [])

            # -----------------------------------------------------------------------
            # DUPLICATE DETECTION (sapid/equipmentid) — ONLY if use_keys=True
            # -----------------------------------------------------------------------
            duplicate_groups = {}
            if use_keys:

                sap_w = find_index(work_fields, "sapid")
                eqp_w = find_index(work_fields, "equipmentid")
                sap_p = find_index(pub_fields, "sapid")
                eqp_p = find_index(pub_fields, "equipmentid")

                dup_keys = set()

                # Collect possible key values
                if sap_w is not None and sap_p is not None:
                    for a,_ in w_list: dup_keys.add(a[sap_w])
                    for a,_ in p_list: dup_keys.add(a[sap_p])

                if eqp_w is not None and eqp_p is not None:
                    for a,_ in w_list: dup_keys.add(a[eqp_w])
                    for a,_ in p_list: dup_keys.add(a[eqp_p])

                # Build duplicate groups
                for val in dup_keys:
                    if val in ("", None):
                        continue

                    matches_w = [
                        i for i,(a,g) in enumerate(w_list)
                        if (sap_w is not None and a[sap_w] == val) or
                           (eqp_w is not None and a[eqp_w] == val)
                    ]

                    matches_p = [
                        i for i,(a,g) in enumerate(p_list)
                        if (sap_p is not None and a[sap_p] == val) or
                           (eqp_p is not None and a[eqp_p] == val)
                    ]

                    if len(matches_w) + len(matches_p) > 1:
                        duplicate_groups[val] = (matches_w, matches_p)

            # Emit DUPLICATE rows (Option B)
            for val, (dw, dp) in duplicate_groups.items():

                # Work duplicates
                for idx in dw:
                    attrs, geom = w_list[idx]
                    icur.insertRow(["DUPLICATE"] + list(attrs) + [geom])
                    duplicate += 1

                # Pub duplicates
                for idx in dp:
                    attrs, geom = p_list[idx]
                    norm = normalize_pub_attrs(attrs, work_fields, pub_fields)
                    icur.insertRow(["DUPLICATE"] + norm + [geom])
                    duplicate += 1

            # If duplicates present, skip normal matching
            if duplicate_groups:
                continue

            # -----------------------------------------------------------------------
            # NORMAL KD-TREE MATCHING (no duplicates)
            # -----------------------------------------------------------------------
            centroids = []
            for idx,(attrs, geom) in enumerate(p_list):
                c = get_centroid(geom)
                if c:
                    centroids.append((c[0], c[1], idx))

            tree = KDTree(centroids)
            used = set()

            for w_attrs, w_geom in w_list:

                wc = get_centroid(w_geom)
                if wc is None:
                    continue

                p_idx2, dist = tree.nearest(wc[0], wc[1])

                # ADD
                if p_idx2 is None or dist > tolerance or p_idx2 in used:
                    icur.insertRow(["ADD"] + list(w_attrs) + [w_geom])
                    add += 1
                    continue

                # Matched
                used.add(p_idx2)
                p_attrs, p_geom = p_list[p_idx2]
                norm_p = normalize_pub_attrs(p_attrs, work_fields, pub_fields)

                # Determine if non-lastupdate fields changed
                last_idx = find_index(work_fields, "lastupdate")
                changed_non_last = False

                for i, fname in enumerate(work_fields):
                    if fname.lower() != "lastupdate" and w_attrs[i] != norm_p[i]:
                        changed_non_last = True
                        break

                # Only lastupdate changed → skip
                if not changed_non_last:
                    continue

                # Determine "winner" values
                winner_attrs = list(w_attrs)

                if last_idx is not None:
                    w_dt = parse_dt(w_attrs[last_idx])
                    p_dt = parse_dt(norm_p[last_idx])

                    if (w_dt is None and p_dt is not None) or \
                       (w_dt is not None and p_dt is not None and p_dt > w_dt):
                        winner_attrs = norm_p

                # Build EDIT diff — winner values only
                diff = []
                for i in range(len(work_fields)):
                    if w_attrs[i] != norm_p[i]:
                        diff.append(winner_attrs[i])
                    else:
                        diff.append(None)

                icur.insertRow(["EDIT"] + diff + [w_geom])
                edit += 1

            # DELETE all unmatched Pub rows
            for idx,(attrs,geom) in enumerate(p_list):
                if idx not in used:
                    norm = normalize_pub_attrs(attrs, work_fields, pub_fields)
                    icur.insertRow(["DELETE"] + norm + [geom])
                    delete += 1

    return (layer_name, add, edit, delete, duplicate)

print("Cell 5 executed at:", datetime.datetime.now())

### Cell 6 | External Mode with use_keys support

def process_layer_without_levelid(work_fc, pub_fc, result_gbd, layer_name,
                                  use_keys=True,
                                  tol_points=1.0, tol_lines=3.0, tol_polygons=5.0):

    # Load features
    work_features, work_fields = load_features(work_fc)
    pub_features, pub_fields  = load_features(pub_fc)

    if not work_features and not pub_features:
        return (layer_name, 0, 0, 0, 0)

    # Determine tolerance by geometry
    geom_type = arcpy.Describe(work_fc).shapeType.lower()
    tol = tol_points if geom_type == "point" else \
          tol_lines if geom_type == "polyline" else \
          tol_polygons

    # Prepare output FC
    out_fc = f"{result_gbd}/{layer_name}_Changes"
    if arcpy.Exists(out_fc):
        arcpy.Delete_management(out_fc)

    sp_ref = arcpy.Describe(work_fc).spatialReference
    arcpy.management.CreateFeatureclass(
        result_gbd,
        f"{layer_name}_Changes",
        geom_type,
        spatial_reference=sp_ref
    )

    arcpy.AddField_management(out_fc, "OPERATION", "TEXT")
    for f in work_fields:
        arcpy.AddField_management(out_fc, f, "TEXT")

    insert_fields = ["OPERATION"] + work_fields + ["SHAPE@"]

    add = edit = delete = duplicate = 0

    # ---------------------------------------------------------
    # DUPLICATE DETECTION (sapid/equipmentid)
    # ---------------------------------------------------------
    duplicate_groups = {}
    if use_keys:

        sap_w = find_index(work_fields, "sapid")
        eqp_w = find_index(work_fields, "equipmentid")
        sap_p = find_index(pub_fields,  "sapid")
        eqp_p = find_index(pub_fields,  "equipmentid")

        dup_keys = set()

        if sap_w is not None and sap_p is not None:
            for a,_ in work_features: dup_keys.add(a[sap_w])
            for a,_ in pub_features:  dup_keys.add(a[sap_p])

        if eqp_w is not None and eqp_p is not None:
            for a,_ in work_features: dup_keys.add(a[eqp_w])
            for a,_ in pub_features:  dup_keys.add(a[eqp_p])

        for val in dup_keys:
            if val in ("", None):
                continue

            matches_w = [
                i for i,(a,g) in enumerate(work_features)
                if (sap_w is not None and a[sap_w] == val) or
                   (eqp_w is not None and a[eqp_w] == val)
            ]

            matches_p = [
                i for i,(a,g) in enumerate(pub_features)
                if (sap_p is not None and a[sap_p] == val) or
                   (eqp_p is not None and a[eqp_p] == val)
            ]

            if len(matches_w) + len(matches_p) > 1:
                duplicate_groups[val] = (matches_w, matches_p)

    # Start writing output
    with arcpy.da.InsertCursor(out_fc, insert_fields) as icur:

        # Emit duplicates
        for val,(dw,dp) in duplicate_groups.items():

            for idx in dw:
                attrs, geom = work_features[idx]
                icur.insertRow(["DUPLICATE"] + list(attrs) + [geom])
                duplicate += 1

            for idx in dp:
                attrs, geom = pub_features[idx]
                norm = normalize_pub_attrs(attrs, work_fields, pub_fields)
                icur.insertRow(["DUPLICATE"] + norm + [geom])
                duplicate += 1

        # Skip normal matching if duplicates exist
        if duplicate_groups:
            return (layer_name, add, edit, delete, duplicate)

        # ---------------------------------------------------------
        # NORMAL MATCHING (KD-tree)
        # ---------------------------------------------------------
        centroids = []
        for idx,(attrs, geom) in enumerate(pub_features):
            c = get_centroid(geom)
            if c:
                centroids.append((c[0], c[1], idx))

        tree = KDTree(centroids)
        used = set()

        for w_attrs, w_geom in work_features:

            wc = get_centroid(w_geom)
            if wc is None:
                continue

            p_idx, dist = tree.nearest(wc[0], wc[1])

            # ADD
            if p_idx is None or dist > tol or p_idx in used:
                icur.insertRow(["ADD"] + list(w_attrs) + [w_geom])
                add += 1
                continue

            used.add(p_idx)
            p_attrs, p_geom = pub_features[p_idx]
            norm_p = normalize_pub_attrs(p_attrs, work_fields, pub_fields)

            # Detect differences
            last_idx = find_index(work_fields, "lastupdate")
            changed_non_last = False

            for i, fname in enumerate(work_fields):
                if fname.lower() != "lastupdate" and w_attrs[i] != norm_p[i]:
                    changed_non_last = True
                    break

            # Only lastupdate changed → skip EDIT
            if not changed_non_last:
                continue

            # Determine winner values
            winner_attrs = list(w_attrs)
            if last_idx is not None:
                w_dt = parse_dt(w_attrs[last_idx])
                p_dt = parse_dt(norm_p[last_idx])

                if (w_dt is None and p_dt is not None) or \
                   (w_dt is not None and p_dt is not None and p_dt > w_dt):
                    winner_attrs = norm_p

            # Build EDIT diff (winner values)
            diff = []
            for i in range(len(work_fields)):
                if w_attrs[i] != norm_p[i]:
                    diff.append(winner_attrs[i])
                else:
                    diff.append(None)

            icur.insertRow(["EDIT"] + diff + [w_geom])
            edit += 1

        # DELETE unmatched Pub
        for idx,(attrs,geom) in enumerate(pub_features):
            if idx not in used:
                norm = normalize_pub_attrs(attrs, work_fields, pub_fields)
                icur.insertRow(["DELETE"] + norm + [geom])
                delete += 1

    return (layer_name, add, edit, delete, duplicate)

print("Cell 6 executed at:", datetime.datetime.now())

def layer_has_levelid(fc):
    fields = [f.name.lower() for f in arcpy.ListFields(fc)]
    return "levelid" in fields

print("Cell 7 loaded at:", datetime.datetime.now(), "LEVELID checker ready")

### Cell 8 | Multi-layer Runner (with use_keys parameter)

def run_all_layers(primary_gbd, edit_gbd, result_gbd, tolerance=1.0, use_keys=True):
    """
    use_keys=True  → use sapid/equipmentid matching + duplicate logic
    use_keys=False → do NOT use key-based matching logic
    """

    results = []
    arcpy.env.workspace = primary_gbd
    layers = arcpy.ListFeatureClasses()

    print("[INFO] Starting comparison run...")

    for layer_name in layers:
        print(f"[INFO] {datetime.datetime.now()} Processing layer: {layer_name}")

        work_fc = f"{primary_gbd}/{layer_name}"
        pub_fc  = f"{edit_gbd}/{layer_name}"

        if not arcpy.Exists(pub_fc):
            print(f"[WARN] Missing in Pub → SKIP: {layer_name}")
            continue

        # LEVELID Mode
        if layer_has_levelid(work_fc) and layer_has_levelid(pub_fc):
            print("  └─ Mode: LEVELID")
            try:
                res = process_layer_with_levelid(
                    work_fc, pub_fc, result_gbd, layer_name, tolerance, use_keys
                )
                results.append(res)
            except:
                print(f"[ERROR] Failed processing LEVELID layer {layer_name}")
                print(traceback.format_exc())

        # External Mode
        else:
            print("  └─ Mode: External (no LEVELID)")
            try:
                res = process_layer_without_levelid(
                    work_fc, pub_fc, result_gbd, layer_name, use_keys
                )
                results.append(res)
            except:
                print(f"[ERROR] Failed processing External layer {layer_name}")
                print(traceback.format_exc())

    print(f"[INFO] All layers processed: {len(results)}")
    return results


print("Cell 8 loaded at:", datetime.datetime.now())

### Cell 9 | Execution Harness

import datetime

# Set your GDB paths
primary_gbd = r"C:\"
edit_gbd  = r"C:\"
result_gbd  = r"C:\"

# Enable / disable sapid/equipmentid logic
use_match = False   # <-- set to False to disable key matching & duplicate logic

print("[INFO] Cell 9 loaded → GDB paths set")
print("       Work GDB :", primary_gbd)
print("       Pub GDB  :", edit_gbd)
print("       Out GDB  :", result_gbd)
print("       use_keys :", use_keys)

print("\n[INFO] Starting full multi-layer comparison run...")
results = run_all_layers(primary_gbd, edit_gbd, result_gbd, use_keys=use_match)
print("[INFO] Run complete.")

print("Layers processed:", len(results))
print("Cell 9 executed at:", datetime.datetime.now())
