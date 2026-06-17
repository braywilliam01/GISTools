import arcpy

# Set local variables
database = r'C\Users\Connection.sde'
arcpy.env.workspace = database

fcList = arcpy.ListFeatureClasses()

for fc in fcList:    ##  arcpy.ChangePrivileges_management(in_dataset, user, {view_privileges}, {edit_privileges})
  arcpy.ChangePrivileges_management(fc, "USER1", "GRANT", "GRANT")
  print fc

print "Complete"
