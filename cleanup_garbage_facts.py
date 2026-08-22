"""
One-time cleanup: removes garbage fact entries (e.g. "User's name: Unknown")
that got stored before the canonicalize_fact validation fix.

Run once, then delete this file.
"""

import memory

collection = memory._get_collection("personal")
all_items = collection.get()

to_delete = []
for item_id, doc in zip(all_items["ids"], all_items["documents"]):
    if "unknown" in doc.lower() or "n/a" in doc.lower():
        to_delete.append((item_id, doc))

if not to_delete:
    print("No garbage entries found.")
else:
    print(f"Found {len(to_delete)} garbage entries:")
    for item_id, doc in to_delete:
        print(f"  - {doc}")
    confirm = input("\nDelete these? (y/n): ").strip().lower()
    if confirm == "y":
        collection.delete(ids=[item_id for item_id, _ in to_delete])
        print("Deleted.")
    else:
        print("Cancelled — nothing deleted.")