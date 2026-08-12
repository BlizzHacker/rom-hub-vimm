#!/usr/bin/env python3
"""Download Vimm.net ROMs to ROMM library."""

import json
import re
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ENRICHED_PATH = Path("/opt/rom-hub-vimm-v026/catalog-enriched.json")
ROMM_LIBRARY = Path("/mnt/usb4/romm/library/roms")  # Adjust based on actual ROMM path
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# EJS core to ROMM platform mapping
CORE_TO_PLATFORM = {
    "nes": "nes",
    "snes": "snes",
    "n64": "n64",
    "gba": "gba",
    "gb": "gb",
    "gbc": "gbc",
    "genesis": "genesis",
    "segacd": "segacd",
    "sega32x": "sega32x",
    "sms": "mastersystem",
    "gamegear": "gamegear",
    "psx": "psx",
    "ps2": "ps2",
    "atari2600": "atari2600",
    "atari5200": "atari5200",
    "atari7800": "atari7800",
    "lynx": "lynx",
    "dc": "dreamcast",
    "saturn": "saturn",
    "jaguar": "jaguar",
    "nds": "nds",
    "ngc": "gc",
    "wii": "wii",
    "psp": "psp",
    "3do": "3do",
    "tg16": "tg16",
    "tgcd": "tgcd",
}

def load_enriched_catalog():
    """Load the enriched catalogue."""
    if not ENRICHED_PATH.exists():
        print(f"Error: {ENRICHED_PATH} not found. Run scraper first.", file=sys.stderr)
        sys.exit(1)
    
    with open(ENRICHED_PATH) as f:
        data = json.load(f)
    
    return [item for item in data.get("items", []) if item.get("core") and item.get("game_url")]

def scrape_fresh_token(vault_id):
    """Scrape a fresh token from the play page."""
    url = f"https://vimm.net/vault/{vault_id}?p=play"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
        
        match = re.search(r"EJS_gameUrl='([^']+)'", html)
        if match:
            return match.group(1)
        return None
    except Exception as e:
        print(f"Error scraping token for {vault_id}: {e}", file=sys.stderr)
        return None

def download_rom(game_url, vault_id):
    """Download ROM with proper referer."""
    referer = f"https://vimm.net/vault/{vault_id}?p=play"
    req = Request(game_url, headers={
        "User-Agent": USER_AGENT,
        "Referer": referer,
    })
    
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.read()
    except Exception as e:
        print(f"Error downloading {game_url}: {e}", file=sys.stderr)
        return None

def extract_rom(zip_data, vault_id, platform):
    """Extract ROM from zip and save to ROMM library."""
    if not zip_data:
        return None
    
    # Create temp file
    temp_zip = Path(f"/tmp/vimm-{vault_id}.zip")
    temp_zip.write_bytes(zip_data)
    
    try:
        with zipfile.ZipFile(temp_zip) as zf:
            # Find the ROM file (not the Vimm's Lair.txt)
            rom_files = [f for f in zf.namelist() if not f.endswith(".txt") and not f.endswith("/")]
            
            if not rom_files:
                print(f"No ROM file found in zip for {vault_id}", file=sys.stderr)
                return None
            
            # Extract the ROM
            rom_filename = rom_files[0]
            rom_data = zf.read(rom_filename)
            
            # Determine output path
            romm_platform = CORE_TO_PLATFORM.get(platform, platform)
            output_dir = ROMM_LIBRARY / romm_platform
            output_dir.mkdir(parents=True, exist_ok=True)
            
            output_path = output_dir / rom_filename
            output_path.write_bytes(rom_data)
            
            print(f"✓ Extracted {rom_filename} to {output_path}")
            return output_path
    
    except Exception as e:
        print(f"Error extracting {vault_id}: {e}", file=sys.stderr)
        return None
    
    finally:
        temp_zip.unlink(missing_ok=True)

def download_item(vault_id):
    """Download a single item by vault ID."""
    catalog = load_enriched_catalog()
    item = next((i for i in catalog if i.get("vault_id") == vault_id), None)
    
    if not item:
        print(f"Item {vault_id} not found in catalogue", file=sys.stderr)
        return None
    
    print(f"Downloading: {item.get('title', 'Unknown')} (vault {vault_id})")
    
    # Scrape fresh token
    game_url = scrape_fresh_token(vault_id)
    if not game_url:
        print(f"Failed to get fresh token for {vault_id}", file=sys.stderr)
        return None
    
    # Download ROM
    zip_data = download_rom(game_url, vault_id)
    if not zip_data:
        print(f"Failed to download ROM for {vault_id}", file=sys.stderr)
        return None
    
    # Extract to ROMM library
    core = item.get("core")
    output_path = extract_rom(zip_data, vault_id, core)
    
    return output_path

def download_all():
    """Download all playable items."""
    catalog = load_enriched_catalog()
    print(f"Found {len(catalog)} playable items")
    
    for idx, item in enumerate(catalog, 1):
        vault_id = item.get("vault_id")
        title = item.get("title", "Unknown")
        
        print(f"\n[{idx}/{len(catalog)}] {title}")
        
        # Check if already downloaded
        core = item.get("core")
        platform = CORE_TO_PLATFORM.get(core, core)
        output_dir = ROMM_LIBRARY / platform
        
        # Simple check: if any file with similar name exists, skip
        # (This is naive - could be improved with hash checking)
        
        output_path = download_item(vault_id)
        if output_path:
            print(f"✓ Saved to {output_path}")
        else:
            print(f"✗ Failed to download")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Download specific vault ID
        vault_id = sys.argv[1]
        download_item(vault_id)
    else:
        # Download all
        download_all()
