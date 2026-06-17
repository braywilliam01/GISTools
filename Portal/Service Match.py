from arcgis.gis import GIS

# Log in to portal; prompts for PW automatically
gis = GIS("Pro")
print("Logged on")

# Layer ID to search for and its URL
find_id = '**ITEMID**'
find_url = gis.content.get(find_id).url
print("Found layer")

# Pull list of all web maps in portal
webmaps = gis.content.search('', item_type='map', max_items=-1)
print("Pulled webmaps")

# Return subset of map IDs which contain the service URL we're looking for
matches = [m.id for m in webmaps if str(m.get_data()).find(find_url) > -1]
print("Found web maps:")
for maps in matches:
    print(maps)

# Pull list of all web apps in portal
webapps = gis.content.search('', item_type='Application', max_items=-1)
print("Pulled webapps:")

# Create empty list to populate with results
app_list = []

# Check each web app for matches
for w in webapps:
    
    try:
        # Get the JSON as a string
        wdata = str(w.get_data())

        criteria = [
            wdata.find(find_url) > -1, # Check if URL is directly referenced
            any([wdata.find(i) > -1 for i in matches]) # Check if any matching maps are in app
        ]

        # If layer is referenced directly or indirectly, append app to list
        if any(criteria):
            app_list.append(w)
    
    # Some apps don't have data, so we'll just skip them if they throw a TypeError
    except:
        continue
    
for apps in app_list:
    print(apps)
