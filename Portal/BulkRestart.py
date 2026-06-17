from arcgis.gis import GIS

gis = GIS("Pro")

servers = gis.admin.servers.list()

for server in servers :
    print(server)
    folders = server.services.folders
    for folder1 in folders :
        print (folder1)
        services = server.services.list(folder=path)
        for item in services :
            print(item)
            print(item.status)
            if (str(item.status) ==  "{'configuredState': 'STARTED', 'realTimeState': 'STARTED'}"):
                item.stop()
                print("Stop")
                item.start()
                print(item.status)
print("Complete")
