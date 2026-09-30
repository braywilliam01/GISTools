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

    # Identity-only match dict, nested under 'connection_info' per Esri's
    # documented updateConnectionProperties example (a flat dict - what
    # every prior attempt here used - matches nothing; the keys must sit
    # one level down). Deliberately excludes user/password/
    # authentication_mode/version since those vary layer-to-layer (users
    # connect through different accounts to the same database) and would
    # make the match too strict.
    connection_info = {}
    for key in ("server", "instance", "database", "dbclient", "db_connection_properties"):
        if info.get(key):
            connection_info[key] = info[key]
    match_info = {"connection_info": connection_info}

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
        # validate=False: default True silently no-ops the whole update if
        # arcpy can't validate new_connection_info, with no exception - the
        # likely reason every prior attempt here updated 0 items regardless
        # of the current-match dict shape. Safe here because _verify_new_sde
        # already confirmed each new .sde file actually connects, in main().
        item.updateConnectionProperties(match_info, target, validate=False)
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


SCRIPT_VERSION = "2026-09-30e-validate-false-with-preflight"


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
