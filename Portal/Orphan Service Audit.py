"""
Scans every Web Map, Web Mapping Application (Web AppBuilder /
Configurable Apps), Dashboard, StoryMap and Experience Builder app in
the portal for references (service URLs and item IDs) that no longer
resolve - i.e. orphaned apps left behind after a service or layer was
removed.

Run from the ArcGIS Pro Python window so GIS("Pro") can use the
already-authenticated connection.

Prints a clean console report at the end; nothing is written to disk.
"""

import datetime
import re

import requests
from arcgis.gis import GIS

gis = GIS("Pro")
print(f"Logged on as {gis.users.me.username}")

# Item types to crawl. "Web Mapping Application" covers both classic
# Web AppBuilder and Configurable Apps.
ITEM_TYPES = [
    "Web Map",
    "Web Mapping Application",
    "Dashboard",
    "StoryMap",
    "Web Experience",
]

REQUEST_TIMEOUT = 20

SERVICE_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+?/rest/services/[^\s\"'<>]+?/"
    r"(?:FeatureServer|MapServer|ImageServer|SceneServer|VectorTileServer|GPServer|GeocodeServer)"
    r"(?:/\d+)?",
    re.IGNORECASE,
)
ITEMID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)

# Caches so we only ever check a given service URL / item ID once, no
# matter how many maps or apps reference it.
url_cache = {}
itemid_cache = {}


def extract_references(obj, found_urls, found_itemids):
    """Walk any web map / web app JSON (already-parsed dicts/lists) and
    collect every service URL and referenced item ID, regardless of
    where in the structure they live (operational layers, popups,
    Experience Builder data sources, etc.)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                if key.lower() == "itemid" and ITEMID_RE.match(value):
                    found_itemids.add(value)
                for m in SERVICE_URL_RE.finditer(value):
                    found_urls.add(m.group(0))
            else:
                extract_references(value, found_urls, found_itemids)
    elif isinstance(obj, list):
        for value in obj:
            extract_references(value, found_urls, found_itemids)


def gather_json_blobs(item):
    """Web Maps / Dashboards keep their config in item.get_data().
    Web AppBuilder, Experience Builder and some StoryMap configs
    instead live in a JSON resource file, so check both."""
    blobs = []

    try:
        data = item.get_data()
        if data:
            blobs.append(data)
    except Exception:
        pass

    try:
        for res in item.resources.list():
            name = res.get("resource", "")
            if name.lower().endswith(".json"):
                try:
                    res_data = item.resources.get(name, try_json=True)
                    if res_data:
                        blobs.append(res_data)
                except Exception:
                    pass
    except Exception:
        pass

    return blobs


def check_url(url):
    if url in url_cache:
        return url_cache[url]

    params = {"f": "json"}
    token = getattr(gis._con, "token", None)
    if token:
        params["token"] = token

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        url_cache[url] = (False, f"request failed: {exc}")
        return url_cache[url]

    if resp.status_code != 200:
        url_cache[url] = (False, f"HTTP {resp.status_code}")
        return url_cache[url]

    try:
        payload = resp.json()
    except ValueError:
        # Non-JSON back from a REST service endpoint almost always
        # means it redirected to a login/error page - service is gone.
        url_cache[url] = (False, "non-JSON response (service likely moved/removed)")
        return url_cache[url]

    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        url_cache[url] = (False, f"service error: {msg}")
    else:
        url_cache[url] = (True, "ok")

    return url_cache[url]


def check_itemid(item_id):
    if item_id in itemid_cache:
        return itemid_cache[item_id]
    try:
        ref_item = gis.content.get(item_id)
    except Exception:
        ref_item = None
    itemid_cache[item_id] = (
        (True, "ok") if ref_item else (False, "referenced item missing or inaccessible")
    )
    return itemid_cache[item_id]


def audit_item(item):
    issues = []
    urls, itemids = set(), set()

    for blob in gather_json_blobs(item):
        extract_references(blob, urls, itemids)

    for url in urls:
        ok, msg = check_url(url)
        if not ok:
            issues.append(("service", url, msg))

    for item_id in itemids:
        if item_id == item.id:
            continue
        ok, msg = check_itemid(item_id)
        if not ok:
            issues.append(("item", item_id, msg))

    return issues


# ----------------------------------------------------------------- main --

items = []
for item_type in ITEM_TYPES:
    found = gis.content.search(query="", item_type=item_type, max_items=-1)
    print(f"{item_type}: {len(found)} item(s)")
    items.extend(found)

print(f"\nAuditing {len(items)} item(s)...\n")

broken = []
for i, item in enumerate(items, 1):
    try:
        issues = audit_item(item)
    except Exception as exc:
        issues = [("error", "", f"audit failed: {exc}")]

    if issues:
        print(f"[{i}/{len(items)}] BROKEN: {item.title} ({item.type}) - {len(issues)} issue(s)")
        broken.append((item, issues))

# ---------------------------------------------------------------- report --

divider = "=" * 100
print(f"\n{divider}")
print(f"ORPHAN SERVICE AUDIT REPORT - {datetime.date.today():%Y-%m-%d}")
print(divider)
print(f"Items scanned: {len(items)}   Broken items: {len(broken)}\n")

if not broken:
    print("No broken references found.")
else:
    for item, issues in broken:
        item_url = f"{gis.url.rstrip('/')}/home/item.html?id={item.id}"
        print(f"- {item.title}  [{item.type}]")
        print(f"    owner: {item.owner}")
        print(f"    url:   {item_url}")
        for issue_type, reference, detail in issues:
            label = "service" if issue_type == "service" else "item ref" if issue_type == "item" else "error"
            print(f"    [{label}] {reference}")
            print(f"              -> {detail}")
        print()

print(divider)
