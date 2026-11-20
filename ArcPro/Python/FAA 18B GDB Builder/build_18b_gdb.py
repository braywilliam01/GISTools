"""
Build a File Geodatabase matching the AC 150/5300-18B (Change 1) Airports
GIS data standard: one feature dataset per Chapter 5 feature group, one
feature class per feature, fields per the attribute tables, and coded-value
domains per section 5.15 (Attribute Enumerations) assigned to the fields
that reference them.

Run this from the ArcGIS Pro Python window, or from a standalone script tool,
with arcpy available. Edit the CONFIG block below before running.

Schema source: faa_18b_schema.json (next to this script), extracted from the
published AC 150/5300-18B-chg1-consolidated.pdf. See README.md in this folder
for how that extraction was done and its known limitations before using this
for an actual submission - a small number of fields had no datatype at all
in the source document and were inferred from the field name/description;
those are flagged below as each one is created.
"""

import json
import os
import sys

import arcpy

# ----------------------------------------------------------------------
# CONFIG - edit before running
# ----------------------------------------------------------------------
OUTPUT_FOLDER = r"C:\GIS\Projects"      # folder the .gdb will be created in
GDB_NAME = "FAA_18B.gdb"                # must not already exist (fresh build only)

# AC 150/5300-18B accepts NAD83-based state plane/UTM, in feet or meters, per
# project (section 3.7). WGS84 geographic is used here only as a safe,
# always-valid default - replace with the airport's actual project CRS
# (e.g. the appropriate NAD83 State Plane zone) before a real submission.
SPATIAL_REFERENCE_WKID = 4326

OVERWRITE_FIELD_IF_EXISTS = False  # kept False; this is a fresh-build script
# ----------------------------------------------------------------------

try:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # Happens when this file is run via exec(open(path).read()) in the
    # ArcGIS Pro Python window - that doesn't set __file__. Either run it
    # with `import runpy; runpy.run_path(r"path\to\build_18b_gdb.py")`
    # instead (which does set __file__), or hardcode the folder here:
    SCRIPT_DIR = r"C:\path\to\this\folder"
SCHEMA_PATH = os.path.join(SCRIPT_DIR, "faa_18b_schema.json")


def log(msg):
    print(msg)
    sys.stdout.flush()


def load_schema():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def create_gdb():
    gdb_path = os.path.join(OUTPUT_FOLDER, GDB_NAME)
    if arcpy.Exists(gdb_path):
        raise RuntimeError(
            f"{gdb_path} already exists. This script only does a fresh build - "
            "delete/rename the existing geodatabase first, or change GDB_NAME."
        )
    if not os.path.isdir(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)
    log(f"Creating file geodatabase: {gdb_path}")
    arcpy.management.CreateFileGDB(OUTPUT_FOLDER, GDB_NAME)
    return gdb_path


def create_domains(gdb_path, domains, errors):
    log(f"Creating {len(domains)} domains ...")
    for i, (domain_name, domain) in enumerate(domains.items(), 1):
        try:
            arcpy.management.CreateDomain(
                gdb_path,
                domain_name,
                domain_description=f"AC 150/5300-18B domain: {domain_name}",
                field_type=domain["field_type"],
                domain_type="CODED",
            )
            for code_entry in domain["codes"]:
                code = code_entry["code"]
                desc = code_entry["description"] or code
                arcpy.management.AddCodedValueToDomain(gdb_path, domain_name, code, desc)
        except Exception as e:
            errors.append(f"DOMAIN {domain_name}: {e}")
        if i % 10 == 0 or i == len(domains):
            log(f"  domains: {i}/{len(domains)}")


def create_feature_datasets(gdb_path, groups, sr, errors):
    log(f"Creating {len(groups)} feature datasets ...")
    for group_name in groups:
        try:
            arcpy.management.CreateFeatureDataset(gdb_path, group_name, sr)
        except Exception as e:
            errors.append(f"FEATURE DATASET {group_name}: {e}")


def create_feature_classes(gdb_path, feature_datasets, errors):
    total = sum(len(fcs) for fcs in feature_datasets.values())
    log(f"Creating {total} feature classes ...")
    done = 0
    for group_name, fc_list in feature_datasets.items():
        dataset_path = os.path.join(gdb_path, group_name)
        for fc in fc_list:
            fc_name = fc["name"]
            try:
                arcpy.management.CreateFeatureclass(
                    dataset_path,
                    fc_name,
                    geometry_type=fc["geometry_type"],
                    has_z="ENABLED" if fc.get("has_z") else "DISABLED",
                )
                fc_path = os.path.join(dataset_path, fc_name)
                add_fields(fc_path, fc, errors)
            except Exception as e:
                errors.append(f"FEATURE CLASS {group_name}/{fc_name}: {e}")
            done += 1
            if done % 10 == 0 or done == total:
                log(f"  feature classes: {done}/{total}")


def add_fields(fc_path, fc, errors):
    fc_name = fc["name"]
    for field in fc["fields"]:
        field_name = arcpy.ValidateFieldName(field["name"], os.path.dirname(fc_path))
        kwargs = {"field_alias": field["alias"]}
        if field["type"] == "TEXT" and field["length"]:
            kwargs["field_length"] = field["length"]
        try:
            arcpy.management.AddField(fc_path, field_name, field["type"], **kwargs)
            if field["domain"]:
                arcpy.management.AssignDomainToField(fc_path, field_name, field["domain"])
        except Exception as e:
            errors.append(f"FIELD {fc_name}.{field_name}: {e}")


def main():
    schema = load_schema()
    sr = arcpy.SpatialReference(SPATIAL_REFERENCE_WKID)
    errors = []

    gdb_path = create_gdb()
    create_domains(gdb_path, schema["domains"], errors)
    create_feature_datasets(gdb_path, list(schema["feature_datasets"].keys()), sr, errors)
    create_feature_classes(gdb_path, schema["feature_datasets"], errors)

    log("")
    log("=" * 60)
    log(f"Done. {len(errors)} errors.")
    if errors:
        report_path = os.path.join(OUTPUT_FOLDER, "faa_18b_build_errors.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(errors))
        log(f"Error details written to: {report_path}")
        for e in errors[:20]:
            log(f"  - {e}")
        if len(errors) > 20:
            log(f"  ... and {len(errors) - 20} more, see the report file")
    log(f"Geodatabase: {gdb_path}")


if __name__ == "__main__":
    main()
