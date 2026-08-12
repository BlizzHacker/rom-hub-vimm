#!/usr/bin/env python3
"""Bulk ROM importer for Vimm's Lair catalog into Romm library."""

import json
import re
import asyncio
import sys
from pathlib import Path
from typing import Optional
import aiohttp

CATALOG_PATH = Path("/opt/rom-hub-vimm-v026/catalog-enriched.json")
ROMM_LIBRARY = Path("/mnt/usb1/roms")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CONCURRENT_DOWNLOADS = 3  # be nice to Vimm


async def scrape_fresh_token(session: aiohttp.ClientSession, vault_id: str) -> Optional[dict]:
    """Scrape a fresh game URL + core from Vimm play page."""
    url = f"https://vimm.net/vault/{vault_id}?p=play"
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT},
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception as e:
        return {"error": str(e)}

    core_m = re.search(r"EJS_core='([^']+)'", html)
    url_m = re.search(r"EJS_gameUrl='([^']+)'", html)

    game_url = url_m.group(1) if url_m else None
    core = core_m.group(1) if core_m else None

    if not game_url:
        return None
    return {"game_url": game_url, "core": core}

CORE_TO_ROMM = {
    "nes": "nes", "snes": "snes", "n64": "n64", "gba": "gba",
    "gb": "gb", "gbc": "gbc", "genesis": "genesis-slash-megadrive",
    "segacd": "segacd", "sega32x": "sega32", "sms": "sms",
    "gamegear": "gamegear", "psx": "psx", "atari2600": "atari2600",
    "atari5200": "atari5200", "atari7800": "atari7800", "lynx": "lynx",
    "dc": "dc", "saturn": "saturn", "nds": "nds", "ngc": "ngc",
    "wii": "wii", "psp": "psp", "ps2": "ps2", "jaguar": "jaguar",
}


async def download_rom(session: aiohttp.ClientSession, item: dict, semaphore: asyncio.Semaphore):
    """Download a single ROM from Vimm (scrapes fresh token each time)."""
    vault_id = item["vault_id"]

    core = item.get("core", "")
    romm_platform = CORE_TO_ROMM.get(core)
    if not romm_platform:
        return None

    target_dir = ROMM_LIBRARY / romm_platform
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = item.get("filename", f"{vault_id}.rom")
    output_path = target_dir / filename

    if output_path.exists():
        return {"vault_id": vault_id, "status": "exists", "path": str(output_path)}

    async with semaphore:
        try:
            # Scrape fresh token
            token_data = await scrape_fresh_token(session, vault_id)
            if not token_data or "error" in token_data:
                return {"vault_id": vault_id, "status": "error", "reason": "no_token"}

            game_url = token_data["game_url"]
            referer = f"https://vimm.net/vault/{vault_id}?p=play"
            async with session.get(
                game_url,
                headers={"User-Agent": USER_AGENT, "Referer": referer},
                timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status != 200:
                    return {"vault_id": vault_id, "status": "error", "code": resp.status}
                zip_data = await resp.read()

            import zipfile
            import io
            try:
                with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                    rom_files = [f for f in zf.namelist() if not f.endswith(".txt") and not f.endswith("/")]
                    if not rom_files:
                        return {"vault_id": vault_id, "status": "error", "reason": "no_rom_in_zip"}
                    rom_filename = rom_files[0]
                    rom_bytes = zf.read(rom_filename)
                    output_path.write_bytes(rom_bytes)
                    return {"vault_id": vault_id, "status": "downloaded", "path": str(output_path), "size": len(rom_bytes)}
            except Exception as e:
                return {"vault_id": vault_id, "status": "error", "reason": f"zip_error: {e}"}

        except Exception as e:
            return {"vault_id": vault_id, "status": "error", "reason": str(e)}


async def main(platform_filter: Optional[str] = None, limit: Optional[int] = None):
    """Main import function."""
    if not CATALOG_PATH.exists():
        print(f"Catalog not found: {CATALOG_PATH}")
        sys.exit(1)

    with open(CATALOG_PATH) as f:
        data = json.load(f)

    items = data.get("items", [])
    print(f"Loaded {len(items)} items from catalog")

    if platform_filter:
        items = [i for i in items if i.get("core") == platform_filter]
        print(f"Filtered to {len(items)} {platform_filter} games")

    if limit:
        items = items[:limit]
        print(f"Limited to first {limit} items")

    semaphore = asyncio.Semaphore(CONCURRENT_DOWNLOADS)

    async with aiohttp.ClientSession() as session:
        tasks = [download_rom(session, item, semaphore) for item in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    downloaded = 0
    existing = 0
    errors = 0
    for r in results:
        if isinstance(r, Exception):
            errors += 1
            continue
        if not r:
            errors += 1
            continue
        if r["status"] == "downloaded":
            downloaded += 1
            print(f"✓ {r['vault_id']}: {r['path']} ({r['size']} bytes)")
        elif r["status"] == "exists":
            existing += 1
        else:
            errors += 1
            print(f"✗ {r['vault_id']}: {r.get('reason', r.get('code', 'unknown'))}")

    print(f"\n=== Summary ===")
    print(f"Downloaded: {downloaded}")
    print(f"Already exist: {existing}")
    print(f"Errors: {errors}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bulk import Vimm ROMs to Romm library")
    parser.add_argument("--platform", help="Filter by platform (nes, snes, etc)")
    parser.add_argument("--limit", type=int, help="Max number of ROMs to download")
    args = parser.parse_args()

    asyncio.run(main(args.platform, args.limit))
