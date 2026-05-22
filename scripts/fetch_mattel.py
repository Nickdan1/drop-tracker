#!/usr/bin/env python3
"""
Fetch the Mattel Creations product catalog and detect newly-added items.

Runs from GitHub Actions on a schedule. Writes:
  - data/drops.json    : list of all known products (the dashboard reads this)
  - data/seen.json     : internal state tracking product IDs we've seen

Stays well within polite-scraping territory:
  - Single request per run
  - Reasonable User-Agent
  - Respects standard HTTP conventions
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

SHOP_URL = "https://creations.mattel.com/products.json?limit=250"
DATA_DIR = Path(__file__).parent.parent / "data"
DROPS_FILE = DATA_DIR / "drops.json"
SEEN_FILE = DATA_DIR / "seen.json"

USER_AGENT = (
    "Mozilla/5.0 (compatible; DropTracker/1.0; personal drop monitor)"
)


def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARN: could not read {path}: {e}", file=sys.stderr)
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def fetch_products():
    """Fetch products from the Shopify endpoint. Returns list of products."""
    req = urllib.request.Request(
        SHOP_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code} fetching products: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"ERROR: failed to fetch products: {e}", file=sys.stderr)
        sys.exit(1)

    return data.get("products", [])


def normalize(product):
    """Convert a Shopify product object into our compact drop format."""
    handle = product.get("handle", "")
    variants = product.get("variants", [])
    images = product.get("images", [])
    available = any(v.get("available") for v in variants)

    price = None
    if variants:
        try:
            price = float(variants[0].get("price", 0))
        except (TypeError, ValueError):
            price = None

    return {
        "id": str(product.get("id", "")),
        "title": product.get("title", "").strip(),
        "handle": handle,
        "url": f"https://creations.mattel.com/products/{handle}" if handle else "",
        "vendor": product.get("vendor", "").strip(),
        "product_type": product.get("product_type", "").strip(),
        "tags": product.get("tags") or [],
        "image": images[0].get("src") if images else None,
        "price": price,
        "available": available,
        "created_at": product.get("created_at"),
        "published_at": product.get("published_at"),
        "first_seen": datetime.now(timezone.utc).isoformat(),
        "source": "Mattel Creations",
    }


def main():
    print(f"=== Drop tracker run: {datetime.now(timezone.utc).isoformat()} ===")

    seen = load_json(SEEN_FILE, {"ids": []})
    seen_ids = set(seen.get("ids", []))
    print(f"Known product IDs: {len(seen_ids)}")

    products = fetch_products()
    print(f"Fetched {len(products)} products from store")

    if not products:
        print("WARN: no products in response; not updating state")
        sys.exit(0)

    all_drops = []
    new_drops = []
    current_ids = set()

    # Preserve first_seen timestamp for already-known products
    existing_drops = load_json(DROPS_FILE, [])
    existing_first_seen = {d["id"]: d.get("first_seen") for d in existing_drops}

    for p in products:
        drop = normalize(p)
        pid = drop["id"]
        current_ids.add(pid)

        if pid in existing_first_seen and existing_first_seen[pid]:
            drop["first_seen"] = existing_first_seen[pid]

        all_drops.append(drop)

        if pid not in seen_ids:
            new_drops.append(drop)
            print(f"  NEW: {drop['title']} ({drop['url']})")

    # Sort newest first by published date, then first_seen
    all_drops.sort(
        key=lambda d: (d.get("published_at") or "", d.get("first_seen") or ""),
        reverse=True,
    )

    # Build the file the dashboard reads
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://creations.mattel.com",
        "total": len(all_drops),
        "new_count": len(new_drops),
        "drops": all_drops,
    }
    save_json(DROPS_FILE, output)

    # Update seen state
    save_json(SEEN_FILE, {"ids": sorted(current_ids)})

    print(f"Wrote {len(all_drops)} drops, {len(new_drops)} new")
    print(f"Output: {DROPS_FILE}")


if __name__ == "__main__":
    main()
