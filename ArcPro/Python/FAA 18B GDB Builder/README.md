# FAA 18B Geodatabase Builder

Builds a file geodatabase matching the AC 150/5300-18B (Change 1) Airports
GIS data standard: one feature dataset per Chapter 5 feature group, one
feature class per feature with its fields, and coded-value domains from
section 5.15 assigned to the fields that reference them.

## Files

- `build_18b_gdb.py` - the arcpy script. Run inside ArcGIS Pro (Python
  window or a script tool). Edit the `CONFIG` block at the top first
  (output folder, GDB name, spatial reference).
- `faa_18b_schema.json` - the schema data: 100 feature classes across 11
  feature datasets (946 fields total), 63 domains (62 from the AC plus one
  `YN_Boolean` convenience domain for `Boolean`-typed fields, which the AC
  doesn't itself define as a numbered enumeration). Edit this file, not the
  script, to correct anything below.

## Where this schema came from

Extracted by parsing the text of the published
`150-5300-18B-chg1-consolidated.pdf` (Chapter 5 feature tables + section
5.15 attribute enumerations) with a script, not transcribed by hand. The
PDF's table layout is not uniform - column wrapping varies row to row - so
the parser was iterated against the raw text and checked for: feature
class/domain counts matching the document's own table of contents (135
Chapter 5 feature sub-sections -> 100 unique feature classes after
collapsing the ones documented once per sub-type, like the 29 NAVAID
equipment types and 8 airport-control-point types that all share one real
feature class; 62 domains), no duplicate or empty domain codes, and no
runaway/merged fields. Several genuine defects in the source PDF itself
(not parsing bugs) were found and corrected by hand against the raw text:
a stray extra `)`, a few missing `(` before `Enumeration:`, one domain
table (`CodeSurfaceMaterial` on `WaterOperatingArea.surfaceMaterial`) with
no closing `)` at all, and two domains (`CodeClassAirspace`,
`CodeGridType`) that use a different value/definition layout than every
other domain table. `CodeAcqusitionType` keeps the AC's own misspelling
(missing the second "i") since that's the name on its enumeration table;
`acquisitionType` fields that reference the correctly-spelled
`CodeAcquisitionType` are aliased to it.

## Before using this for an actual submission

- **Spatial reference**: defaults to WGS84 (WKID 4326) in the script's
  `CONFIG` block purely as an always-valid placeholder. Section 3.7 of the
  AC expects a NAD83-based state plane or UTM projection appropriate to the
  airport - set `SPATIAL_REFERENCE_WKID` accordingly before building.
- **19 fields had no datatype at all in the source PDF** (confirmed by
  reading the raw text, not a parsing gap) and were assigned a type by a
  name/description heuristic (e.g. `pavementClassificationNumber` -> a
  number). Search `faa_18b_schema.json` for fields named in this list and
  double-check their `type`/`length` against the PDF yourself:
  `pavementClassificationNumber` (6 feature classes), `userFlag` (1),
  `setback`, `runwayEndDesignator`, `takeOffDistanceAvailable`,
  `takeOffRunwayAvailable`, `touchdownZoneSlope`, `touchdownZoneElevation`,
  `sequence`, `contourValue`, `referencePointEllipsoidHeight`,
  `magneticBearing`, `centroid`.
- Field lengths for `Enumeration`-typed (domain-backed) `TEXT` fields were
  sized to the longest code value in that domain plus a small buffer, not
  read from the AC (it doesn't specify one for these).
- The script fails if the target `.gdb` already exists (fresh build only,
  per how this was scoped) - it won't overwrite or merge into one you
  already built by hand.
- Domain/field creation runs one API call per code value and per field
  (~946 fields, ~2,000 domain codes) - expect it to take a few minutes
  inside ArcPro. Errors on individual fields/domains are collected and
  written to `faa_18b_build_errors.txt` in the output folder rather than
  aborting the whole run; check that file if the end-of-run error count is
  nonzero.
