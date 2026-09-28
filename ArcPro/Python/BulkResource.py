import arcpy

OLD_SDE = arcpy.GetParameterAsText(0)
NEW_SDE = arcpy.GetParameterAsText(1)


def _repoint(item, label, is_layer):
    if is_layer and (item.isGroupLayer or item.isBasemapLayer):
        return "skipped"
    before = item.connectionProperties
    if not before:
        return "skipped"
    try:
        item.updateConnectionProperties(OLD_SDE, NEW_SDE)
    except Exception as e:
        arcpy.AddWarning(f"Could not update {label}: {e}")
        return "failed"
    return "updated" if item.connectionProperties != before else "skipped"


def main():
    if not OLD_SDE or not NEW_SDE:
        raise ValueError("Old and new .sde connection file paths are both required.")

    aprx = arcpy.mp.ArcGISProject("CURRENT")
    counts = {"updated": 0, "skipped": 0, "failed": 0}

    for m in aprx.listMaps():
        for lyr in m.listLayers():
            counts[_repoint(lyr, f"{m.name}/{lyr.name}", is_layer=True)] += 1
        for tbl in m.listTables():
            counts[_repoint(tbl, f"{m.name}/{tbl.name}", is_layer=False)] += 1

    aprx.save()
    arcpy.AddMessage(
        f"Updated {counts['updated']}, skipped {counts['skipped']}, failed {counts['failed']}"
    )


if __name__ == "__main__":
    main()
