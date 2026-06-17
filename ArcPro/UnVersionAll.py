import arcpy, fnmatch

# Set local variables
database = r"C:\Users\Connection.sde"
arcpy.DisconnectUser(database, "ALL")
arcpy.env.workspace = database

datasetName = arcpy.ListFeatureClasses()
filtered = fnmatch.filter(datasetName, '*')
tables = arcpy.ListTables()
ds_list = arcpy.ListDatasets()

# Delete selected #feature classes
for fc in filtered:
    print(fc)
    try:
        if arcpy.Exists(fc):
            print ("fc exists")
            arcpy.UnregisterAsVersioned_management(fc, "NO_KEEP_EDIT")
            #tables
            for table in tables:
                print ("table exists")  
                arcpy.UnregisterAsVersioned_management(table, "NO_KEEP_EDIT")

            #data sets
            for ds in ds_list:
                print ("ds exists")
                arcpy.UnregisterAsVersioned_management(ds, "NO_KEEP_EDIT")
    except (RuntimeError, TypeError, NameError,IndexError,IOError):
        print ("Error")
        continue
    print ("Completed")
