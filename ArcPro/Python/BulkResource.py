import arcpy

OLD_SERVER = arcpy.GetParameterAsText(0)
OLD_GISPROD = arcpy.GetParameterAsText(1)
OLD_GISWORK = arcpy.GetParameterAsText(2)
OLD_GISPUB = arcpy.GetParameterAsText(3)
OLD_GISDEV = arcpy.GetParameterAsText(4)
NEW_GISPROD = arcpy.GetParameterAsText(5)
NEW_GISWORK = arcpy.GetParameterAsText(6)
NEW_GISPUB = arcpy.GetParameterAsText(7)
NEW_GISDEV = arcpy.GetParameterAsText(8)
DRY_RUN = arcpy.GetParameterAsText(9) == "true"

SDE_PAIRS_BY_DATABASE = {
    "GISPROD": (OLD_GISPROD, NEW_GISPROD),
    "GISWORK": (OLD_GISWORK, NEW_GISWORK),
    "GISPUB": (OLD_GISPUB, NEW_GISPUB),
    "GISDEV": (OLD_GISDEV, NEW_GISDEV),
}

_diag_logged = False


def _target_sde(before):
    info = before.get("connection_info", {}) or {}
    instance = (info.get("instance") or info.get("server") or "").upper()
    database = (info.get("database") or before.get("dataset", "").split(".")[0]).upper()

    if OLD_SERVER.upper() not in instance:
        return None, None, database  # not on the old server, leave alone

    old_sde, new_sde = SDE_PAIRS_BY_DATABASE.get(database, (None, None))
    if not old_sde or not new_sde:
        return None, None, database  # no old/new connection file configured yet

    return old_sde, new_sde, database


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

    old_sde, new_sde, database = _target_sde(before)
    if not old_sde:
        return "skipped"

    if DRY_RUN:
        arcpy.AddMessage(f"[DRY RUN] Would update {label} ({database}) -> {new_sde}")
        return "updated"

    try:
        item.updateConnectionProperties(old_sde, new_sde)
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
                f"old_sde={old_sde!r} | new_sde={new_sde!r} | "
                f"before={before!r} | after={after!r}"
            )
        return "skipped"
    return "updated"


def main():
    if not OLD_SERVER:
        raise ValueError("Old server name is required.")
    if not any([OLD_GISPROD, OLD_GISWORK, OLD_GISPUB, OLD_GISDEV]):
        raise ValueError("At least one old .sde connection file is required.")
    if not any([NEW_GISPROD, NEW_GISWORK, NEW_GISPUB, NEW_GISDEV]):
        raise ValueError("At least one new .sde connection file is required.")

    for db, (old_sde, new_sde) in SDE_PAIRS_BY_DATABASE.items():
        if bool(old_sde) != bool(new_sde):
            arcpy.AddWarning(f"{db}: only one of old/new connection file was set - skipping this database.")

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
