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
        return None, database  # not on the old server, leave alone

    return NEW_SDE_BY_DATABASE.get(database) or None, database


def _repoint(item, label, is_layer):
    if is_layer and (item.isGroupLayer or item.isBasemapLayer):
        return "skipped"
    before = item.connectionProperties
    if not before or before.get("workspace_factory") != "SDE":
        return "skipped"

    target, database = _target_sde(before)
    if not target:
        return "skipped"

    if DRY_RUN:
        arcpy.AddMessage(f"[DRY RUN] Would update {label} ({database}) -> {target}")
        return "updated"

    try:
        item.updateConnectionProperties(before, target)
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
