# ConsolidationToolbox.pyt
# ASCII-only Python Toolbox for ArcGIS Pro
#
# Compares the schemas of multiple feature classes and produces exactly
# three outputs:
#   1. Consolidated_TargetSchema.csv - the proposed schema (field name, alias,
#      type, length), built from fields present on at least the "Common
#      Field Presence Threshold" parameter's percentage (default 51%) of the
#      input feature classes -- not necessarily all of them, so the per-FC
#      True/False columns can genuinely be False for some rows. When sources disagree on type,
#      SourceTypes shows every type seen, slash-separated (e.g.
#      "Double/Integer"), while Type keeps the single promoted type actually
#      used for the field mapping -- numeric conflicts resolve to the
#      narrowest common type (see NUMERIC_RANK), not unconditionally to
#      Double. When sources disagree on length, Length
#      resolves to the longest length among the String sources (255 if none
#      report a usable length). HadConflict flags any field where sources
#      disagreed on type, length, and/or raw name. EverPopulated is False if
#      every source record for that field is null (and, by default, also
#      empty-string -- see the "Treat empty strings as unused?" parameter),
#      or "Unknown" if every contributing FC's record scan failed outright
#      (so a real "all null" never gets confused with "couldn't check").
#      Written as utf-8-sig so Excel renders non-ASCII characters correctly.
#   2. UniqueFields.csv - fields that fell below the presence threshold,
#      and are therefore NOT part of the proposed schema. One row per
#      (source FC, field) instance showing the exact name/type/length as it
#      exists in that source, plus one True/False column per input FC
#      showing every FC that field is present on. Also utf-8-sig.
#   3. consolidation.fieldmap - an arcpy.FieldMappings preset, built from the
#      proposed schema, importable directly into the ArcGIS Merge/Append
#      field mapping UI.

import arcpy
import os
import re
import csv
import math
from collections import defaultdict

# -------------------- Config --------------------
# Default for the "Common Field Presence Threshold (%)" tool parameter -- a
# field only needs to be present on this percentage of the input FCs (not
# necessarily all of them) to be included in the proposed schema; everything
# below the threshold goes to UniqueFields.csv instead. See
# split_common_and_unique() for how the actual (possibly user-overridden)
# value is turned into a required FC count.
DEFAULT_COMMON_THRESHOLD_PCT = 51

# System/geometry fields excluded by type (robust regardless of naming convention).
# Blob/Raster are excluded too: arcpy.da.SearchCursor can't read Blob fields at
# all, and including one would abort the EverPopulated scan for every other
# field on that same FC, not just the unreadable one.
EXCLUDE_FIELD_TYPES = {"OID", "Geometry", "GlobalID", "Blob", "Raster"}
# Esri-managed geometry bookkeeping fields that are ordinary Double fields (no
# distinguishing type), so they must be excluded by name instead. shape_leng is
# the DBF-truncated name shapefiles use for Shape_Length (10-char limit).
EXCLUDE_FIELD_NAMES_LOWER = {"shape_length", "shape_area", "shape_leng"}

# Fallback length for String fields when no source reports a usable (non-zero) length.
DEFAULT_STRING_LENGTH = 255

VALID_MERGE_RULES = ["First", "Last", "Join", "Min", "Max", "Mean", "Median", "Sum", "StDev", "Count"]
TEXT_ONLY_MERGE_RULES = {"Join"}
NUMERIC_ONLY_MERGE_RULES = {"Min", "Max", "Mean", "Median", "Sum", "StDev", "Count"}
NUMERIC_TYPES = {"Integer", "SmallInteger", "BigInteger", "Single", "Double"}
# Promotion order for numeric conflicts: narrowest first. A conflict resolves to
# whichever present type ranks highest, not unconditionally to Double -- e.g. a
# SmallInteger/Integer conflict now resolves to Integer, and a field that's the
# same numeric type on every source keeps that type instead of being forced to
# Double. Mixing in any floating type still promotes to that floating type,
# since an integer can't represent a fractional value.
NUMERIC_RANK = {"SmallInteger": 0, "Integer": 1, "BigInteger": 2, "Single": 3, "Double": 4}
TEMPORAL_TYPES = {"Date", "DateOnly", "TimeOnly", "TimestampOffset"}

def is_excluded_field(f):
    if f.type in EXCLUDE_FIELD_TYPES:
        return True
    return (f.name or "").lower() in EXCLUDE_FIELD_NAMES_LOWER

def effective_merge_rule(user_rule, field_type):
    # Esri restricts these rules by field type: Join is text-only, the
    # statistical rules are numeric-only. Anything outside the rule's
    # allowed type falls back to "First", which is valid for every type.
    if user_rule in NUMERIC_ONLY_MERGE_RULES and field_type not in NUMERIC_TYPES:
        return "First"
    if user_rule in TEXT_ONLY_MERGE_RULES and field_type != "String":
        return "First"
    return user_rule

DEFAULT_FIELDMAP_FILENAME = "consolidation.fieldmap"
DEFAULT_SCHEMA_CSV        = "Consolidated_TargetSchema.csv"
DEFAULT_UNIQUE_CSV        = "UniqueFields.csv"

# -------------------- Helpers --------------------
def normalize_name(n):
    """
    Strips everything but letters/digits so e.g. "Owner_Name" and "OwnerName"
    match as the same field across sources -- that cross-style matching is
    the whole point of this function, and is an intentional, accepted
    tradeoff (a rarer field like "ID_1" vs "ID1" could also collide).

    The one failure mode that's NOT an acceptable tradeoff: a field name
    made entirely of characters outside a-z0-9 (e.g. fully non-Latin text)
    would otherwise strip to "" and silently collide with every other such
    field across every source. Falling back to the raw lowercased name
    keeps those fields distinct from each other while still matching two
    sources that share the exact same raw name.
    """
    stripped = re.sub(r'[^a-z0-9]', '', (n or '').lower())
    if stripped:
        return stripped
    return "raw:" + (n or "").lower()

def build_field_index(fc_list, fields_by_fc):
    """
    Single pass over every source field, built once and reused everywhere a
    normalized-name lookup is needed (common/unique split, schema proposal,
    field mapping) instead of each of those re-scanning every field of every
    source on its own.
    Returns: normalized_name -> ordered list of (fc, arcpy Field), fc-list order
    preserved (matters for order-sensitive merge rules like First/Last), at
    most one entry per fc.
    """
    index = defaultdict(list)
    for fc in fc_list:
        seen_this_fc = {}  # norm -> field name already kept for this fc
        for f in fields_by_fc[fc]:
            if is_excluded_field(f):
                continue
            k = normalize_name(f.name)
            if k in seen_this_fc:
                arcpy.AddWarning(
                    "In {}, fields '{}' and '{}' both normalize to '{}'; keeping '{}' and dropping '{}' from comparison.".format(
                        fc, seen_this_fc[k], f.name, k, seen_this_fc[k], f.name))
                continue
            seen_this_fc[k] = f.name
            index[k].append((fc, f))
    return index

def split_common_and_unique(field_index, total_fc_count, threshold_pct):
    """
    Fields present on at least threshold_pct% of the input FCs feed the
    proposed schema and field mapping. Everything below that threshold is
    reported separately instead of being folded into the proposed schema. A
    field can now be "common" without being on every single input -- the
    per-FC True/False columns in the schema CSV reflect that gap instead of
    being constant True.
    """
    required_count = math.ceil(total_fc_count * threshold_pct / 100.0) if total_fc_count else 0
    common_index = {}
    unique_index = {}
    for k, entries in field_index.items():
        if len(entries) >= required_count:
            common_index[k] = entries
        else:
            unique_index[k] = entries
    return common_index, unique_index

def _most_common(values, default=None):
    """
    Mode of values. Ties go to whichever value appears first in the list
    (max() returns the first maximal element it scans), and values here are
    always passed in fc-input order -- so a tie resolves to the earliest-
    listed source's spelling, not an alphabetical/ASCII artifact. That also
    means the user can control tie-breaks just by reordering their inputs.
    Used for out_name/out_alias/suggested-domain picks, all of which boil
    down to the same operation.
    """
    if not values:
        return default
    return max(values, key=values.count)

def build_fc_labels(inputs):
    """
    Maps each full FC catalog path to a short display label -- the last two
    path components (e.g. "AVNGISDB02-GISProd.sde/DBO.ElecLightPole" instead
    of the full Favorites/SDE-connection path) -- for use as CSV column
    headers and the UniqueFields.csv SourceFC value. The full path is still
    what's used internally for every lookup and in warning messages (so
    there's always a path to act on); only the display text is shortened.

    If two inputs would produce the same short label (rare, but possible with
    same-named tables under differently-named connections), the colliding
    ones grow by one more path component each until they're unique again,
    so two distinct feature classes never share one CSV column header.
    """
    labels = {}
    taken = set()
    for fc in inputs:
        parts = [p for p in re.split(r'[\\/]+', fc.rstrip('\\/')) if p]
        n = min(2, len(parts)) or 1
        label = "/".join(parts[-n:]) if parts else fc
        while label in taken and n < len(parts):
            n += 1
            label = "/".join(parts[-n:])
        labels[fc] = label
        taken.add(label)
    return labels

def _mark_presence_columns(row, inputs, fc_labels, present_fcs):
    """Shared by write_schema_csv and write_unique_csv: one True/False column per
    input FC (keyed by its short display label) showing whether that FC is
    among present_fcs for this row's field."""
    for fc in inputs:
        row[fc_labels[fc]] = fc in present_fcs

def compute_ever_populated(common_index, treat_empty_as_null=True):
    """
    For each common field, check whether ANY contributing FC has at least
    one record where that field is non-null. When treat_empty_as_null is
    True (the default), an empty string also counts as unused -- a text
    field full of "" reads the same as one full of NULL. When False, only
    an actual database NULL counts; an explicitly-recorded empty string
    counts as populated.

    Returns dict: normKey -> True / False / None. None means "couldn't be
    determined" because every FC contributing this field failed its record
    scan (locked table, dropped connection, etc.) -- False is reserved for
    "every record we were actually able to check was null/empty", so the two
    cases are never conflated.

    Scans each FC once with a single SearchCursor covering every one of
    that FC's contributing field names (not once per field), short-
    circuiting a field as soon as one non-null value is found anywhere.
    This is a real table scan per input FC -- on very large feature classes
    this step will take noticeably longer than the schema-only comparison.
    """
    ever_populated = {}
    contributing_fcs = defaultdict(set)
    pending_by_fc = defaultdict(dict)  # fc -> {source_field_name: normKey}
    for key, entries in common_index.items():
        ever_populated[key] = False
        for fc, f in entries:
            pending_by_fc[fc][f.name] = key
            contributing_fcs[key].add(fc)

    scan_failed_fcs = set()
    for fc, field_map in pending_by_fc.items():
        field_names = list(field_map.keys())
        remaining = set(field_names)
        if not remaining:
            continue
        try:
            with arcpy.da.SearchCursor(fc, field_names) as cursor:
                for row in cursor:
                    if not remaining:
                        break
                    for fname, val in zip(field_names, row):
                        if fname not in remaining:
                            continue
                        is_populated = val is not None and (not treat_empty_as_null or val != "")
                        if is_populated:
                            ever_populated[field_map[fname]] = True
                            remaining.discard(fname)
        except RuntimeError as e:
            # arcpy.da cursor failures (locked table, schema issue, dropped
            # connection) are documented to raise RuntimeError -- an environment/
            # data problem worth a warning, not a reason to abort the whole run.
            # Any other exception type here would mean a bug in this function
            # itself, and is allowed to propagate and fail the run loudly instead
            # of being silently swallowed alongside legitimate data issues.
            arcpy.AddWarning("Could not scan records of {} to check for populated fields: {}".format(fc, e))
            scan_failed_fcs.add(fc)

    for key, fcs in contributing_fcs.items():
        if not ever_populated[key] and (fcs & scan_failed_fcs):
            ever_populated[key] = None
    return ever_populated

def _promote_type(types_set):
    types = set(types_set)
    if "String" in types:
        return "String"
    numeric_present = NUMERIC_TYPES & types
    if numeric_present:
        return max(numeric_present, key=lambda t: NUMERIC_RANK[t])
    temporal = types & TEMPORAL_TYPES
    if temporal:
        if len(temporal) == 1:
            return next(iter(temporal))
        if "TimeOnly" in temporal and (temporal - {"TimeOnly"}):
            # TimeOnly has no date component. Forcing it into "Date" alongside a
            # date-bearing temporal type would fabricate or silently drop data on
            # merge instead of representing it -- String is the only type that can
            # hold either kind of value without lying about what it means.
            return "String"
        return "Date"
    return sorted(types)[0] if types else "String"

def propose_schema(common_index):
    proposed = []
    for key, entries in sorted(common_index.items()):
        fields = [f for _, f in entries]
        names = [f.name for f in fields]
        aliases = [f.aliasName for f in fields if f.aliasName]
        out_name = _most_common(names)
        out_alias = _most_common(aliases, default=out_name)

        types_set = {f.type for f in fields}
        # Length is only a meaningful concept for String fields here -- two
        # sources that merely differ in numeric TYPE (e.g. Integer vs Double)
        # also differ in byte-size .length, which is noise already captured by
        # had_type_conflict, not a real length disagreement to flag separately.
        string_lengths_set = {f.length or 0 for f in fields if f.type == "String"}
        had_type_conflict = len(types_set) > 1
        had_length_conflict = len(string_lengths_set) > 1
        had_name_conflict = len(set(names)) > 1
        if had_type_conflict or had_length_conflict:
            detail = ", ".join("{}.{} ({}, len {})".format(fc, f.name, f.type, f.length or 0) for fc, f in entries)
            arcpy.AddWarning("Field '{}' differs across sources: {}".format(out_name, detail))
        if had_name_conflict:
            # Surfaced even when type/length agree -- otherwise the tie-break
            # pick in _most_common() would be silent and the user would never
            # know a spelling was chosen for them.
            arcpy.AddWarning(
                "Field '{}' is spelled differently across sources ({}); using '{}'.".format(
                    out_name, ", ".join(sorted(set(names))), out_name))
        temporal_conflict = types_set & TEMPORAL_TYPES
        numeric_present = types_set & NUMERIC_TYPES
        if len(temporal_conflict) > 1 and "TimeOnly" in temporal_conflict and (temporal_conflict - {"TimeOnly"}):
            arcpy.AddWarning(
                "Field '{}' mixes a time-only type with a date-bearing type across sources ({}); using 'String' "
                "in the proposed schema since neither temporal type can correctly represent both kinds of value.".format(
                    out_name, ", ".join(sorted(temporal_conflict))))
        elif len(temporal_conflict) > 1:
            arcpy.AddWarning(
                "Field '{}' has conflicting temporal types across sources ({}); using 'Date' in the proposed schema.".format(
                    out_name, ", ".join(sorted(temporal_conflict))))
        elif temporal_conflict and numeric_present:
            arcpy.AddWarning(
                "Field '{}' is temporal in some sources ({}) and numeric in others ({}); using 'Double' in the "
                "proposed schema, which cannot represent a date/time value.".format(
                    out_name, ", ".join(sorted(temporal_conflict)), ", ".join(sorted(numeric_present))))
        out_type = _promote_type(types_set)
        if "BigInteger" in numeric_present and (numeric_present & {"Single", "Double"}):
            # The hierarchy in NUMERIC_RANK still has to resolve this to a floating
            # type (an integer can't hold a fractional value), but that's the one
            # remaining case where promotion genuinely loses precision -- a
            # BigInteger value beyond ~2^53 isn't exactly representable as a float.
            arcpy.AddWarning(
                "Field '{}' mixes BigInteger with a floating-point type across sources ({}); promoting to '{}' "
                "can lose precision for BigInteger values beyond ~2^53.".format(
                    out_name, ", ".join(sorted(numeric_present)), out_type))
        if out_type == "String":
            # Only consider the actual String-typed sources for the longest length --
            # a conflicting non-String source's .length is a byte size, not a character
            # count, and must not be mixed into this max(). There may be none at all
            # (e.g. a TimeOnly/Date conflict promoted to String, see _promote_type) --
            # max() on an empty sequence would raise, so fall through to the default.
            out_len = max(string_lengths_set) if string_lengths_set else 0
            out_len = out_len or DEFAULT_STRING_LENGTH
        else:
            out_len = None

        # Type conflicts can't be resolved to "the longest" the way length conflicts
        # can (there's no ordering across unrelated types), so surface every type
        # that disagreed, slash-separated, alongside the single type actually used
        # for the field mapping.
        source_types_display = "/".join(sorted(types_set)) if had_type_conflict else out_type

        dom_names = [f.domain for f in fields if f.domain]
        dom_suggestion = _most_common(dom_names)

        proposed.append({
            "name": out_name,
            "alias": out_alias,
            "type": out_type,
            "sourceTypes": source_types_display,
            "length": out_len,
            "hadConflict": had_type_conflict or had_length_conflict or had_name_conflict,
            "suggestedDomain": dom_suggestion,
            "_normKey": key
        })
    proposed.sort(key=lambda x: x["name"].lower())
    return proposed

def build_fieldmap_content(proposed_schema, common_index, merge_rule):
    """
    Builds the FieldMappings object for the proposed schema and returns its
    exportToString() content as a plain string. Writing it to disk is the
    caller's job -- execute() stages it alongside the CSV outputs so all
    three can be committed with one atomic rename (see _atomic_write_all()).
    """
    fm = arcpy.FieldMappings()

    for tgt in proposed_schema:
        tgt_name = tgt["name"]
        matches = common_index.get(tgt["_normKey"], [])
        if not matches:
            continue

        fmo = arcpy.FieldMap()
        added = []
        for fc, f in matches:
            try:
                fmo.addInputField(fc, f.name)
                added.append((fc, f))
            except RuntimeError as e:
                # arcpy field-mapping errors (bad field reference, etc.) are
                # documented to raise RuntimeError -- worth a warning and moving
                # on to the next source, not aborting the whole field map. Any
                # other exception type would be a bug in this function itself
                # and is allowed to propagate and fail loudly instead.
                arcpy.AddWarning("Could not add {}.{} to field map for target '{}': {}".format(fc, f.name, tgt_name, e))
        if not added:
            arcpy.AddWarning("Skipping target field '{}': no source field could be added to its field map.".format(tgt_name))
            continue

        out_field = fmo.outputField
        out_field.name = tgt_name
        out_field.aliasName = tgt.get("alias", tgt_name)
        out_field.type = tgt["type"]
        if tgt["type"] == "String" and tgt.get("length"):
            out_field.length = tgt["length"]
        fmo.outputField = out_field

        tgt_rule = effective_merge_rule(merge_rule, tgt["type"])
        if tgt_rule != merge_rule:
            arcpy.AddWarning(
                "Merge rule '{}' is not valid for target field '{}' (type {}); using '{}' instead.".format(
                    merge_rule, tgt_name, tgt["type"], tgt_rule))
        fmo.mergeRule = tgt_rule
        fm.addFieldMap(fmo)

    return fm.exportToString()

def _write_text(path, content, encoding="utf-8"):
    with open(path, "w", encoding=encoding) as f:
        f.write(content)

def _write_dict_csv(path, fieldnames, rows):
    # utf-8-sig (BOM) so Excel on Windows -- the likely way these get opened --
    # correctly renders non-ASCII characters in field names/aliases/domains.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

def _atomic_write_all(write_specs):
    """
    write_specs: list of (final_path, write_fn), where write_fn(tmp_path)
    writes one file's complete content to tmp_path. Every file is written to
    a temporary path first; only once ALL of them succeed are they renamed
    into their final locations with os.replace(). If any write fails, every
    temp file attempted so far is removed and the exception propagates, so a
    failure partway through this tool's outputs never leaves a mix of this
    run's new files and a missing/stale one that looks like a complete result.
    """
    tmp_paths = []
    try:
        for final_path, write_fn in write_specs:
            tmp_path = final_path + ".tmp"
            tmp_paths.append(tmp_path)
            write_fn(tmp_path)
        for (final_path, _), tmp_path in zip(write_specs, tmp_paths):
            os.replace(tmp_path, final_path)
    except Exception:
        for tmp_path in tmp_paths:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        raise

def write_schema_csv(proposed_schema, common_index, ever_populated, inputs, fc_labels, path):
    """
    One row per proposed field:
      - Type: the single type actually used for the field mapping (promoted
        when sources disagree).
      - SourceTypes: "/"-joined list of every distinct type seen across
        sources when they disagreed (e.g. "Double/Integer"), otherwise just
        the one agreed type -- the type-conflict detail that used to live in
        conflicts.csv.
      - Length: resolved to the longest length among the actual String
        sources when lengths disagreed.
      - HadConflict: True if sources disagreed on type, length, and/or raw
        name for this field.
      - EverPopulated: True if any contributing FC has at least one record
        counted as populated for that field (see compute_ever_populated's
        treat_empty_as_null), False if every record across every source
        that could actually be checked is null (and empty, if that flag is
        on), or "Unknown" if every contributing FC's record scan failed.
      - One True/False column per input FC (header = that FC's short label,
        see build_fc_labels) showing whether that FC contributed this field.
        Since a field only needs to clear the "Common Field Presence
        Threshold" parameter's percentage of the inputs to make the proposed
        schema, not all of them, this can genuinely be False for some FCs.
    """
    fieldnames = ["FieldName","Alias","Type","SourceTypes","Length",
                  "SuggestedDomain","HadConflict","EverPopulated"] + [fc_labels[fc] for fc in inputs]
    rows = []
    for p in proposed_schema:
        present_fcs = {fc for fc, _ in common_index.get(p["_normKey"], [])}
        ev = ever_populated.get(p["_normKey"], False)
        row = {
            "FieldName": p.get("name"),
            "Alias": p.get("alias"),
            "Type": p.get("type"),
            "SourceTypes": p.get("sourceTypes"),
            "Length": p.get("length"),
            "SuggestedDomain": p.get("suggestedDomain"),
            "HadConflict": p.get("hadConflict"),
            "EverPopulated": "Unknown" if ev is None else ev
        }
        _mark_presence_columns(row, inputs, fc_labels, present_fcs)
        rows.append(row)
    _write_dict_csv(path, fieldnames, rows)

def write_unique_csv(unique_index, inputs, fc_labels, path):
    """
    Fields that fell below the "Common Field Presence Threshold" parameter's
    percentage. One row per (source FC, field) instance -- SourceFC shown as
    its short label, see build_fc_labels -- plus one True/False column per
    input FC showing every FC this field's normalized name is present on --
    so from any single row you can see both the exact field name/type/length
    as it exists in that one source, and the field's full presence footprint
    across all the inputs.
    """
    total_fc_count = len(inputs)
    fieldnames = ["SourceFC","FieldName","Alias","Type","Length","PresentOnFCCount","TotalFCCount"] + \
                 [fc_labels[fc] for fc in inputs]
    rows = []
    for k, entries in sorted(unique_index.items()):
        present_fcs = {fc for fc, _ in entries}
        present_count = len(entries)
        for fc, f in entries:
            row = {
                "SourceFC": fc_labels[fc],
                "FieldName": f.name,
                "Alias": f.aliasName,
                "Type": f.type,
                "Length": f.length or 0,
                "PresentOnFCCount": present_count,
                "TotalFCCount": total_fc_count
            }
            _mark_presence_columns(row, inputs, fc_labels, present_fcs)
            rows.append(row)
    _write_dict_csv(path, fieldnames, rows)

# -------------------- Toolbox --------------------
class Toolbox(object):
    def __init__(self):
        self.label = "Schema Consolidation Toolbox"
        self.alias = "schema_consolidation"
        self.tools = [ConsolidateSchemas]

class ConsolidateSchemas(object):
    def __init__(self):
        self.label = "Analyze & Propose Consolidated Schema (CSV + FieldMappings)"
        self.description = (
            "Compare multiple feature classes and propose a consolidated schema built from fields present "
            "on at least the Common Field Presence Threshold percentage of the inputs (default {:.0f}%). "
            "Exports: the proposed schema (CSV), fields that fell below that threshold (CSV), and a "
            "FieldMappings preset (.fieldmap) for the proposed schema.".format(DEFAULT_COMMON_THRESHOLD_PCT)
        )
        self.canRunInBackground = True

    def getParameterInfo(self):
        p_fcs = arcpy.Parameter(
            displayName="Input Feature Classes",
            name="in_fcs",
            datatype="GPValueTable",
            parameterType="Required",
            direction="Input"
        )
        p_fcs.columns = [["DEFeatureClass","Feature Class"]]

        p_out = arcpy.Parameter(
            displayName="Output Folder (CSV and .fieldmap)",
            name="out_folder",
            datatype="DEFolder",
            parameterType="Required",
            direction="Output"
        )

        p_export = arcpy.Parameter(
            displayName="Export FieldMappings preset (.fieldmap)?",
            name="export_fieldmap",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        p_export.value = True

        p_rule = arcpy.Parameter(
            displayName="FieldMappings merge rule",
            name="merge_rule",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_rule.value = "First"
        p_rule.filter.type = "ValueList"
        p_rule.filter.list = VALID_MERGE_RULES

        p_treat_empty = arcpy.Parameter(
            displayName="Treat empty strings as unused (for EverPopulated)?",
            name="treat_empty_as_null",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        p_treat_empty.value = True

        p_threshold = arcpy.Parameter(
            displayName="Common Field Presence Threshold (%)",
            name="common_threshold_pct",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input"
        )
        p_threshold.value = DEFAULT_COMMON_THRESHOLD_PCT
        p_threshold.filter.type = "Range"
        p_threshold.filter.list = [1, 100]

        return [p_fcs, p_out, p_export, p_rule, p_treat_empty, p_threshold]

    def updateParameters(self, params):
        p_export = params[2]
        p_rule = params[3]
        p_rule.enabled = bool(p_export.value) if p_export.value is not None else True
        return

    def updateMessages(self, params):
        p_fcs = params[0]
        if p_fcs.value is not None:
            rows = [str(row[0]) for row in p_fcs.value if row and row[0]]
            if 0 < len(rows) < 2:
                p_fcs.setWarningMessage("Provide at least two feature classes for a meaningful comparison.")
            elif len(rows) != len(set(rows)):
                p_fcs.setWarningMessage("The same feature class is listed more than once.")
        return

    def execute(self, params, messages):
        vt = params[0].value
        out_folder = params[1].valueAsText
        do_fieldmap = bool(params[2].value) if params[2].value is not None else True
        merge_rule = params[3].valueAsText or "First"
        treat_empty_as_null = bool(params[4].value) if params[4].value is not None else True
        threshold_pct = params[5].value if params[5].value is not None else DEFAULT_COMMON_THRESHOLD_PCT
        # The Range filter only protects the interactive GP dialog -- clamp here too
        # in case this tool is ever called directly from a script with an out-of-range value.
        threshold_pct = max(1, min(100, int(threshold_pct)))

        # 1) Inputs & Validation
        inputs = [str(row[0]) for row in vt if row and row[0]]
        if not inputs:
            raise RuntimeError("No input feature classes provided.")
        deduped = list(dict.fromkeys(inputs))
        if len(deduped) != len(inputs):
            arcpy.AddWarning("Duplicate feature classes in the input list were ignored; using each one once.")
            inputs = deduped
        if len(inputs) == 1:
            # Only a non-blocking UI hint in updateMessages() -- echo it here too so
            # it's visible in the run's own message log, not just a dismissible
            # pre-run dialog warning that's easy to click past.
            arcpy.AddWarning(
                "Only one input feature class was provided; the proposed schema will just mirror that "
                "FC's own schema, and UniqueFields.csv will be empty.")
        arcpy.AddMessage("Analyzing {} feature classes...".format(len(inputs)))
        fc_labels = build_fc_labels(inputs)
        try:
            os.makedirs(out_folder, exist_ok=True)
        except OSError as e:
            raise RuntimeError("Could not create output folder '{}': {}".format(out_folder, e))

        # 2) Harvest: one ListFields() pass per fc, then build a single
        # normalized-name index that every later step looks up against.
        # Fails clearly and names the offending FC rather than letting a
        # locked/moved/inaccessible input kill the run with a raw traceback --
        # and rather than silently dropping it, which would quietly change the
        # FC count the presence threshold is computed against.
        fields_by_fc = {}
        for fc in inputs:
            try:
                fields_by_fc[fc] = list(arcpy.ListFields(fc))
            except Exception as e:
                raise RuntimeError("Could not read fields from '{}': {}".format(fc, e))
        field_index = build_field_index(inputs, fields_by_fc)

        # 3) Split into fields meeting the presence threshold (feed the
        # proposed schema) vs fields that fell below it (reported, but
        # excluded from the proposed schema).
        common_index, unique_index = split_common_and_unique(field_index, len(inputs), threshold_pct)
        if not common_index:
            arcpy.AddWarning(
                "0 fields met the {}% presence threshold across these inputs -- the proposed schema, "
                "field map, and per-FC columns will all be empty. Every input field will be listed in "
                "UniqueFields.csv instead.".format(threshold_pct))

        # 4) Proposed schema, built only from fields meeting the threshold
        proposed = propose_schema(common_index)

        # 4b) Scan actual records to flag fields where every source is null/empty
        arcpy.AddMessage("Scanning records for populated vs. all-null fields...")
        ever_populated = compute_ever_populated(common_index, treat_empty_as_null=treat_empty_as_null)

        # 5) FieldMappings preset content (optional), built from the proposed
        # schema. Writing it to disk is deferred to the atomic-write step below,
        # alongside the two CSVs, so a failure partway through never leaves a
        # mix of this run's new outputs and a missing/stale file (see
        # _atomic_write_all()).
        fieldmap_path = None
        write_specs = [
            (os.path.join(out_folder, DEFAULT_SCHEMA_CSV),
                lambda p: write_schema_csv(proposed, common_index, ever_populated, inputs, fc_labels, p)),
            (os.path.join(out_folder, DEFAULT_UNIQUE_CSV),
                lambda p: write_unique_csv(unique_index, inputs, fc_labels, p)),
        ]
        if do_fieldmap:
            fieldmap_content = build_fieldmap_content(proposed, common_index, merge_rule)
            fieldmap_path = os.path.join(out_folder, DEFAULT_FIELDMAP_FILENAME)
            write_specs.append((fieldmap_path, lambda p: _write_text(p, fieldmap_content)))

        # 6) The exports -- committed together; see _atomic_write_all().
        try:
            _atomic_write_all(write_specs)
        except OSError as e:
            raise RuntimeError(
                "Failed to write outputs to '{}': {}. Check disk space and folder permissions.".format(
                    out_folder, e))

        arcpy.AddMessage("Proposed schema CSV: {}".format(os.path.join(out_folder, DEFAULT_SCHEMA_CSV)))
        arcpy.AddMessage("Unique fields CSV: {}".format(os.path.join(out_folder, DEFAULT_UNIQUE_CSV)))
        if fieldmap_path:
            arcpy.AddMessage("FieldMappings preset: {}".format(fieldmap_path))
        arcpy.AddMessage("Done.")
