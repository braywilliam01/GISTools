# Workato "Ruby code" step — translates NOTAM GeoJSON + existing ArcGIS
# records into adds/updates/mark-old payloads for applyEdits, per layer
# (point/line/polygon). Ported from Update Notams.py.
#
# Input fields to define on this step (map your existing HTTP datapills):
#   notam_json       Object          - response body of the "get NOTAM data" HTTP step
#                                       shape: { "data" => { "geojson" => [ {feature}, ... ] } }
#   existing_points   Array<Object>  - `features` array from the point-layer query step
#                                       each item: { "attributes" => { "Notam_ID" => ..., "OBJECTID" => ... } }
#   existing_lines    Array<Object>  - same shape, line layer
#   existing_polys    Array<Object>  - same shape, polygon layer
#
# Output:
#   { "points" => { "adds" => <json string>, "updates" => <json string>, "olds" => <json string> },
#     "lines"  => { ... },
#     "polys"  => { ... } }
#
# Each adds/updates/olds value is a JSON-encoded string, ready to drop
# straight into the applyEdits HTTP step's adds/updates body params.

require 'json'

PRIMARY_KEY = "Notam_ID"

def flatten_and_classify(feature)
  core = feature.dig("properties", "coreNOTAMData") || {}
  notam_event  = core["notamEvent"] || {}
  notam        = core["notam"] || {}
  translations = core["notamTranslation"] || []

  local_text = nil
  icao_text  = nil

  translations.each do |t|
    case t["type"]
    when "LOCAL_FORMAT" then local_text = t["simpleText"]
    when "ICAO"          then icao_text  = t["formattedText"]
    end
  end

  attrs = {}
  notam_event.each { |k, v| attrs["event_#{k}"] = v }
  notam.each { |k, v| attrs[k] = v }
  attrs["localText"] = local_text
  attrs["icaoText"]  = icao_text
  attrs[PRIMARY_KEY] = notam["id"]

  geom_type = nil
  geometry  = nil

  g = feature.dig("geometry", "geometries", 0)
  if g
    geom_type = g["type"]
    case geom_type
    when "Point"
      lon, lat = g["coordinates"]
      geometry = { "x" => lon, "y" => lat, "spatialReference" => { "wkid" => 4326 } }
    when "LineString"
      geometry = { "paths" => [g["coordinates"]], "spatialReference" => { "wkid" => 4326 } }
    when "Polygon"
      geometry = { "rings" => g["coordinates"], "spatialReference" => { "wkid" => 4326 } }
    end
  end

  [geom_type, attrs, geometry]
end

def split_by_geometry(notam_json)
  pts, lines, polys = [], [], []
  features = notam_json.dig("data", "geojson") || []

  features.each do |f|
    geom_type, attrs, geom = flatten_and_classify(f)
    pkg = { "attributes" => attrs, "geometry" => geom }

    case geom_type
    when "Point"      then pts   << pkg
    when "LineString" then lines << pkg
    when "Polygon"    then polys << pkg
    else                   pts   << pkg  # no/unrecognized geometry
    end
  end

  [pts, lines, polys]
end

def existing_id_map(existing_features)
  mapping = {}
  (existing_features || []).each do |feat|
    attrs = feat["attributes"] || {}
    nid = attrs[PRIMARY_KEY]
    mapping[nid] = attrs["OBJECTID"] unless nid.nil?
  end
  mapping
end

def build_edit_batches(new_features, existing_mapping)
  adds, updates, new_ids = [], [], []

  new_features.each do |feat|
    nid = feat["attributes"][PRIMARY_KEY]
    new_ids << nid

    if existing_mapping.key?(nid)
      feat["attributes"]["OBJECTID"] = existing_mapping[nid]
      updates << feat
    else
      adds << feat
    end
  end

  [adds, updates, new_ids]
end

def build_old_status_updates(existing_mapping, new_ids)
  existing_mapping.each_with_object([]) do |(nid, oid), olds|
    next if new_ids.include?(nid)
    olds << { "attributes" => { "OBJECTID" => oid, "status" => "Old" } }
  end
end

def build_layer_payload(new_features, existing_features)
  existing_mapping        = existing_id_map(existing_features)
  adds, updates, new_ids  = build_edit_batches(new_features, existing_mapping)
  olds                    = build_old_status_updates(existing_mapping, new_ids)

  {
    "adds"    => adds.to_json,
    "updates" => updates.to_json,
    "olds"    => olds.to_json
  }
end

pts, lines, polys = split_by_geometry(input['notam_json'])

output = {
  "points" => build_layer_payload(pts, input['existing_points']),
  "lines"  => build_layer_payload(lines, input['existing_lines']),
  "polys"  => build_layer_payload(polys, input['existing_polys'])
}
