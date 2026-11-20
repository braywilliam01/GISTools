"""
Repair script: adds the coded values to the domains in an already-built
FAA_18B.gdb (from build_18b_gdb.py), for the case where that first run hit
the AddCodedValueToDomain typo bug and left every domain empty while
everything else (feature datasets, feature classes, fields, domain
assignments) built fine.

Safe to re-run: skips any domain that already has coded values, so running
this more than once (or after a partially-successful run) won't duplicate
anything or error out.

Edit GDB_PATH below, then run the same way you ran build_18b_gdb.py.
"""

import json
import os
import sys

import arcpy

try:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    SCRIPT_DIR = r"C:\path\to\this\folder"  # edit this if run via exec()

# ----------------------------------------------------------------------
GDB_PATH = r"C:\GIS\Projects\FAA_18B.gdb"  # the .gdb build_18b_gdb.py created
# ----------------------------------------------------------------------

SCHEMA_PATH = os.path.join(SCRIPT_DIR, "faa_18b_schema.json")


def log(msg):
    print(msg)
    sys.stdout.flush()


def main():
    if not arcpy.Exists(GDB_PATH):
        raise RuntimeError(f"{GDB_PATH} does not exist - fix GDB_PATH above.")

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)

    existing_domains = {d.name: d for d in arcpy.da.ListDomains(GDB_PATH)}
    domains = schema["domains"]
    errors = []
    filled = 0
    skipped = 0

    log(f"Checking {len(domains)} domains in {GDB_PATH} ...")
    for domain_name, domain in domains.items():
        existing = existing_domains.get(domain_name)
        if existing is None:
            errors.append(f"DOMAIN {domain_name}: not found in the geodatabase")
            continue
        if existing.codedValues:
            skipped += 1
            continue
        for code_entry in domain["codes"]:
            code = code_entry["code"]
            desc = code_entry["description"] or code
            try:
                arcpy.management.AddCodedValueToDomain(GDB_PATH, domain_name, code, desc)
            except Exception as e:
                errors.append(f"DOMAIN {domain_name} code {code!r}: {e}")
        filled += 1
        if filled % 10 == 0:
            log(f"  filled {filled} domains so far ...")

    log("")
    log("=" * 60)
    log(f"Done. Filled {filled} domains, skipped {skipped} already-populated, {len(errors)} errors.")
    if errors:
        report_path = os.path.join(os.path.dirname(GDB_PATH), "faa_18b_domain_fix_errors.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(errors))
        log(f"Error details written to: {report_path}")
        for e in errors[:20]:
            log(f"  - {e}")


if __name__ == "__main__":
    main()
