# -*- coding: utf-8 -*-
import arcpy
import traceback
from math import sqrt

# ---------------------------------------------------------
# DEBUG MAP + HELPER
# ---------------------------------------------------------

DEBUG_MAP = {
    "None": 0,
    "Light": 1,
    "Medium": 2,
    "Verbose": 3
}

def dbg(debug_level, level_required, msg):
    if debug_level >= level_required:
        arcpy.AddMessage(msg)

# ---------------------------------------------------------
# PYTHON TOOLBOX
# ---------------------------------------------------------

class Toolbox(object):
    def __init__(self):
        self.label = "Data Comparison Tool"
        self.alias = "datacomp"
        self.tools = [CompareLayersTool]


class CompareLayersTool(object):

    def __init__(self):
        self.label = "Compare Work vs Pub"
        self.description = (
            "Compares Work and Public geodatabases to identify Adds, Deletes, "
            "Attribute Edits, Geometry Edits, and Full Edits."
        )

    def getParameterInfo(self):

        p1 = arcpy.Parameter(
            displayName="Work Geodatabase",
            name="work_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )

        p2 = arcpy.Parameter(
            displayName="Public Geodatabase",
            name="pub_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )

        p3 = arcpy.Parameter(
            displayName="Output Geodatabase",
            name="out_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )

        p4 = arcpy.Parameter(
            displayName="LEVELID Tolerance (map units)",
            name="levelid_tol",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input"
        )
        p4.value = 1.0

        p6 = arcpy.Parameter(
            displayName="Point Tolerance (map units)",
            name="point_tol",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input"
        )
        p6.value = 1.0

        p7 = arcpy.Parameter(
            displayName="Line Tolerance (map units)",
            name="line_tol",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input"
        )
        p7.value = 3.0

        p8 = arcpy.Parameter(
            displayName="Polygon Tolerance (map units)",
            name="polygon_tol",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input"
        )
        p8.value = 5.0

        p5 = arcpy.Parameter(
            displayName="Debug Level",
            name="debug_level",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p5.filter.type = "ValueList"
        p5.filter.list = ["None", "Light", "Medium", "Verbose"]
        p5.value = "Light"

        p9 = arcpy.Parameter(
            displayName="Specific Layer Name (optional)",
            name="layer_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )

        return [p1, p2, p3, p4, p6, p7, p8, p5, p9]


    def execute(self, parameters, messages):

        work_gdb = parameters[0].valueAsText
        pub_gdb  = parameters[1].valueAsText
        out_gdb  = parameters[2].valueAsText

        levelid_tol = float(parameters[3].value)
        point_tol   = float(parameters[4].value)
        line_tol    = float(parameters[5].value)
        polygon_tol = float(parameters[6].value)

        debug_level = DEBUG_MAP.get(parameters[7].valueAsText or "None", 0)
        layer_name  = parameters[8].valueAsText

        messages.addMessage("[INFO] Running comparison…")

        run_all_layers(
            work_gdb, pub_gdb, out_gdb,
            levelid_tol,
            point_tol, line_tol, polygon_tol,
            layer_name,
            debug_level
        )

        messages.addMessage("[INFO] Comparison completed.")


# ---------------------------------------------------------
# GLOBAL FIELD RULES
# ---------------------------------------------------------

ignore_fields = {"globalid", "lastupdate", "createdate", "editor"}
oid_like_fields = {"objectid", "oid", "fid"}

# ---------------------------------------------------------
# NAME NORMALIZATION HELPERS
# ---------------------------------------------------------

def normalize_name(name):
    return name.split(".")[-1] if "." in name else name

def sanitize_output_name(name):
    n = normalize_name(name)
    for bad in [".", "-", " ", "(", ")", "[", "]"]:
        n = n.replace(bad, "_")
    if n and n[0].isdigit():
        n = "_" + n
    return n

# ---------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------

def get_centroid(geom):
    if not geom:
        return None
    c = geom.centroid
    return (c.X, c.Y)

def normalize_pub_attrs(pub_attrs, work_fields, pub_fields):
    out = []
    for f in work_fields:
        out.append(pub_attrs[pub_fields.index(f)] if f in pub_fields else None)
    return out

# ---------------------------------------------------------
# SIMPLE LINEAR KDTree
# ---------------------------------------------------------

class KDTree:
    def __init__(self, points):
        self.points = points

    def nearest(self, x, y):
        best_idx = None
        best_dist = float("inf")
        for px, py, idx in self.points:
            d = sqrt((px - x)**2 + (py - y)**2)
            if d < best_dist:
                best_dist = d
                best_idx = idx
        return best_idx, best_dist
# ---------------------------------------------------------
# GEOMETRY CHANGE CHECK (robust for polygons, polylines, points)
# ---------------------------------------------------------

def geometry_changed(wg, pg, tol):
    """
    Robust geometry comparison for points, polylines, polygons.
    Safely handles None vertices and polygon hole separators.
    Returns True only if geometry differs beyond tolerance.
    """

    if wg is None or pg is None:
        return False

    # Fast early exit: envelope mismatch → geometry changed
    if wg.extent.disjoint(pg.extent):
        return True

    w_parts = wg.getPart()
    p_parts = pg.getPart()

    # Different part count = geometry changed
    if len(w_parts) != len(p_parts):
        return True

    # Compare each part (rings / paths)
    for wp, pp in zip(w_parts, p_parts):

        # Skip polygon hole separators (None entries)
        if wp is None and pp is None:
            continue

        if wp is None or pp is None:
            return True

        # Clean None vertices inside parts
        wp_clean = [v for v in wp if v is not None]
        pp_clean = [v for v in pp if v is not None]

        # Different vertex count = geometry changed
        if len(wp_clean) != len(pp_clean):
            return True

        # Vertex-by-vertex comparison
        for wv, pv in zip(wp_clean, pp_clean):

            # Missing vertices = geometric difference
            if wv is None or pv is None:
                return True

            # Empty or corrupted vertices
            if (wv.X is None or wv.Y is None or
                pv.X is None or pv.Y is None):
                return True

            dx = abs(wv.X - pv.X)
            dy = abs(wv.Y - pv.Y)

            if dx > tol or dy > tol:
                return True

    return False


# ---------------------------------------------------------
# FEATURE LOADING
# ---------------------------------------------------------

def load_features(fc, debug_level):
    dbg(debug_level, 1, "[LOAD] " + fc)

    if not arcpy.Exists(fc):
        arcpy.AddWarning("Feature class missing: " + fc)
        return [], []

    desc = arcpy.Describe(fc)
    shape_field = desc.shapeFieldName
    skip_fields = {"shape_length", "shape_area"}

    allowed = []
    for f in arcpy.ListFields(fc):
        lname = f.name.lower()
        if lname == shape_field.lower(): continue
        if lname in ignore_fields: continue
        if lname in oid_like_fields: continue
        if lname in skip_fields: continue
        allowed.append(f.name)

    items = []
    try:
        with arcpy.da.SearchCursor(fc, allowed + ["SHAPE@"]) as cur:
            for row in cur:
                items.append((row[:-1], row[-1]))
    except:
        arcpy.AddError("Load failure on: " + fc)
        arcpy.AddError(traceback.format_exc())
        return [], []

    return items, allowed


# ---------------------------------------------------------
# LEVELID MODE (with robust geometry handling)
# ---------------------------------------------------------

def process_layer_with_levelid(
    work_fc, pub_fc, out_gdb, layer_name,
    tolerance_levelid,
    debug_level
):

    dbg(debug_level, 1, "[LEVELID] " + layer_name)

    work_feats, work_fields = load_features(work_fc, debug_level)
    pub_feats, pub_fields   = load_features(pub_fc, debug_level)

    if not work_feats and not pub_feats:
        return (layer_name, 0, 0, 0)

    widx = next((i for i,f in enumerate(work_fields) if f.lower()=="levelid"), None)
    pidx = next((i for i,f in enumerate(pub_fields) if f.lower()=="levelid"), None)

    if widx is None or pidx is None:
        arcpy.AddError("LEVELID missing: " + layer_name)
        return (layer_name, 0, 0, 0)

    # Group by LEVELID
    work_groups = {}
    for a,g in work_feats:
        work_groups.setdefault(a[widx], []).append((a,g))

    pub_groups = {}
    for a,g in pub_feats:
        pub_groups.setdefault(a[pidx], []).append((a,g))

    safe_name = sanitize_output_name(layer_name) + "_Changes"
    out_fc = out_gdb + "/" + safe_name

    if arcpy.Exists(out_fc):
        arcpy.Delete_management(out_fc)

    sp = arcpy.Describe(work_fc).spatialReference

    arcpy.CreateFeatureclass_management(
        out_gdb,
        safe_name,
        arcpy.Describe(work_fc).shapeType,
        None, "DISABLED", "DISABLED",
        sp
    )

    arcpy.AddField_management(out_fc, "OPERATION", "TEXT")
    for f in arcpy.ListFields(work_fc):
        if f.name in work_fields:
            arcpy.AddField_management(out_fc, f.name, f.type)

    out_fields = [f.name for f in arcpy.ListFields(out_fc)
                  if f.type not in ("OID", "Geometry")]
    insert_fields = out_fields + ["SHAPE@"]

    add_count = 0
    edit_count = 0
    del_count = 0

    with arcpy.da.InsertCursor(out_fc, insert_fields) as icur:

        levels = work_groups.keys() | pub_groups.keys()

        for lvl in levels:

            wlist = work_groups.get(lvl, [])
            plist = pub_groups.get(lvl, [])

            # DELETE-only case
            if not wlist and plist:
                for pa,pg in plist:
                    norm = normalize_pub_attrs(pa, work_fields, pub_fields)
                    row = [
                        "DELETE" if f=="OPERATION"
                        else norm[work_fields.index(f)] if f in work_fields
                        else None
                        for f in out_fields
                    ]
                    row.append(pg)
                    icur.insertRow(row)
                    del_count += 1
                continue

            # ADD-only case
            if wlist and not plist:
                for wa,wg in wlist:
                    row = [
                        "ADD" if f=="OPERATION"
                        else wa[work_fields.index(f)] if f in work_fields
                        else None
                        for f in out_fields
                    ]
                    row.append(wg)
                    icur.insertRow(row)
                    add_count += 1
                continue

            # Compute centroid KDTree for Public
            centroids = []
            for idx,(pa,pg) in enumerate(plist):
                c = get_centroid(pg)
                if c:
                    centroids.append((c[0],c[1],idx))

            tree = KDTree(centroids)
            used = set()

            # Process WORK features
            for wa,wg in wlist:

                wc = get_centroid(wg)
                if not wc:
                    continue

                p_idx, dist = tree.nearest(wc[0], wc[1])

                # ADD if no match or out-of-tolerance
                if p_idx is None or dist > tolerance_levelid or p_idx in used:
                    row = [
                        "ADD" if f=="OPERATION"
                        else wa[work_fields.index(f)] if f in work_fields
                        else None
                        for f in out_fields
                    ]
                    row.append(wg)
                    icur.insertRow(row)
                    add_count += 1
                    continue

                # Matched
                used.add(p_idx)

                pa, pg = plist[p_idx]
                norm = normalize_pub_attrs(pa, work_fields, pub_fields)

                changed = False
                diffs = {}

                for i,field in enumerate(work_fields):
                    wa_val = wa[i]
                    pa_val = norm[i]
                    if str(wa_val) != str(pa_val):
                        changed = True
                        diffs[field] = wa_val
                    else:
                        diffs[field] = None

                # *** SAFE GEOMETRY CHECK ***
                geom_changed = geometry_changed(wg, pg, tolerance_levelid)

                oper = (
                    "EDIT" if changed and geom_changed
                    else "ATTR_EDIT" if changed
                    else "GEOM_EDIT" if geom_changed
                    else None
                )

                if oper:
                    row = [
                        oper if f=="OPERATION"
                        else diffs[f] if f in diffs
                        else None
                        for f in out_fields
                    ]
                    row.append(wg)
                    icur.insertRow(row)
                    edit_count += 1

            # Remaining PUB = DELETE
            for idx,(pa,pg) in enumerate(plist):
                if idx not in used:
                    norm = normalize_pub_attrs(pa, work_fields, pub_fields)
                    row = [
                        "DELETE" if f=="OPERATION"
                        else norm[work_fields.index(f)] if f in work_fields
                        else None
                        for f in out_fields
                    ]
                    row.append(pg)
                    icur.insertRow(row)
                    del_count += 1

    return (layer_name, add_count, edit_count, del_count)
# ---------------------------------------------------------
# NON-LEVELID MODE (with robust geometry handling)
# ---------------------------------------------------------

def process_layer_without_levelid(
    work_fc, pub_fc, out_gdb, layer_name,
    tol_points, tol_lines, tol_polygons,
    debug_level
):

    dbg(debug_level, 1, "[NO LEVELID] Processing: " + layer_name)

    work_feats, work_fields = load_features(work_fc, debug_level)
    pub_feats, pub_fields   = load_features(pub_fc, debug_level)

    if not work_feats and not pub_feats:
        return (layer_name, 0, 0, 0)

    geom_type = arcpy.Describe(work_fc).shapeType.lower()

    # Geometry-type specific tolerance
    tol = (
        tol_points if geom_type == "point"
        else tol_lines if geom_type == "polyline"
        else tol_polygons
    )

    safe_name = sanitize_output_name(layer_name) + "_Changes"
    out_fc = f"{out_gdb}/{safe_name}"

    if arcpy.Exists(out_fc):
        arcpy.Delete_management(out_fc)

    sp = arcpy.Describe(work_fc).spatialReference

    # Create output feature class
    arcpy.CreateFeatureclass_management(
        out_gdb,
        safe_name,
        geom_type,
        None, "DISABLED", "DISABLED",
        sp
    )

    # Add fields
    arcpy.AddField_management(out_fc, "OPERATION", "TEXT")
    for f in arcpy.ListFields(work_fc):
        if f.name in work_fields:
            arcpy.AddField_management(out_fc, f.name, f.type)

    # Build InsertCursor field list
    out_fields = [
        f.name for f in arcpy.ListFields(out_fc)
        if f.type not in ("OID", "Geometry")
    ]
    insert_fields = out_fields + ["SHAPE@"]

    # Build centroid KDTree of Public database
    pub_centroids = []
    for idx, (pa, pg) in enumerate(pub_feats):
        c = get_centroid(pg)
        if c:
            pub_centroids.append((c[0], c[1], idx))

    tree = KDTree(pub_centroids)
    used = set()

    add_count = 0
    edit_count = 0
    del_count = 0

    with arcpy.da.InsertCursor(out_fc, insert_fields) as icur:

        for wa, wg in work_feats:

            wc = get_centroid(wg)
            if wc is None:
                continue

            p_idx, dist = tree.nearest(wc[0], wc[1])

            # ---------------------------------------------------------
            # ADD — no match or out of tolerance or already matched
            # ---------------------------------------------------------
            if p_idx is None or dist > tol or p_idx in used:
                row = [
                    "ADD" if f == "OPERATION"
                    else wa[work_fields.index(f)] if f in work_fields
                    else None
                    for f in out_fields
                ]
                row.append(wg)
                icur.insertRow(row)
                add_count += 1
                continue

            # ---------------------------------------------------------
            # MATCH FOUND
            # ---------------------------------------------------------
            used.add(p_idx)

            pa, pg = pub_feats[p_idx]
            norm = normalize_pub_attrs(pa, work_fields, pub_fields)

            # Attribute comparison
            diffs = {}
            changed = False
            for i, field in enumerate(work_fields):
                wa_val = wa[i]
                pa_val = norm[i]
                if str(wa_val) != str(pa_val):
                    changed = True
                    diffs[field] = wa_val
                else:
                    diffs[field] = None

            # Geometry comparison with safe polygon handling
            geom_changed = geometry_changed(wg, pg, tol)

            # Determine type of change
            oper = (
                "EDIT" if changed and geom_changed
                else "ATTR_EDIT" if changed
                else "GEOM_EDIT" if geom_changed
                else None
            )

            if oper:
                row = [
                    oper if f == "OPERATION"
                    else diffs[f] if f in diffs
                    else None
                    for f in out_fields
                ]
                row.append(wg)
                icur.insertRow(row)
                edit_count += 1

        # ---------------------------------------------------------
        # DELETE — public features not matched
        # ---------------------------------------------------------
        for idx, (pa, pg) in enumerate(pub_feats):
            if idx not in used:
                norm = normalize_pub_attrs(pa, work_fields, pub_fields)
                row = [
                    "DELETE" if f == "OPERATION"
                    else norm[work_fields.index(f)] if f in work_fields
                    else None
                    for f in out_fields
                ]
                row.append(pg)
                icur.insertRow(row)
                del_count += 1

    return (layer_name, add_count, edit_count, del_count)


# ---------------------------------------------------------
# MAIN RUNNER (with Audit layer preference + stable geometry handling)
# ---------------------------------------------------------

def layer_has_levelid(fc):
    fields = [f.name.lower() for f in arcpy.ListFields(fc)]
    return "levelid" in fields


def run_all_layers(
    work_gdb, pub_gdb, out_gdb,
    levelid_tol,
    point_tol, line_tol, polygon_tol,
    layer_filter,
    debug_level
):

    arcpy.env.workspace = work_gdb
    layers = arcpy.ListFeatureClasses()

    dbg(debug_level, 1, "[RUNNER] Workspace Loaded")

    normalized = {normalize_name(l): l for l in layers}

    # ---------------------------------------------------------
    # Layer filter (optional)
    # ---------------------------------------------------------
    if layer_filter:
        dbg(debug_level, 1, "[RUNNER] Filter: " + layer_filter)
        short = normalize_name(layer_filter)

        if short in normalized:
            layers = [normalized[short]]
        elif layer_filter in layers:
            layers = [layer_filter]
        else:
            arcpy.AddError("Layer not found: " + layer_filter)
            return []

    results = []

    # ---------------------------------------------------------
    # PROCESS EACH LAYER
    # ---------------------------------------------------------
    for layer_name in layers:

        dbg(debug_level, 1, "[RUNNER] Processing layer: " + layer_name)

        work_fc = f"{work_gdb}/{layer_name}"

        # ---------------------------------------------------------
        # PUBLIC DATA RESOLUTION (prefer _Audit)
        # ---------------------------------------------------------

        base_pub_fc  = f"{pub_gdb}/{layer_name}"
        audit_pub_fc = f"{pub_gdb}/{layer_name}_Audit"

        if arcpy.Exists(audit_pub_fc):
            pub_fc = audit_pub_fc
            dbg(debug_level, 1, f"[RUNNER] Using AUDIT layer: {layer_name}_Audit")
        elif arcpy.Exists(base_pub_fc):
            pub_fc = base_pub_fc
            dbg(debug_level, 1, f"[RUNNER] Using BASE layer: {layer_name}")
        else:
            arcpy.AddWarning(f"[WARN] Missing Public/Audit: {layer_name}")
            continue

        # ---------------------------------------------------------
        # RUN MODE
        # ---------------------------------------------------------
        try:
            if layer_has_levelid(work_fc) and layer_has_levelid(pub_fc):
                result = process_layer_with_levelid(
                    work_fc, pub_fc, out_gdb,
                    layer_name,
                    levelid_tol,
                    debug_level
                )
            else:
                result = process_layer_without_levelid(
                    work_fc, pub_fc, out_gdb,
                    layer_name,
                    point_tol,
                    line_tol,
                    polygon_tol,
                    debug_level
                )

            results.append(result)

        except Exception:
            arcpy.AddError("[ERROR] Failed layer: " + layer_name)
            arcpy.AddError(traceback.format_exc())

    dbg(debug_level, 1, "[RUNNER] Completed all layers")
    return results
