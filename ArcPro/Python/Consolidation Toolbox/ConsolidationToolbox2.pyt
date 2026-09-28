# ConsolidationToolbox.pyt
# ASCII-only Python Toolbox for ArcGIS Pro
# Bundles Sections 1-7:
#  - Inputs & Validation
#  - Schema Harvest (fields + geometry/SR + optional domains)
#  - N-way Comparison (common/conflicts)
#  - Proposed Target Schema (nullable policy)
#  - Traceability (target -> sources)
#  - FieldMappings preset (.fieldmap) + mapping CSV (optional)
#  - CSV Reports for all summary parts (incl. Consolidated_TargetSchema.csv)

import arcpy
import os
import re
import csv
import json
from collections import defaultdict

# -------------------- Config --------------------
# System/geometry fields excluded by type (robust regardless of naming convention).
EXCLUDE_FIELD_TYPES = {"OID", "Geometry", "GlobalID"}
# Esri-managed geometry bookkeeping fields that are ordinary Double fields (no
# distinguishing type), so they must be excluded by name instead.
EXCLUDE_FIELD_NAMES_LOWER = {"shape_length", "shape_area"}

VALID_MERGE_RULES = ["First", "Last", "Join", "Min", "Max", "Mean", "Median", "Sum", "StDev", "Count"]
TEXT_ONLY_MERGE_RULES = {"Join"}
NUMERIC_ONLY_MERGE_RULES = {"Min", "Max", "Mean", "Median", "Sum", "StDev", "Count"}
NUMERIC_TYPES = {"Integer", "SmallInteger", "BigInteger", "Single", "Double"}
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
DEFAULT_MAPPING_CSV       = "Consolidation_FieldMappings.csv"
DEFAULT_SCHEMA_CSV        = "Consolidated_TargetSchema.csv"
DEFAULT_INPUTS_CSV        = "inputs.csv"
DEFAULT_GEOMETRY_CSV      = "geometry.csv"
DEFAULT_COMMON_CSV        = "common_fields.csv"
DEFAULT_CONFLICTS_CSV     = "conflicts.csv"
DEFAULT_TRACE_CSV         = "traceability.csv"
DEFAULT_META_CSV          = "metadata.csv"
DEFAULT_DOMAINS_CSV       = "domains.csv"

# -------------------- Helpers --------------------
def normalize_name(n):
    return re.sub(r'[^a-z0-9]', '', (n or '').lower())

def describe_fc(fc):
    """Single arcpy.Describe() call per fc, reused for both geometry info and workspace resolution."""
    d = arcpy.Describe(fc)
    sr = getattr(d, "spatialReference", None)
    geom = {
        "shapeType": getattr(d, "shapeType", None),
        "hasZ": getattr(d, "hasZ", False),
        "hasM": getattr(d, "hasM", False),
        "srName": sr.name if sr else None,
        "srWKID": sr.factoryCode if sr else None
    }
    # d.workspace is a Describe object for the fc's true owning workspace,
    # resolved by Esri regardless of feature-dataset nesting -- no manual
    # path-string walking needed (and no second Describe() call).
    try:
        workspace = d.workspace.catalogPath
    except Exception as e:
        arcpy.AddWarning("Could not resolve true workspace root for {}: {}".format(fc, e))
        workspace = d.path
    return geom, workspace

def list_domains(workspace):
    doms = {}
    try:
        for dom in arcpy.da.ListDomains(workspace):
            entry = {"type": dom.domainType}
            if dom.domainType == "CodedValue":
                entry["codedValues"] = dict(dom.codedValues)
            elif dom.domainType == "Range":
                entry["range"] = list(dom.range)
            entry["splitPolicy"] = dom.splitPolicy
            entry["mergePolicy"] = dom.mergePolicy
            entry["owner"] = getattr(dom, "owner", None)
            doms[dom.name] = entry
    except Exception as e:
        arcpy.AddWarning("Could not read domains from {}: {}".format(workspace, e))
    return doms

def build_field_index(fc_list, fields_by_fc):
    """
    Single pass over every source field, built once and reused everywhere a
    normalized-name lookup is needed (comparison, schema proposal,
    traceability, field mapping) instead of each of those re-scanning every
    field of every source on its own.
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

def compare_n_way(fc_list, field_index):
    total = len(fc_list)
    common_norm = sorted(k for k, entries in field_index.items() if len(entries) == total)
    conflicts_norm = []
    for k in common_norm:
        entries = field_index[k]
        types = {f.type for _, f in entries}
        lengths = {f.length or 0 for _, f in entries}
        if len(types) > 1 or len(lengths) > 1:
            conflicts_norm.append(k)
    return common_norm, conflicts_norm

def _promote_type(types_set):
    types = set(types_set)
    if "String" in types:
        return "String"
    if {"Integer","SmallInteger","BigInteger","Single","Double"} & types:
        return "Double"
    temporal = types & TEMPORAL_TYPES
    if temporal:
        return next(iter(temporal)) if len(temporal) == 1 else "Date"
    return sorted(types)[0] if types else "String"

def propose_schema(field_index, keep_nullable=True):
    proposed = []
    for key, entries in field_index.items():
        fields = [f for _, f in entries]
        names = [f.name for f in fields]
        aliases = [f.aliasName for f in fields if f.aliasName]
        out_name = max(sorted(set(names)), key=names.count)
        out_alias = max(sorted(set(aliases)), key=aliases.count) if aliases else out_name

        types_set = {f.type for f in fields}
        temporal_conflict = types_set & TEMPORAL_TYPES
        numeric_present = types_set & NUMERIC_TYPES
        if len(temporal_conflict) > 1:
            arcpy.AddWarning(
                "Field '{}' has conflicting temporal types across sources ({}); using 'Date' in the proposed schema.".format(
                    out_name, ", ".join(sorted(temporal_conflict))))
        elif temporal_conflict and numeric_present:
            arcpy.AddWarning(
                "Field '{}' is temporal in some sources ({}) and numeric in others ({}); using 'Double' in the "
                "proposed schema, which cannot represent a date/time value.".format(
                    out_name, ", ".join(sorted(temporal_conflict)), ", ".join(sorted(numeric_present))))
        out_type = _promote_type(types_set)
        if out_type == "String":
            out_len = max((f.length or 0) for f in fields) or 50
        else:
            out_len = None

        dom_names = [f.domain for f in fields if f.domain]
        dom_suggestion = max(sorted(set(dom_names)), key=dom_names.count) if dom_names else None

        proposed.append({
            "name": out_name,
            "alias": out_alias,
            "type": out_type,
            "length": out_len,
            "nullable": bool(keep_nullable),
            "required": False,
            "suggestedDomain": dom_suggestion,
            "_normKey": key
        })
    proposed.sort(key=lambda x: x["name"].lower())
    return proposed

def build_traceability(proposed_schema, field_index):
    return {
        t["name"]: [{"fc": fc, "field": f.name} for fc, f in field_index.get(t["_normKey"], [])]
        for t in proposed_schema
    }

def build_fieldmappings_and_csv(proposed_schema, field_index, out_folder, fieldmap_filename, mapping_csv_filename, merge_rule):
    os.makedirs(out_folder, exist_ok=True)
    fieldmap_path = os.path.join(out_folder, fieldmap_filename)
    mapping_csv_path = os.path.join(out_folder, mapping_csv_filename)

    fm = arcpy.FieldMappings()
    mapping_rows = []

    for tgt in proposed_schema:
        tgt_name = tgt["name"]
        matches = field_index.get(tgt["_normKey"], [])
        if not matches:
            continue

        fmo = arcpy.FieldMap()
        added = []
        for fc, f in matches:
            try:
                fmo.addInputField(fc, f.name)
                added.append((fc, f))
            except Exception as e:
                arcpy.AddWarning("Could not add {}.{} to field map for target '{}': {}".format(fc, f.name, tgt_name, e))
        if not added:
            arcpy.AddWarning("Skipping target field '{}': no source field could be added to its field map.".format(tgt_name))
            continue
        matches = added

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

        for fc, f in matches:
            mapping_rows.append({
                "TargetField": tgt_name,
                "TargetType": tgt.get("type"),
                "TargetLength": tgt.get("length"),
                "MergeRule": tgt_rule,
                "SourceFC": fc,
                "SourceField": f.name,
                "SourceType": f.type,
                "SourceDomain": f.domain or None
            })

    with open(fieldmap_path, "w", encoding="utf-8") as f:
        f.write(fm.exportToString())

    headers = ["TargetField","TargetType","TargetLength","MergeRule","SourceFC","SourceField","SourceType","SourceDomain"]
    with open(mapping_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(mapping_rows)

    return fieldmap_path, mapping_csv_path

def _write_csv(out_folder, filename, header, rows):
    with open(os.path.join(out_folder, filename), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

def _write_dict_csv(out_folder, filename, fieldnames, rows):
    with open(os.path.join(out_folder, filename), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

def write_summary_csvs(summary, out_folder):
    os.makedirs(out_folder, exist_ok=True)

    _write_csv(out_folder, DEFAULT_INPUTS_CSV, ["InputFC"],
               [[fc] for fc in summary.get("inputs", [])])

    _write_csv(out_folder, DEFAULT_GEOMETRY_CSV,
               ["FC","shapeType","hasZ","hasM","srName","srWKID"],
               [[fc, gi.get("shapeType"), gi.get("hasZ"), gi.get("hasM"), gi.get("srName"), gi.get("srWKID")]
                for fc, gi in summary.get("geometry", {}).items()])

    _write_csv(out_folder, DEFAULT_COMMON_CSV, ["normalizedName"],
               [[name] for name in summary.get("commonFields(normalized)", [])])

    _write_dict_csv(out_folder, DEFAULT_CONFLICTS_CSV,
        ["normalizedName","SourceFC","SourceField","Type","Length"],
        summary.get("conflictDetails", []))

    _write_dict_csv(out_folder, DEFAULT_SCHEMA_CSV,
        ["FieldName","Alias","Type","Length","Nullable","Required","SuggestedDomain"],
        [{
            "FieldName": p.get("name"),
            "Alias": p.get("alias"),
            "Type": p.get("type"),
            "Length": p.get("length"),
            "Nullable": p.get("nullable"),
            "Required": p.get("required"),
            "SuggestedDomain": p.get("suggestedDomain")
        } for p in summary.get("proposedSchema(nullable)", [])])

    domain_rows = []
    for ws, doms in summary.get("domainsByWorkspace", {}).items():
        for dom_name, entry in doms.items():
            detail = entry.get("codedValues") if entry.get("type") == "CodedValue" else entry.get("range")
            domain_rows.append({
                "Workspace": ws,
                "DomainName": dom_name,
                "Type": entry.get("type"),
                "SplitPolicy": entry.get("splitPolicy"),
                "MergePolicy": entry.get("mergePolicy"),
                "Owner": entry.get("owner"),
                "Detail": json.dumps(detail, default=str) if detail is not None else None
            })
    _write_dict_csv(out_folder, DEFAULT_DOMAINS_CSV,
        ["Workspace","DomainName","Type","SplitPolicy","MergePolicy","Owner","Detail"], domain_rows)

    trace_rows = []
    for tgt, sources in summary.get("traceability", {}).items():
        if not sources:
            trace_rows.append({"TargetField": tgt, "SourceFC": None, "SourceField": None})
        else:
            trace_rows.extend(
                {"TargetField": tgt, "SourceFC": s.get("fc"), "SourceField": s.get("field")} for s in sources)
    _write_dict_csv(out_folder, DEFAULT_TRACE_CSV, ["TargetField","SourceFC","SourceField"], trace_rows)

    _write_csv(out_folder, DEFAULT_META_CSV, ["key","value"], [
        ["fieldMappingsPreset", summary.get("fieldMappingsPreset")],
        ["targetSchemaLabel", summary.get("targetSchemaLabel")]
    ])

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
            "Compare multiple feature classes, harvest domains, propose a nullable consolidated schema, "
            "generate traceability, export a FieldMappings preset (.fieldmap), and write CSV reports."
        )
        self.canRunInBackground = True

    def getParameterInfo(self):
        p_workspaces = arcpy.Parameter(
            displayName="SDE Workspaces (optional)",
            name="in_workspaces",
            datatype="DEWorkspace",
            parameterType="Optional",
            direction="Input"
        )
        p_workspaces.multiValue = True

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

        p_label = arcpy.Parameter(
            displayName="Target Schema Label",
            name="target_label",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_label.value = "Consolidated_Target"

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

        return [p_workspaces, p_fcs, p_out, p_label, p_export, p_rule]

    def updateParameters(self, params):
        p_export = params[4]
        p_rule = params[5]
        p_rule.enabled = bool(p_export.value) if p_export.value is not None else True
        return

    def updateMessages(self, params):
        p_fcs = params[1]
        if p_fcs.value is not None:
            rows = [str(row[0]) for row in p_fcs.value if row and row[0]]
            if 0 < len(rows) < 2:
                p_fcs.setWarningMessage("Provide at least two feature classes for a meaningful comparison.")
            elif len(rows) != len(set(rows)):
                p_fcs.setWarningMessage("The same feature class is listed more than once.")
        return

    def execute(self, params, messages):
        vt = params[1].value
        out_folder = params[2].valueAsText
        target_label = params[3].valueAsText or "Consolidated_Target"
        do_fieldmap = bool(params[4].value) if params[4].value is not None else True
        merge_rule = params[5].valueAsText or "First"
        extra_workspaces = [str(w) for w in params[0].value] if params[0].value else []

        # 1) Inputs & Validation
        inputs = [str(row[0]) for row in vt]
        if not inputs:
            raise RuntimeError("No input feature classes provided.")
        deduped = list(dict.fromkeys(inputs))
        if len(deduped) != len(inputs):
            arcpy.AddWarning("Duplicate feature classes in the input list were ignored; using each one once.")
            inputs = deduped
        arcpy.AddMessage("Analyzing {} feature classes...".format(len(inputs)))

        # 2) Harvest: one ListFields()/Describe() pass per fc, then build a single
        # normalized-name index that every later step looks up against (O(1))
        # instead of each re-scanning every field of every source on its own.
        fields_by_fc = {fc: list(arcpy.ListFields(fc)) for fc in inputs}
        field_index = build_field_index(inputs, fields_by_fc)

        geom_by_fc = {}
        domains_by_ws = {}
        for fc in inputs:
            geom, ws = describe_fc(fc)
            geom_by_fc[fc] = geom
            if ws not in domains_by_ws:
                domains_by_ws[ws] = list_domains(ws)
        for ws in extra_workspaces:
            if ws not in domains_by_ws:
                arcpy.AddMessage("Harvesting domains from additional workspace: {}".format(ws))
                domains_by_ws[ws] = list_domains(ws)

        # 3) N-way comparison
        common_norm, conflicts_norm = compare_n_way(inputs, field_index)
        conflict_details = [
            {"normalizedName": k, "SourceFC": fc, "SourceField": f.name, "Type": f.type, "Length": f.length or 0}
            for k in conflicts_norm for fc, f in field_index[k]
        ]

        # 4) Proposed schema (nullable)
        proposed = propose_schema(field_index, keep_nullable=True)

        # 5) Traceability
        trace = build_traceability(proposed, field_index)

        # 6) FieldMappings + mapping CSV (optional)
        fieldmap_path = None
        mapping_csv_path = None
        if do_fieldmap:
            fieldmap_path, mapping_csv_path = build_fieldmappings_and_csv(
                proposed_schema=proposed,
                field_index=field_index,
                out_folder=out_folder,
                fieldmap_filename=DEFAULT_FIELDMAP_FILENAME,
                mapping_csv_filename=DEFAULT_MAPPING_CSV,
                merge_rule=merge_rule
            )

        # Compose summary
        summary = {
            "inputs": inputs,
            "geometry": {fc: geom_by_fc[fc] for fc in inputs},
            "domainsByWorkspace": domains_by_ws,
            "commonFields(normalized)": common_norm,
            "conflicts(normalized)": conflicts_norm,
            "conflictDetails": conflict_details,
            "proposedSchema(nullable)": proposed,
            "traceability": trace,
            "fieldMappingsPreset": fieldmap_path,
            "targetSchemaLabel": target_label
        }

        # 7) CSV reports (incl. targeted consolidated schema)
        write_summary_csvs(summary, out_folder)

        arcpy.AddMessage("CSV outputs written to: {}".format(out_folder))
        if fieldmap_path:
            arcpy.AddMessage("FieldMappings preset: {}".format(fieldmap_path))
        if mapping_csv_path:
            arcpy.AddMessage("Mapping CSV: {}".format(mapping_csv_path))
        arcpy.AddMessage("Done. Label: {}".format(target_label))
