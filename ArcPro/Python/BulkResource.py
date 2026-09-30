import arcpy

OLD_SERVER = arcpy.GetParameterAsText(0)
NEW_GISPROD = arcpy.GetParameterAsText(1)
NEW_GISWORK = arcpy.GetParameterAsText(2)
NEW_GISPUB = arcpy.GetParameterAsText(3)
NEW_GISDEV = arcpy.GetParameterAsText(4)
DRY_RUN = arcpy.GetParameterAsText(5) == "true"

NEW_SDE_BY_DATABASE = {
    "GISPROD": NEW_GISPROD,
    "GISWORK": NEW_GISWORK,
    "GISPUB": NEW_GISPUB,
    "GISDEV": NEW_GISDEV,
}

_diag_logged = False


def _target_sde(before):
    info = before.get("connection_info", {}) or {}
    instance = (info.get("instance") or info.get("server") or "").upper()
    database = (info.get("database") or before.get("dataset", "").split(".")[0]).upper()

    if OLD_SERVER.upper() not in instance:
        return None, database, None  # not on the old server, leave alone

    target = NEW_SDE_BY_DATABASE.get(database) or None
    if not target:
        return None, database, None

    # Match dict: the full flat connection_info (same shape Esri's own
    # examples use for current_connection_info), everything except the
    # masked password - a 2-key subset (instance+database) silently
    # matched nothing, so try the most complete dict that still avoids
    # round-tripping a password value that could never equal the layer's
    # real stored credential.
    match_info = {k: v for k, v in info.items() if k != "password"}

    return target, database, match_info


def _repoint(item, map_name, is_layer):
    # Some layer instances (broken/unsupported sources not caught by
    # isGroupLayer/isBasemapLayer) raise AttributeError on almost any
    # property access, not just connectionProperties - including .name.
    # Guard the whole block rather than each attribute one at a time.
    try:
        label = f"{map_name}/{item.name}"
    except AttributeError:
        label = f"{map_name}/<unsupported layer>"

    try:
        if is_layer and (item.isGroupLayer or item.isBasemapLayer):
            return "skipped"
        before = item.connectionProperties
    except AttributeError:
        return "skipped"
    if not before or before.get("workspace_factory") != "SDE":
        return "skipped"

    target, database, match_info = _target_sde(before)
    if not target:
        return "skipped"

    if DRY_RUN:
        arcpy.AddMessage(f"[DRY RUN] Would update {label} ({database}) -> {target}")
        return "updated"

    try:
        item.updateConnectionProperties(match_info, target)
    except Exception as e:
        arcpy.AddWarning(f"Could not update {label}: {e}")
        return "failed"

    after = item.connectionProperties
    if after == before:
        global _diag_logged
        if not _diag_logged:
            _diag_logged = True
            arcpy.AddWarning(
                f"DIAGNOSTIC (first no-op only) for {label}: "
                f"match_info sent={match_info!r} | new_target={target!r} | "
                f"before={before!r} | after={after!r}"
            )
        return "skipped"
    return "updated"


def main():
    if not OLD_SERVER:
        raise ValueError("Old server name is required.")
    if not any([NEW_GISPROD, NEW_GISWORK, NEW_GISPUB, NEW_GISDEV]):
        raise ValueError("At least one new .sde connection file is required.")

    aprx = arcpy.mp.ArcGISProject("CURRENT")
    counts = {"updated": 0, "skipped": 0, "failed": 0}

    for m in aprx.listMaps():
        for lyr in m.listLayers():
            counts[_repoint(lyr, m.name, is_layer=True)] += 1
        for tbl in m.listTables():
            counts[_repoint(tbl, m.name, is_layer=False)] += 1

    if not DRY_RUN:
        aprx.save()

    arcpy.AddMessage(
        f"Updated {counts['updated']}, skipped {counts['skipped']}, failed {counts['failed']}"
    )


if __name__ == "__main__":
    main()
