import arcpy
import traceback

def script_tool(old_sde, new_sde):
    """Updates all layers in CURRENT APRX with detailed debug messages."""
    
    aprx = arcpy.mp.ArcGISProject("CURRENT")
    updated = 0

    arcpy.AddMessage("===== DEBUG START =====")
    arcpy.AddMessage("Old SDE path: " + old_sde)
    arcpy.AddMessage("New SDE path: " + new_sde)

    # Loop through all maps
    for m in aprx.listMaps():
        arcpy.AddMessage("\n--- Checking Map: " + m.name + " ---")

        # Include sublayers
        for lyr in m.listLayers(True):

            arcpy.AddMessage("\nLayer: " + lyr.name)
            arcpy.AddMessage("  Layer type: " + str(type(lyr)))

            # Check supports DATASOURCE
            supports_ds = lyr.supports("DATASOURCE")
            arcpy.AddMessage("  Supports DATASOURCE: " + str(supports_ds))

            # Try to read dataSource
            try:
                ds = lyr.dataSource
                arcpy.AddMessage("  dataSource: " + str(ds))
            except Exception as e:
                arcpy.AddMessage("  dataSource FAILED: " + str(e))
                continue

            # Try to read connectionProperties
            try:
                cp = lyr.connectionProperties
                arcpy.AddMessage("  connectionProperties:")
                for key, val in cp.items():
                    arcpy.AddMessage("    " + str(key) + ": " + str(val))
            except:
                arcpy.AddMessage("  No connectionProperties available")

            # Detect whether the layer uses the old SDE path
            if old_sde.lower() not in ds.lower():
                arcpy.AddMessage("  Old SDE NOT FOUND in dataSource → Skipping")
                continue

            arcpy.AddMessage("  Old SDE FOUND → Attempting updateConnectionProperties")

            try:
                result = lyr.updateConnectionProperties(old_sde, new_sde)
                updated += 1
                arcpy.AddMessage("  SUCCESS: Layer updated")
                arcpy.AddMessage("  updateConnectionProperties returned: " + str(result))

            except Exception as ex:
                arcpy.AddWarning("  ERROR updating layer: " + lyr.name)
                arcpy.AddWarning("  Exception: " + str(ex))
                arcpy.AddWarning("  Traceback: " + traceback.format_exc())

    aprx.save()
    arcpy.AddMessage("\n===== DEBUG END =====")
    return "Update complete. " + str(updated) + " layers updated."


if __name__ == "__main__":
    old_sde = arcpy.GetParameterAsText(0)
    new_sde = arcpy.GetParameterAsText(1)

    result = script_tool(old_sde, new_sde)
    arcpy.SetParameterAsText(2, result)
