# NOTAMS

Syncs NOTAM (Notice to Airmen) data into an Esri ArcGIS Online hosted
feature service, split across point/line/polygon layers.

## How it works

1. Load NOTAM GeoJSON (`coreNOTAMData` per feature — event info, NOTAM
   attributes, LOCAL/ICAO text translations).
2. Flatten each feature's attributes and classify its geometry as
   Point / LineString / Polygon (features with no parseable geometry
   fall back to the point layer).
3. Query each ArcGIS layer for existing records, keyed by `Notam_ID`.
4. Diff new vs. existing:
   - IDs not already present → `adds`
   - IDs already present → `updates` (matched to their `OBJECTID`)
   - Existing IDs missing from the new feed → marked `status = "Old"`
     (soft-delete, not removed)
5. Push `adds`/`updates`/old-status changes via ArcGIS REST
   `applyEdits`.

## Files

- **`Update Notams.py`** — standalone Python implementation. Reads
  NOTAM GeoJSON from a local `Untitled-1.json`, does the full
  query/diff/applyEdits cycle per layer, and checks each `applyEdits`
  response for errors (exits non-zero if any layer fails).

  Config (top of file) is placeholder values — fill in
  `PORTAL_BASE_URL`, `SERVICE_NAME`, `TOKEN`, and the three layer IDs
  before running.

- **`workato_transform.rb`** — Ruby port of the flatten/classify/diff
  logic for use as a Workato "Ruby code" step, for recipes that
  already have the HTTP request steps built (get NOTAM data, query
  existing records, `applyEdits`). Takes the raw NOTAM JSON plus each
  layer's existing `features` array as input, and outputs
  JSON-encoded `adds`/`updates`/`olds` strings per layer, ready to
  drop into the `applyEdits` HTTP step's body params. See the header
  comment in the file for the exact input/output field shapes.

## Known limitations

- No pagination on the existing-records query — layers with more
  records than the service's max page size will have older records
  fall out of the diff and get re-added instead of updated.
- No batching on `applyEdits` — a large refresh sends all adds/updates
  in a single call, which may exceed the service's batch edit limit.
- Nested (non-scalar) fields in `notamEvent`/`notam` are passed
  through as attribute values as-is; ArcGIS field writes expect
  scalars, so nested values may fail to write silently.
