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

SCRIPT_VERSION = "2026-09-30f-cim-based"


def _target_sde(before):
    info = before.get("connection_info", {}) or {}
    instance = (info.get("instance") or info.get("server") or "").upper()
    database = (info.get("database") or before.get("dataset", "").split(".")[0]).upper()

    if OLD_SERVER.upper() not in instance:
        return None, database  # not on the old server, leave alone

    return NEW_SDE_BY_DATABASE.get(database) or None, database


def _get_data_connection(cim_def):
    # Feature layers nest it under featureTable; standalone tables (and
    # possibly other layer types) expose it directly.
    try:
        return cim_def.featureTable.dataConnection
    except AttributeError:
        return cim_def.dataConnection


def _set_data_connection(cim_def, dc):
    try:
        cim_def.featureTable.dataConnection = dc
    except AttributeError:
        cim_def.dataConnection = dc


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

    target, database = _target_sde(before)
    if not target:
        return "skipped"

    if DRY_RUN:
        arcpy.AddMessage(f"[DRY RUN] Would update {label} ({database}) -> {target}")
        return "updated"

    # updateConnectionProperties is a confirmed Esri defect (BUG-000112574)
    # that silently no-ops for non-group layers in some Pro versions -
    # every attempt at fixing the match-dict shape here hit that same
    # silent failure regardless of shape. Edit the CIM definition directly
    # instead, the documented workaround: getDefinition/modify
    # dataConnection.workspaceConnectionString/setDefinition.
    try:
        cim_def = item.getDefinition("V2")
        dc = _get_data_connection(cim_def)
        old_conn_str = dc.workspaceConnectionString
        dc.workspaceConnectionString = f"DATABASE={target}"
        _set_data_connection(cim_def, dc)
        item.setDefinition(cim_def)
    except Exception as e:
        arcpy.AddWarning(f"Could not update {label}: {e}")
        return "failed"

    # Re-fetch a fresh CIM definition to verify - item.connectionProperties
    # is unreliable/stale after an in-place edit (root cause of the bug
    # above), so compare via getDefinition again instead.
    after_dc = _get_data_connection(item.getDefinition("V2"))
    if after_dc.workspaceConnectionString == old_conn_str:
        arcpy.AddWarning(
            f"No change detected for {label} after CIM update "
            f"(old connection string={old_conn_str!r})"
        )
        return "skipped"
    return "updated"


def _verify_new_sde(database, path):
    """Actually connect and list contents - arcpy.Exists() only checks the
    file is on disk, it doesn't prove the connection itself works. This
    runs before any per-layer update so a bad new connection file fails
    loudly here instead of causing a silent 0-updated run later."""
    try:
        old_workspace = arcpy.env.workspace
        arcpy.env.workspace = path
        arcpy.ListFeatureClasses()
        arcpy.env.workspace = old_workspace
    except Exception as e:
        raise ValueError(f"New connection file for {database} failed to validate: {path} -> {e}")


def main():
    arcpy.AddMessage(f"BulkResource.py version: {SCRIPT_VERSION}")
    if not OLD_SERVER:
        raise ValueError("Old server name is required.")
    if not any([NEW_GISPROD, NEW_GISWORK, NEW_GISPUB, NEW_GISDEV]):
        raise ValueError("At least one new .sde connection file is required.")

    for database, path in NEW_SDE_BY_DATABASE.items():
        if path:
            _verify_new_sde(database, path)
            arcpy.AddMessage(f"Verified new connection for {database}: {path}")

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
