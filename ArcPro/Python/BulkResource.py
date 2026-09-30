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


def _target_sde(before):
    info = before.get("connection_info", {}) or {}
    instance = (info.get("instance") or info.get("server") or "").upper()
    database = (info.get("database") or before.get("dataset", "").split(".")[0]).upper()

    if OLD_SERVER.upper() not in instance:
        return None, database, None  # not on the old server, leave alone

    target = NEW_SDE_BY_DATABASE.get(database) or None
    if not target:
        return None, database, None

    # Minimal match dict: avoid round-tripping the (masked) password that
    # Item.connectionProperties returns, since arcpy would never see it match
    # the layer's real stored credential and would silently skip the update.
    match_info = {"workspace_factory": before.get("workspace_factory")}
    connection_info = {}
    if info.get("instance"):
        connection_info["instance"] = info["instance"]
    if info.get("database"):
        connection_info["database"] = info["database"]
    if connection_info:
        match_info["connection_info"] = connection_info

    return target, database, match_info


def _repoint(item, label, is_layer):
    if is_layer and (item.isGroupLayer or item.isBasemapLayer):
        return "skipped"
    before = item.connectionProperties
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
    return "updated" if item.connectionProperties != before else "skipped"


def main():
    if not OLD_SERVER:
        raise ValueError("Old server name is required.")
    if not any([NEW_GISPROD, NEW_GISWORK, NEW_GISPUB, NEW_GISDEV]):
        raise ValueError("At least one new .sde connection file is required.")

    aprx = arcpy.mp.ArcGISProject("CURRENT")
    counts = {"updated": 0, "skipped": 0, "failed": 0}

    for m in aprx.listMaps():
        for lyr in m.listLayers():
            counts[_repoint(lyr, f"{m.name}/{lyr.name}", is_layer=True)] += 1
        for tbl in m.listTables():
            counts[_repoint(tbl, f"{m.name}/{tbl.name}", is_layer=False)] += 1

    if not DRY_RUN:
        aprx.save()

    arcpy.AddMessage(
        f"Updated {counts['updated']}, skipped {counts['skipped']}, failed {counts['failed']}"
    )


if __name__ == "__main__":
    main()
