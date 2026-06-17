import arcpy
 
aprx = arcpy.mp.ArcGISProject("CURRENT")
 
# Old and new SDE paths
old_sde = r"C:\Users\Connection1.sde"
new_sde = r"C:\Users\Connection2.sde"
 
for m in aprx.listMaps():
    for lyr in m.listLayers():
        if lyr.supports("DATASOURCE"):
            print(f"Checking: {lyr.name} -> {lyr.dataSource}")
            try:
                lyr.updateConnectionProperties(old_sde, new_sde)
                print(f"Updated: {lyr.name}")
            except Exception as e:
                print(f"Skipped: {lyr.name} ({e})")
 
aprx.save()
print("Update complete!")
