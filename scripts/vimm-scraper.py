#!/usr/bin/env python3
"""Scrape Vimm.net play pages to extract EmulatorJS cores and game URLs."""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

CATALOG_PATH = Path("/opt/rom-hub-vimm-v026/catalog.json")
ENRICHED_PATH = Path("/opt/rom-hub-vimm-v026/catalog-enriched.json")
PROGRESS_PATH = Path("/opt/rom-hub-vimm-v026/scraper-progress.json")
DELAY = 1.5  # seconds between requests
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

def load_catalog():
    """Load the base catalogue."""
    with open(CATALOG_PATH) as f:
        data = json.load(f)
    return data.get("items", [])

def load_progress():
    """Load scraper progress (already-processed vault IDs)."""
    if PROGRESS_PATH.exists():
        with open(PROGRESS_PATH) as f:
            return json.load(f)
    return {"processed": [], "failed": []}

def save_enriched(items):
    """Write the enriched catalogue atomically.

    It is 14MB and it is rewritten every ten pages, so an in-place truncate
    leaves a multi-second window on every pass where the file on disk is half a
    JSON document. The vault server reads this same path on a request, and a
    read landing in that window raised JSONDecodeError and turned into a 500 --
    which the player reports as "Network Error" on a game that works perfectly
    a second later.

    Writing beside it and renaming makes the swap atomic: a reader sees either
    the whole old file or the whole new one. os.replace is the atomic form and
    works on Windows too, where os.rename onto an existing path fails.
    """
    tmp = ENRICHED_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump({
            "version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "Vimm.net scraper",
            "items": items,
        }, f, indent=2)
        # The rename can otherwise beat the data to disk, so a crash leaves a
        # file that is present, named correctly and truncated.
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ENRICHED_PATH)


def save_progress(progress):
    """Save scraper progress."""
    with open(PROGRESS_PATH, "w") as f:
        json.dump(progress, f, indent=2)

def fetch_play_page(vault_id):
    """Fetch the play page and extract EJS variables."""
    url = f"https://vimm.net/vault/{vault_id}?p=play"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
        
        # Extract EJS variables
        core_match = re.search(r"EJS_core='([^']+)'", html)
        game_url_match = re.search(r"EJS_gameUrl='([^']+)'", html)
        title_match = re.search(r"<title>Play ([^<]+) for", html)
        bg_match = re.search(r"EJS_backgroundImage='([^']+)'", html)
        threads_match = re.search(r"EJS_threads=(true|false)", html)
        
        return {
            "core": core_match.group(1) if core_match else None,
            "game_url": game_url_match.group(1) if game_url_match else None,
            "title": title_match.group(1) if title_match else None,
            "background": bg_match.group(1) if bg_match else None,
            "threads": threads_match.group(1) == "true" if threads_match else False,
        }
    except (HTTPError, URLError, Exception) as e:
        print(f"  Error fetching {vault_id}: {e}", file=sys.stderr)
        return None

def enrich_catalog():
    """Enrich catalogue with playable data."""
    items = load_catalog()
    progress = load_progress()
    processed = set(progress["processed"])
    failed = set(progress["failed"])
    
    enriched = []
    total = len(items)
    
    print(f"Enriching {total} items...")
    print(f"Already processed: {len(processed)}")
    print(f"Already failed: {len(failed)}")
    
    for idx, item in enumerate(items, 1):
        vault_id = item.get("vault_id")
        if not vault_id:
            continue
        
        # Skip if already processed
        if vault_id in processed or vault_id in failed:
            # Still add to enriched list from existing data
            enriched.append(item)
            continue
        
        print(f"[{idx}/{total}] Processing vault {vault_id}...", file=sys.stderr)
        
        # Fetch play page
        play_data = fetch_play_page(vault_id)
        
        if play_data and play_data.get("core") and play_data.get("game_url"):
            # Merge play data into item
            enriched_item = {**item, **play_data}
            enriched.append(enriched_item)
            processed.add(vault_id)
            print(f"  ✓ {play_data.get('title', 'Unknown')} - core={play_data['core']}", file=sys.stderr)
        else:
            # Mark as failed
            failed.add(vault_id)
            enriched.append(item)  # Keep original item
            print(f"  ✗ No playable data found", file=sys.stderr)
        
        # Save progress every 10 items
        if idx % 10 == 0:
            progress["processed"] = list(processed)
            progress["failed"] = list(failed)
            save_progress(progress)
            
            save_enriched(enriched)
        
        # Rate limit
        time.sleep(DELAY)
    
    # Final save
    progress["processed"] = list(processed)
    progress["failed"] = list(failed)
    save_progress(progress)
    
    save_enriched(enriched)
    
    print(f"\nEnrichment complete!")
    print(f"  Processed: {len(processed)}")
    print(f"  Failed: {len(failed)}")
    print(f"  Total items: {len(enriched)}")
    print(f"  Output: {ENRICHED_PATH}")

if __name__ == "__main__":
    enrich_catalog()
