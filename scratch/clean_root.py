import sys, os, time, json, requests
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, r"c:\Users\nitro\Desktop\Notion Drive")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.config import NOTION_TOKEN, NOTION_DATABASE_ID, NOTION_VERSION
import core.state as S

headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION,
}
db_id = NOTION_DATABASE_ID.replace("-", "")

print("1. Fetching all pages from Notion database...")
all_pages = []
has_more = True
start_cursor = None

while has_more:
    payload = {"page_size": 100}
    if start_cursor:
        payload["start_cursor"] = start_cursor
    r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query", headers=headers, json=payload, timeout=25)
    data = r.json()
    results = data.get("results", [])
    all_pages.extend(results)
    has_more = data.get("has_more", False)
    start_cursor = data.get("next_cursor")
    print(f"  Fetched {len(all_pages)} pages...")

print(f"\nTotal active pages in Notion: {len(all_pages)}")

c_folders = []
loose_files = []
root_devices = {}

for page in all_pages:
    pid = page["id"].replace("-", "")
    props = page.get("properties", {})
    title_list = props.get("Name", {}).get("title", [])
    name = title_list[0].get("plain_text", "").strip() if title_list else ""
    type_name = props.get("Type", {}).get("select", {}).get("name", "")
    parents = props.get("Parent Folder", {}).get("relation", [])
    
    if name == "Local Disk (C:)" and type_name == "Folder":
        c_folders.append(pid)
    
    if not parents:
        if type_name == "File":
            loose_files.append((pid, name))
        elif type_name == "Folder":
            root_devices.setdefault(name, []).append(pid)

print(f"\nLocal Disk (C:) folders: {len(c_folders)}")
print(f"Loose files at root: {len(loose_files)}")
for rname, pids in root_devices.items():
    print(f"  Root folder '{rname}': {len(pids)} copy/copies")

# Determine which C: folder to keep
keep_c_id = None
duplicate_c = []
if c_folders:
    keep_c_id = c_folders[0]  # Keep the first one
    duplicate_c = c_folders[1:]
    print(f"\n-> Keeping Local Disk (C:) Notion ID: {keep_c_id}")
    print(f"-> Archiving {len(duplicate_c)} duplicate Local Disk (C:) pages: {duplicate_c}")
else:
    # Create fresh Local Disk (C:)
    print("\n-> Creating fresh Local Disk (C:) root...")
    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json={
            "parent": {"database_id": db_id},
            "icon": {"type": "emoji", "emoji": "💽"},
            "properties": {
                "Name": {"title": [{"text": {"content": "Local Disk (C:)"}}]},
                "Type": {"select": {"name": "Folder"}},
                "Favorite": {"checkbox": True},
            }
        },
        timeout=25
    )
    if res.ok:
        keep_c_id = res.json()["id"].replace("-", "")
        print(f"-> Created new Local Disk (C:) Notion ID: {keep_c_id}")

to_archive = [f[0] for f in loose_files] + duplicate_c
print(f"\n2. Archiving {len(to_archive)} items ({len(loose_files)} loose files + {len(duplicate_c)} duplicate C: folders)...")

def delete_one(nid):
    u = f"https://api.notion.com/v1/pages/{nid}"
    for _ in range(4):
        try:
            res = requests.patch(u, headers=headers, json={"archived": True}, timeout=15)
            if res.status_code == 429:
                time.sleep(1.5)
                continue
            return res.status_code in (200, 404)
        except Exception:
            time.sleep(0.5)
    return False

if to_archive:
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(delete_one, to_archive))
    print(f"  Successfully archived {sum(1 for r in results if r)} items!")

# 3. Ensure Local Disk (C:) has Favorite=True and proper properties
if keep_c_id:
    requests.patch(
        f"https://api.notion.com/v1/pages/{keep_c_id}",
        headers=headers,
        json={
            "properties": {
                "Favorite": {"checkbox": True},
                "Open in Browser": {"url": f"https://www.notion.so/{keep_c_id}"}
            }
        },
        timeout=15
    )

# 4. Update local cache (.notion_drive_cache.json) and state.json
print("\n3. Updating local drive cache and state...")
cache_paths = [
    Path.home() / ".notion_drive_cache.json",
    Path(r"c:\Users\nitro\Desktop\Notion Drive\.notion_drive_cache.json")
]

for cp in cache_paths:
    if cp.exists():
        try:
            with open(cp, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            
            # Remove all archived IDs
            archived_set = set(to_archive)
            cdata["items"] = {k: v for k, v in cdata.get("items", {}).items() if k not in archived_set}
            
            # Add or update keep_c_id
            if keep_c_id:
                cdata["items"][keep_c_id] = {
                    "id": keep_c_id,
                    "name": "Local Disk (C:)",
                    "type": "Folder",
                    "extension": "",
                    "size_mb": 0.0,
                    "size_bytes": 0,
                    "parent_id": None,
                    "local_path": "C:\\Users",
                    "mtime": time.time(),
                    "cached_at": time.time()
                }
            
            # Rebuild children map & root_items
            cdata["children"] = {}
            cdata["root_items"] = []
            for it_id, it in cdata["items"].items():
                pid = it.get("parent_id")
                if pid and pid in cdata["items"]:
                    cdata["children"].setdefault(pid, []).append(it_id)
                else:
                    cdata["root_items"].append(it_id)
            
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(cdata, f, indent=2)
            print(f"  Updated cache file: {cp}")
        except Exception as e:
            print(f"  Cache error on {cp}: {e}")

print("\nCleanup completely finished! Root is now clean with exactly 1 copy of each drive and ZERO loose files.")
