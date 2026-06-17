import arcpy, fnmatch

# Set local variables
database = r'C:\Users\connection.sde'
arcpy.env.workspace = database

datasetName = arcpy.ListFeatureClasses()
filtered = fnmatch.filter(datasetName, '*')
tables = arcpy.ListTables()
ds_list = arcpy.ListDatasets()

# Delete selected #feature classes
for fc in filtered:
    try:
        if arcpy.Exists(fc):
            arcpy.RegisterAsVersioned_management(fc, "NO_EDITS_TO_BASE")
            #tables
            for table in tables:
                arcpy.RegisterAsVersioned_management(table, "NO_EDITS_TO_BASE")

            #data sets
            for ds in ds_list:
                arcpy.RegisterAsVersioned_management(ds, "NO_EDITS_TO_BASE")
    except (RuntimeError, TypeError, NameError,IndexError,IOError):
        continue
    print ("Completed")




