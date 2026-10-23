"""
Debug companion to "Orphan Service Audit.py".

Runs the exact same reference-extraction and service-check logic, but
only against the first N items (default 5) and with verbose debug
output at every step - raw JSON blobs, every URL/item ID extracted,
the exact request sent (url/params/headers) and the raw response for
each check. Nothing is written to disk; copy the console output to
hand back for diagnosis.

Run from the ArcGIS Pro Python window so GIS("Pro") can use the
already-authenticated connection.
"""

import json
import re
import textwrap

import requests
from arcgis.gis import GIS

# How many items (across all ITEM_TYPES, in search order) to debug.
ITEM_LIMIT = 5

gis = GIS("Pro")
print(f"Logged on as {gis.users.me.username}")
print(f"Portal URL (used as Referer): {gis.url}\n")

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


def dump(label, obj, width=2000):
    """Pretty-print (and hard-truncate) anything so one weird item
    doesn't flood the console."""
    try:
        text = json.dumps(obj, indent=2, default=str)
    except TypeError:
        text = str(obj)
    if len(text) > width:
        text = text[:width] + f"\n... [truncated, {len(text)} chars total]"
    print(f"      {label}:")
    for line in text.splitlines():
        print(f"        {line}")


def extract_references(obj, found_urls, found_itemids, path="root"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                if key.lower() == "itemid" and ITEMID_RE.match(value):
                    found_itemids.add(value)
                    print(f"      [found itemid] {path}.{key} = {value}")
                for m in SERVICE_URL_RE.finditer(value):
                    found_urls.add(m.group(0))
                    print(f"      [found url]    {path}.{key} = {m.group(0)}")
            else:
                extract_references(value, found_urls, found_itemids, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            extract_references(value, found_urls, found_itemids, f"{path}[{i}]")


def gather_json_blobs(item):
    blobs = []

    try:
        data = item.get_data()
        print(f"    get_data() -> {'dict/list, ' + str(len(data)) + ' top-level key(s)/entries' if data else 'empty/None'}")
        if data:
            blobs.append(("get_data()", data))
    except Exception as exc:
        print(f"    get_data() raised: {exc!r}")

    try:
        resources = item.resources.list()
        print(f"    resources.list() -> {len(resources)} resource(s): {[r.get('resource') for r in resources]}")
        for res in resources:
            name = res.get("resource", "")
            if name.lower().endswith(".json"):
                try:
                    res_data = item.resources.get(name, try_json=True)
                    print(f"    resource '{name}' fetched ok")
                    if res_data:
                        blobs.append((f"resource:{name}", res_data))
                except Exception as exc:
                    print(f"    resource '{name}' raised: {exc!r}")
    except Exception as exc:
        print(f"    resources.list() raised: {exc!r}")

    return blobs


def check_url(url):
    params = {"f": "json"}
    token = getattr(gis._con, "token", None)
    if token:
        params["token"] = token
    headers = {"Referer": gis.url}

    print(f"    -> GET {url}")
    print(f"       params={{'f': 'json', 'token': {'<set>' if token else None}}}")
    print(f"       headers={headers}")

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        print(f"       request raised: {exc!r}")
        return False, f"request failed: {exc}"

    print(f"       HTTP {resp.status_code}")
    print(f"       final url: {resp.url}")
    body_preview = textwrap.shorten(resp.text, width=800, placeholder=" ...[truncated]")
    print(f"       body: {body_preview}")

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"

    try:
        payload = resp.json()
    except ValueError:
        return False, "non-JSON response (service likely moved/removed)"

    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return False, f"service error: {msg}"
    return True, "ok"


def check_itemid(item_id):
    print(f"    -> gis.content.get({item_id!r})")
    try:
        ref_item = gis.content.get(item_id)
    except Exception as exc:
        print(f"       raised: {exc!r}")
        return False, f"lookup raised: {exc}"
    if ref_item:
        print(f"       found: {ref_item.title!r} [{ref_item.type}] owner={ref_item.owner}")
        return True, "ok"
    print("       not found / inaccessible")
    return False, "referenced item missing or inaccessible"


# ----------------------------------------------------------------- main --

items = []
for item_type in ITEM_TYPES:
    if len(items) >= ITEM_LIMIT:
        break
    found = gis.content.search(query="", item_type=item_type, max_items=ITEM_LIMIT - len(items))
    print(f"{item_type}: pulled {len(found)} item(s) for debug run")
    items.extend(found)

items = items[:ITEM_LIMIT]
print(f"\n{'=' * 100}\nDEBUGGING {len(items)} ITEM(S)\n{'=' * 100}\n")

for i, item in enumerate(items, 1):
    print(f"[{i}/{len(items)}] {item.title!r}")
    print(f"    type:  {item.type}")
    print(f"    id:    {item.id}")
    print(f"    owner: {item.owner}")
    print(f"    url:   {gis.url.rstrip('/')}/home/item.html?id={item.id}")

    blobs = gather_json_blobs(item)

    urls, itemids = set(), set()
    for source_label, blob in blobs:
        print(f"    scanning blob from {source_label}...")
        dump(f"raw blob ({source_label})", blob)
        extract_references(blob, urls, itemids, path=source_label)

    print(f"    totals: {len(urls)} service url(s), {len(itemids)} item id ref(s)")

    for url in urls:
        ok, msg = check_url(url)
        print(f"       => {'OK' if ok else 'ISSUE'}: {msg}\n")

    for item_id in itemids:
        if item_id == item.id:
            print(f"    -> item id ref {item_id} is self-reference, skipping")
            continue
        ok, msg = check_itemid(item_id)
        print(f"       => {'OK' if ok else 'ISSUE'}: {msg}\n")

    print()

print(f"{'=' * 100}\nDEBUG RUN COMPLETE\n{'=' * 100}")
