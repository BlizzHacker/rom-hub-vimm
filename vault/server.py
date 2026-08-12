#!/usr/bin/env python3
"""Vimm Vault - FastAPI server for Yarrit.com ROM integration."""

import asyncio
import html
import json
import os
import re
import zipfile
import io
import time
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Every path is an environment override with the Move Weight deployment as the
# default. Two reasons, and the second is the one that bites: this project is
# meant to be library-neutral, and hardcoded absolute paths made the module
# impossible to IMPORT anywhere else -- it built directories under /opt at
# import time, so merely collecting the tests on a laptop failed.
def _path(var: str, default: str) -> Path:
    return Path(os.environ.get(var, default))


CATALOG_PATH = _path("VIMM_CATALOG", "/opt/rom-hub-vimm-v026/catalog-enriched.json")
EMULATORJS_PATH = _path("VIMM_EMULATORJS", "/opt/rom-hub-vimm-v026/emulatorjs")
ROMM_LIBRARY = _path("VIMM_ROMM_LIBRARY", "/mnt/usb4/romm/library/roms")
ROM_CACHE = _path("VIMM_ROM_CACHE", "/opt/vimm-vault/cache")
ART_CACHE = _path("VIMM_ART_CACHE", "/opt/vimm-vault/art-cache")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CACHE_TTL = 3600

# Absolute URLs handed to other origins -- yarrit.com renders these posters and
# fetches these ROMs, so a relative path is no use to it. Overridable because
# nothing about this server should assume it is the one Move Weight runs.
PUBLIC_BASE = os.environ.get("VIMM_PUBLIC_BASE", "https://vimm.yarrit.com").rstrip("/")

_catalog = {}
_catalog_loaded = 0

# Every system EmulatorJS can boot, copied from getCores() in the release this
# server serves. It is the ONLY authority on the question.
#
# Guessing at this list is a known way to lose a thousand games at a time: four
# invalid core names once cost the RomM Xbox client 1,451 titles, and the
# failure is silent both ways -- an invented name 404s at the core download,
# and a name left out quietly hides a system that works perfectly.
EJS_SYSTEMS = frozenset({
    "3do", "amiga", "arcade", "atari2600", "atari5200", "atari7800", "c64",
    "c128", "coleco", "dos", "gb", "gba", "jaguar", "lynx", "mame", "n64",
    "nds", "nes", "ngp", "pce", "pcfx", "pet", "plus4", "psx", "psp", "sega",
    "sega32x", "segaCD", "segaGG", "segaMD", "segaMS", "segaSaturn", "snes",
    "vb", "vic20", "ws",
})

# The scraper reads EJS_core straight off vimm.net's own player page, so most
# catalogue values are already EmulatorJS ids and pass through untouched. This
# holds only the ones that do not:
#
#   segacd  Vimm writes it lower-case; EmulatorJS wants segaCD, and the
#           mismatch 404s the core download for all 190 Sega CD entries.
#   gbc     Game Boy Color runs on the Game Boy core.
#   tg16    TurboGrafx-16 is mednafen_pce.
#
# Deliberately absent, and each one is a decision rather than an oversight:
# ps2, gamecube, wii, wiiware, xbox, xbox360, x360-d, ps3, 3ds, dreamcast and
# cdi have no EmulatorJS core at all, and tgcd (TurboGrafx-CD) needs a system
# BIOS this server does not ship. Together that is roughly half the catalogue.
# Those entries stay in it and stay downloadable -- they are real -- they just
# cannot claim to play here.
CORE_ALIASES = {
    "segacd": "segaCD",
    "gbc": "gb",
    "tg16": "pce",
}

# Display names, for eyes rather than for logic.
PLATFORM_NAMES = {
    "nes": "NES", "snes": "SNES", "n64": "Nintendo 64",
    "gb": "Game Boy", "gba": "Game Boy Advance", "nds": "Nintendo DS",
    "segaMD": "Genesis", "segaCD": "Sega CD", "sega32x": "32X",
    "segaMS": "Master System", "segaGG": "Game Gear",
    "segaSaturn": "Saturn", "psx": "PlayStation", "psp": "PSP",
    "atari2600": "Atari 2600", "atari5200": "Atari 5200",
    "atari7800": "Atari 7800", "lynx": "Lynx", "jaguar": "Jaguar",
    "pce": "TurboGrafx-16", "vb": "Virtual Boy", "3do": "3DO",
    "ngp": "Neo Geo Pocket", "ws": "WonderSwan", "coleco": "ColecoVision",
}


def ejs_core(raw: str) -> str:
    """The EmulatorJS system id for a catalogue core, or "" if it cannot play.

    Returning "" rather than the raw value is the point. Passing an unknown
    name through to the player produces a core download that 404s behind a
    loading bar that never finishes -- a failure with no message, on a page
    that promised a game.
    """
    core = (raw or "").strip()
    core = CORE_ALIASES.get(core, core)
    return core if core in EJS_SYSTEMS else ""


def playable(item: dict) -> bool:
    """Whether this entry can boot in a browser. Set at load; see load_catalog."""
    return bool(item.get("ejs"))


CORE_TO_ROMM = {
    "nes": "nes", "snes": "snes", "n64": "n64", "gba": "gba",
    "gb": "gb", "gbc": "gbc", "segaMD": "genesis", "segaCD": "segacd",
    "sega32x": "sega32x", "segaMS": "mastersystem", "segaGG": "gamegear",
    "psx": "psx", "atari2600": "atari2600", "atari5200": "atari5200",
    "atari7800": "atari7800", "lynx": "lynx", "dreamcast": "dreamcast",
    "segaSaturn": "saturn", "nds": "nds", "gamecube": "gc", "wii": "wii",
    "psp": "psp", "ps2": "ps2", "jaguar": "jaguar", "pce": "turbografx16",
}


def card_for(item: dict) -> dict:
    """One catalogue entry as a Yarrit search card.

    The `#ejs=<core>` fragment on the URI is the important part. Yarrit's
    resolvers must never infer a core from a file extension -- `.bin` is
    Colecovision, Atari 2600 and Mega Drive all at once, and the wrong core
    boots happily and then runs a black screen with no error. The vault knows
    the system for certain because vimm.net declares it, so the answer travels
    with the URI instead of being guessed at the other end.

    `seeders` is 1 rather than 0 only because a 0 there reads as "dead" in a
    UI built for torrents; `instant` is the field that actually says this plays.
    """
    vault_id = item.get("vault_id", "")
    title = item.get("title", "Unknown")
    core = item.get("core", "")
    system = item.get("ejs", "")
    platform = PLATFORM_NAMES.get(system, core.upper())

    return {
        "key": f"vimm:{vault_id}",
        "title": title,
        "year": item.get("year") or 0,
        "isSeries": False,
        "kind": "game",
        "sources": [{
            "title": title,
            "indexer": "Vimm's Lair",
            "size": item.get("size_bytes", 0),
            "sizeHuman": "",
            "seeders": 1,
            "leechers": 0,
            "magnet": f"{PUBLIC_BASE}/api/rom/{vault_id}#ejs={system}",
            "quality": "TOUCH",
            "source": platform,
            "codec": "",
            "audio": "",
            "group": "",
            "webSafe": True,
            "published": "",
        }],
        "best": 0,
        "seeders": 1,
        "art": {
            # vimm.net/vault/art/<id> -- what this used to point at -- is a 404
            # for every entry. The real art is behind image.php, needs a
            # vimm.net Referer, and sends no CORP header, so it is served
            # through this origin instead. See api_art.
            "poster": f"{PUBLIC_BASE}/api/art/{vault_id}",
            "found": True,
        },
        "groups": ["games"],
        "adult": False,
        "instant": True,
        "popular": 0,
        "platform": platform,
    }


def clean_title(raw: str) -> str:
    """Titles come out of the scraper still HTML-encoded.

    2,122 of them carry `&#039;` or `&amp;` because they were lifted from
    vimm.net's markup and never decoded. That is invisible on a page built with
    innerHTML -- the browser decodes them again on the way in -- and is exactly
    why it went unnoticed. It is very visible anywhere else: a JSON consumer
    that sets textContent, which is what yarrit.com does, renders a literal
    "Arkista&#039;s Ring".

    So decode once here, at the edge of the catalogue, and let every caller
    escape for its own output context.
    """
    return html.unescape(raw or "")


def load_catalog():
    global _catalog, _catalog_loaded
    if CATALOG_PATH.exists():
        mtime = CATALOG_PATH.stat().st_mtime
        if mtime > _catalog_loaded:
            try:
                with open(CATALOG_PATH) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                # The scraper rewrites this file in place, and it is 14MB, so
                # there is a window on every pass where it is half a document.
                # Reading it then raises, and because this runs on the request
                # path that surfaced as a 500 -- which EmulatorJS reports to
                # the player as "Network Error", with the file perfectly valid
                # a second later.
                #
                # Keep the catalogue already in memory and do not advance the
                # mtime, so the next request tries again. A torn read means the
                # writer is mid-flight, which is a reason to wait rather than
                # an error anybody can act on. The scraper writes atomically
                # now, so this should be unreachable; it is the belt to that
                # brace, because the cost of being wrong is the whole site.
                print(f"catalogue not readable yet ({e}); keeping {len(_catalog)} entries")
                return _catalog
            _catalog = {}
            for item in data.get("items", []):
                vid = item.get("vault_id")
                if vid and item.get("core"):
                    # Decode here, once, so that search, the pages and the JSON
                    # APIs all see the same real title. Doing it per-endpoint
                    # left the FILTER matching encoded text, which is why a
                    # search for "arkista's ring" returned nothing.
                    item["title"] = clean_title(item.get("title", ""))
                    # Resolved once, here, so that the player, the search
                    # cards and the browser all answer "can this play?" from
                    # the same value instead of each re-deriving it and
                    # disagreeing.
                    item["ejs"] = ejs_core(item.get("core", ""))
                    _catalog[vid] = item
            _catalog_loaded = mtime
    return _catalog


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Created on startup, not at import. Importing this module must not touch
    # the filesystem -- that is what makes it testable off the deployment host.
    ROM_CACHE.mkdir(parents=True, exist_ok=True)
    ART_CACHE.mkdir(parents=True, exist_ok=True)
    load_catalog()
    yield


app = FastAPI(title="Vimm Vault API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def cross_origin_isolation(request, call_next):
    """Put the page in a cross-origin isolated context.

    Without these two headers the browser does not expose SharedArrayBuffer,
    and EmulatorJS refuses to start at all when EJS_threads is on -- it prints
    "Error for site owner / Check console" over the canvas and the game never
    boots. That was the whole of the outage: the play page asked for threads
    and the server never granted the context that makes them legal.

    COEP is `credentialless` rather than `require-corp` deliberately. Vimm's
    box art and screenshots are fetched from vimm.net and carry no
    Cross-Origin-Resource-Policy header, so require-corp would isolate the page
    by blocking every image on it. `credentialless` sends those no-cors
    requests without credentials instead, which is exactly right for public
    artwork. Safari does not implement it and so is not isolated -- which is
    why the page feature-detects rather than assuming (see play_page).

    CORP is `cross-origin` because yarrit.com embeds ROMs and art from here.
    Same-origin is the default, and it would break that the moment yarrit.com
    turns on a COEP of its own.
    """
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "credentialless"
    response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    return response


async def scrape_vimm_token(vault_id: str) -> Optional[dict]:
    url = f"https://vimm.net/vault/{vault_id}?p=play"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": USER_AGENT},
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

        core_m = re.search(r"EJS_core='([^']+)'", html)
        url_m = re.search(r"EJS_gameUrl='([^']+)'", html)
        title_m = re.search(r"<title>Play ([^<]+) for", html)
        bg_m = re.search(r"EJS_backgroundImage='([^']+)'", html)

        return {
            "core": core_m.group(1) if core_m else None,
            "game_url": url_m.group(1) if url_m else None,
            "title": title_m.group(1) if title_m else None,
            "background": bg_m.group(1) if bg_m else None,
        }
    except Exception as e:
        print(f"Scrape error {vault_id}: {e}")
        return None


async def fetch_rom_bytes(game_url: str, vault_id: str) -> Optional[tuple]:
    referer = f"https://vimm.net/vault/{vault_id}?p=play"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(game_url, headers={
                "User-Agent": USER_AGENT,
                "Referer": referer,
            }, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    return None
                zip_data = await resp.read()
    except Exception as e:
        print(f"Download error: {e}")
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            rom_files = [f for f in zf.namelist() if not f.endswith(".txt") and not f.endswith("/")]
            if not rom_files:
                return None
            rom_filename = rom_files[0]
            return zf.read(rom_filename), rom_filename
    except Exception as e:
        print(f"Extract error: {e}")
        return None


def get_cached_rom(vault_id: str) -> Optional[tuple]:
    cache_file = ROM_CACHE / f"{vault_id}.rom"
    meta_file = ROM_CACHE / f"{vault_id}.meta"
    if cache_file.exists() and meta_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL:
            with open(meta_file) as f:
                meta = json.load(f)
            return cache_file.read_bytes(), meta.get("filename", "rom.bin")
    return None


def cache_rom(vault_id: str, rom_bytes: bytes, filename: str):
    ROM_CACHE.mkdir(parents=True, exist_ok=True)
    (ROM_CACHE / f"{vault_id}.rom").write_bytes(rom_bytes)
    (ROM_CACHE / f"{vault_id}.meta").write_text(json.dumps({"filename": filename}))


@app.get("/api/catalog")
async def api_catalog(q: str = "", platform: str = "", limit: int = 100, offset: int = 0):
    cat = load_catalog()
    items = list(cat.values())

    if platform:
        items = [i for i in items if (i.get("core") or "").lower() == platform.lower()]

    if q:
        terms = q.lower().split()
        items = [i for i in items if all(t in (i.get("title") or "").lower() for t in terms)]

    total = len(items)
    page = items[offset:offset + limit]
    return {"total": total, "offset": offset, "limit": limit, "items": page}


@app.get("/api/platforms")
async def api_platforms():
    cat = load_catalog()
    counts = {}
    for item in cat.values():
        core = item.get("core", "unknown")
        counts[core] = counts.get(core, 0) + 1
    return {"platforms": sorted(counts.items(), key=lambda x: -x[1])}


@app.get("/api/game/{vault_id}")
async def api_game(vault_id: str):
    cat = load_catalog()
    item = cat.get(vault_id)
    if not item:
        raise HTTPException(404, "Game not found")
    return item


async def _get_rom(vault_id: str):
    cat = load_catalog()
    item = cat.get(vault_id)
    if not item:
        raise HTTPException(404, "Game not found")

    cached = get_cached_rom(vault_id)
    if cached:
        return cached

    token_data = await scrape_vimm_token(vault_id)
    if not token_data or not token_data.get("game_url"):
        raise HTTPException(502, "Could not get ROM URL from Vimm")

    result = await fetch_rom_bytes(token_data["game_url"], vault_id)
    if not result:
        raise HTTPException(502, "Could not download ROM")

    rom_bytes, filename = result
    cache_rom(vault_id, rom_bytes, filename)
    return rom_bytes, filename


@app.get("/api/rom/{vault_id}")
async def api_rom(vault_id: str):
    rom_bytes, filename = await _get_rom(vault_id)
    return Response(
        content=rom_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(len(rom_bytes)),
        },
    )


@app.head("/api/rom/{vault_id}")
async def api_rom_head(vault_id: str):
    """EmulatorJS asks HEAD first, to size its download bar.

    Only GET was declared, so that probe answered 405 and put an error in the
    console of every play page. The catalogue's size_bytes cannot stand in
    here: it is the size of the zip vimm.net serves, not of the ROM inside it
    -- 24,576 against 40,976 for vault 4 -- and a Content-Length that
    disagrees with the body is worse than none.

    So it resolves the ROM for real and returns the length with no body. That
    costs a fetch on a cold entry, but the result is cached and the GET that
    follows a moment later is then free, so nothing is downloaded twice.
    """
    rom_bytes, filename = await _get_rom(vault_id)
    return Response(
        status_code=200,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(len(rom_bytes)),
            "Accept-Ranges": "none",
        },
    )


def download_url_for(item: dict) -> str:
    """Where the file itself lives, from the catalogue rather than a scrape.

    _get_rom works the URL out by re-reading the PLAY page and pulling
    EJS_gameUrl off it, which is fine for something playable and useless for
    anything else: an Xbox 360 entry has no play page, so that lookup returned
    nothing and every download 502'd. The catalogue has held the real
    download_url all along.

    download_url is preferred and game_url is the fallback, which is the
    opposite of what looks right. The stored game_url carries a token
    vimm.net's data.php minted during the scrape and those EXPIRE: measured
    2026-08-12, the stored one for vault 78221 answered 404 while the plain
    ?mediaId= form answered 200 with 418MB. A stale token is worse than no URL
    because it fails after the response has already begun.
    """
    return (item.get("download_url") or item.get("game_url") or "").strip()


async def open_vimm_download(url: str, vault_id: str):
    """Open the transfer and CHECK IT before anything is sent to the client.

    The obvious shape -- return a StreamingResponse whose generator does the
    fetching -- cannot report an error. FastAPI sends the 200 and the headers
    the moment the response object exists, so an upstream failure inside the
    generator arrives as a truncated body with a success status. That is
    exactly what happened: vimm.net answered 429 while its own scraper was
    running and the browser was handed a 200 with zero bytes, which reads as a
    broken file rather than as "slow down".

    So the request is opened here, the status is checked here, and only a
    response that is really going to deliver bytes becomes a StreamingResponse.
    The session is handed to the caller to close when the body is done.
    """
    timeout = aiohttp.ClientTimeout(total=None, sock_read=120)
    session = aiohttp.ClientSession(timeout=timeout)
    try:
        resp = await session.get(url, headers={
            "User-Agent": USER_AGENT,
            # Their download host answers 400 without this.
            "Referer": f"https://vimm.net/vault/{vault_id}",
        })
    except Exception as e:
        await session.close()
        raise HTTPException(502, f"Could not reach Vimm: {e}") from e

    if resp.status != 200:
        detail = {
            429: "Vimm is rate-limiting this server right now; try again shortly",
        }.get(resp.status, f"Vimm answered {resp.status} for this file")
        resp.release()
        await session.close()
        raise HTTPException(502, detail)
    return session, resp


@app.get("/api/download/{vault_id}")
async def api_download(vault_id: str):
    """The file as vimm.net serves it -- archive and all, never unpacked.

    A download is not a ROM handed to an emulator: unpacking it here would cost
    the whole file in memory to hand back something the person did not ask for,
    and for a disc image there is nothing to unpack anyway.
    """
    cat = load_catalog()
    item = cat.get(vault_id)
    if not item:
        raise HTTPException(404, "Game not found")

    url = download_url_for(item)
    if not url:
        # Only now is a scrape worth the round trip, and it still only helps
        # for something with a play page.
        token = await scrape_vimm_token(vault_id)
        url = (token or {}).get("game_url") or ""
    if not url:
        raise HTTPException(502, "No download URL for this entry")

    session, resp = await open_vimm_download(url, vault_id)

    filename = item.get("filename") or f"{vault_id}.bin"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    }
    # The REAL length, from the transfer that is about to happen. Never
    # size_bytes: that is the uncompressed disc image -- 4,584,877,588 for
    # vault 78221 -- while the file served is a 7z of 418,471,928, and
    # promising the first while sending the second is a broken download.
    if resp.headers.get("Content-Length"):
        headers["Content-Length"] = resp.headers["Content-Length"]

    async def body():
        try:
            async for chunk in resp.content.iter_chunked(256 * 1024):
                yield chunk
        finally:
            resp.release()
            await session.close()
    # NO Content-Length from size_bytes. That field is the size of the
    # UNCOMPRESSED disc image -- 4,584,877,588 for vault 78221 -- while the
    # file vimm.net actually serves is a 7z of 418,471,928. Declaring the
    # first and sending the second is a promise the transfer cannot keep, and
    # the browser reports it as a broken download rather than as our error.
    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers=headers,
    )


@app.get("/api/save-to-romm/{vault_id}")
async def api_save_to_romm(vault_id: str):
    cat = load_catalog()
    item = cat.get(vault_id)
    if not item:
        raise HTTPException(404, "Game not found")

    core = item.get("core", "")
    romm_platform = CORE_TO_ROMM.get(core)
    if not romm_platform:
        raise HTTPException(400, f"No ROMM platform mapping for core={core}")

    target_dir = ROMM_LIBRARY / romm_platform
    target_dir.mkdir(parents=True, exist_ok=True)

    rom_bytes, filename = await _get_rom(vault_id)
    output_path = target_dir / filename
    output_path.write_bytes(rom_bytes)

    return {"status": "saved", "path": str(output_path), "size": len(rom_bytes)}


ART_TYPES = ("box", "screen")


@app.get("/api/art/{vault_id}")
async def api_art(vault_id: str, type: str = "box"):
    """Box art and screenshots, proxied rather than hotlinked.

    Three separate reasons, any one of which is enough:

      * vimm.net serves art only to requests carrying a vimm.net Referer, so a
        direct <img src> from another origin gets nothing.
      * the catalogue stores the URL as a site-relative path, which resolves
        against the wrong host everywhere it is used.
      * under COEP a cross-origin image with no CORP header is blocked, and
        that is every image vimm.net serves.

    Proxying answers all three at once, and gives yarrit.com a poster URL on an
    origin that sends CORS.
    """
    if type not in ART_TYPES:
        raise HTTPException(400, f"type must be one of {ART_TYPES}")
    cat = load_catalog()
    if vault_id not in cat:
        raise HTTPException(404, "Game not found")

    cache_file = ART_CACHE / f"{vault_id}-{type}.img"
    meta_file = ART_CACHE / f"{vault_id}-{type}.type"
    if cache_file.exists() and meta_file.exists():
        return Response(
            content=cache_file.read_bytes(),
            media_type=meta_file.read_text() or "image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    url = f"https://vimm.net/image.php?type={type}&id={vault_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={
                "User-Agent": USER_AGENT,
                "Referer": f"https://vimm.net/vault/{vault_id}",
            }, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    raise HTTPException(404, "No art for this entry")
                data = await resp.read()
                ctype = resp.headers.get("Content-Type", "image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Art error {vault_id}: {e}")
        raise HTTPException(502, "Could not fetch art")

    # An error page is HTML, and caching one would pin a broken image for a
    # week. Only an actual image is worth keeping.
    if ctype.startswith("image/"):
        ART_CACHE.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(data)
        meta_file.write_text(ctype)

    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/stats")
async def api_stats():
    cat = load_catalog()
    cache_count = len(list(ROM_CACHE.glob("*.rom")))
    return {
        "total_games": len(cat),
        "cached_roms": cache_count,
        "platforms": len(set(i.get("core") for i in cat.values())),
    }


def unplayable_page(vault_id: str, safe_title: str, core: str) -> str:
    """Shown for an entry no browser emulator can run.

    A 404 would be wrong -- the entry exists and its download works -- so this
    is a 200 that says which machine it is and offers the thing that does work.
    """
    machine = html.escape(PLATFORM_NAMES.get(core, core or "this system"))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title} - Yarrit ROM Vault</title>
    <style>
        body {{ background: #0a0a0a; color: #eee; font-family: system-ui, sans-serif;
               display: grid; place-items: center; min-height: 100vh; margin: 0; }}
        .box {{ max-width: 30rem; padding: 2rem; text-align: center; }}
        h1 {{ font-size: 1.4rem; margin: 0 0 .75rem; }}
        p {{ color: #999; line-height: 1.5; margin: 0 0 1.5rem; }}
        a {{ display: inline-block; padding: .6rem 1.2rem; border-radius: .5rem;
             text-decoration: none; margin: 0 .25rem; }}
        .go {{ background: #00cc66; color: #000; font-weight: 600; }}
        .plain {{ background: #222; color: #ccc; }}
    </style>
</head>
<body>
    <div class="box">
        <h1>{safe_title}</h1>
        <p>This is a {machine} game, and there is no browser emulator that
           runs {machine}. The download works.</p>
        <a class="go" href="/api/download/{vault_id}">Download</a>
        <a class="plain" href="/">Back to the vault</a>
    </div>
</body>
</html>"""


@app.get("/play/{vault_id}", response_class=HTMLResponse)
async def play_page(vault_id: str):
    cat = load_catalog()
    item = cat.get(vault_id)
    if not item:
        raise HTTPException(404, "Game not found")

    core = item.get("core", "")
    # load_catalog decoded this, so it must be re-escaped for the markup it
    # lands in -- these titles are scraped from someone else's page.
    safe_title = html.escape(item.get("title", "Unknown"))

    system = item.get("ejs", "")
    if not system:
        # Roughly half the catalogue is Xbox 360, PS3, Wii, GameCube, PS2,
        # Dreamcast or CD-i, none of which EmulatorJS can run. Rendering the
        # player anyway asks for a core that does not exist, which 404s behind
        # a loading bar that never finishes -- a page that promised a game and
        # then says nothing at all. Say it instead, and keep the download,
        # which does work.
        return HTMLResponse(unplayable_page(vault_id, safe_title, core), status_code=200)

    # The scraper stores this as a path relative to vimm.net -- an unadorned
    # "/image.php?type=screen&id=666" -- so setting it verbatim asks THIS
    # origin for it and gets a 404 back for the 9,460 entries that have one.
    # Serve it through /api/art instead: same origin, so it survives COEP, and
    # the proxy sends the Referer vimm.net wants.
    bg_line = ""
    if item.get("background"):
        bg_line = f"EJS_backgroundImage = '/api/art/{vault_id}';"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title} - Yarrit ROM Vault</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #0a0a0a; color: #fff; font-family: system-ui, sans-serif; }}
        .top-bar {{
            position: fixed; top: 0; left: 0; right: 0; z-index: 100;
            background: rgba(10,10,10,0.95); padding: 8px 16px;
            display: flex; align-items: center; gap: 12px;
            backdrop-filter: blur(8px); border-bottom: 1px solid #222;
        }}
        .top-bar a {{ color: #0f0; text-decoration: none; font-size: 14px; }}
        .top-bar .title {{ font-weight: bold; flex: 1; font-size: 14px; }}
        .top-bar button {{
            background: #222; border: 1px solid #444; color: #fff;
            padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;
        }}
        .top-bar button:hover {{ background: #333; }}
        #game-container {{ margin-top: 44px; width: 100vw; height: calc(100vh - 44px); }}
    </style>
</head>
<body>
    <div class="top-bar">
        <a href="/">&larr; Vault</a>
        <span class="title">{safe_title}</span>
        <button onclick="window.open('/api/download/{vault_id}')">Download</button>
    </div>
    <div id="game-container"></div>
    <script>
        EJS_player = '#game-container';
        EJS_core = '{system}';
        EJS_gameUrl = '/api/rom/{vault_id}';
        {bg_line}
        // Threads are only legal in a cross-origin isolated context. Asserting
        // them without one does not degrade -- EmulatorJS aborts the boot with
        // "Error for site owner" and prints the reason to a console nobody has
        // open. So ask the browser whether it granted isolation instead of
        // assuming the headers arrived: a Safari visitor, or anyone behind a
        // proxy that strips COOP/COEP, then gets the single-threaded core and
        // a game that runs. Only ppsspp and dosbox_pure genuinely require
        // threads; every other core here plays fine without them.
        EJS_threads = self.crossOriginIsolated === true;

        // EmulatorJS auto-loads a localization file for the browser's locale.
        // Its bundled en-US.json is an identity map -- "Restart": "Restart" --
        // that predates half the strings in the current emulator, so an
        // English visitor pays a fetch to gain nothing and earns a screenful
        // of "Translation not found" warnings. Skip it for English only;
        // every other locale still gets its real translation. The flag reads
        // backwards because upstream tests `!== false`, not `!== true`.
        if (/^en\\b/i.test(navigator.language || 'en')) EJS_disableAutoLang = false;
    </script>
    <script src="/emulatorjs/data/loader.js"></script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def vault_index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Yarrit ROM Vault</title>
    <style>
        :root { --bg: #0a0a0a; --card: #161616; --accent: #00cc66; --text: #eee; --muted: #888; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, sans-serif; }
        header {
            padding: 24px 20px; text-align: center;
            background: linear-gradient(135deg, #0a1a0a, #0a0a1a);
            border-bottom: 1px solid #222;
        }
        header h1 { font-size: 32px; }
        header p { color: var(--muted); margin-top: 4px; }
        .controls {
            max-width: 1200px; margin: 20px auto; padding: 0 20px;
            display: flex; gap: 12px; flex-wrap: wrap;
        }
        .controls input {
            flex: 1; min-width: 200px; padding: 10px 16px;
            background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
            color: #fff; font-size: 16px; outline: none;
        }
        .controls input:focus { border-color: var(--accent); }
        .controls select {
            padding: 10px 16px; background: #1a1a1a;
            border: 1px solid #333; border-radius: 8px; color: #fff;
        }
        .grid {
            max-width: 1200px; margin: 0 auto; padding: 0 20px 40px;
            display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 12px;
        }
        .card {
            background: var(--card); border-radius: 8px; padding: 14px;
            border: 1px solid #222; transition: all 0.15s;
        }
        .card:hover { border-color: var(--accent); transform: translateY(-2px); }
        .card h3 { font-size: 14px; margin-bottom: 6px; line-height: 1.3; }
        .card h3 a { color: var(--text); text-decoration: none; }
        .card h3 a:hover { color: var(--accent); }
        .card .meta { color: var(--muted); font-size: 12px; display: flex; justify-content: space-between; }
        .card .actions { margin-top: 8px; display: flex; gap: 6px; }
        .card .actions a {
            font-size: 11px; padding: 3px 8px; border-radius: 4px;
            text-decoration: none; background: #222; color: #ccc;
        }
        .card .actions a.play { background: var(--accent); color: #000; font-weight: bold; }
        .card .actions a:hover { opacity: 0.8; }
        .load-more { text-align: center; padding: 20px; }
        .load-more button {
            padding: 10px 30px; background: var(--accent); border: none;
            border-radius: 8px; color: #000; font-weight: bold; cursor: pointer;
            font-size: 14px;
        }
        .load-more button:hover { opacity: 0.8; }
        .platform-badge {
            display: inline-block; padding: 1px 6px; border-radius: 3px;
            background: #2a2a2a; font-size: 11px; color: var(--accent);
        }
    </style>
</head>
<body>
    <header>
        <h1>Yarrit ROM Vault</h1>
        <p id="stats">Loading...</p>
    </header>
    <div class="controls">
        <input type="text" id="search" placeholder="Search games..." oninput="debounceSearch()">
        <select id="platform" onchange="filterChanged()">
            <option value="">All Platforms</option>
        </select>
    </div>
    <div class="grid" id="grid"></div>
    <div class="load-more" id="loadMore" style="display:none">
        <button onclick="loadMore()">Load More</button>
    </div>
    <script>
        let offset = 0;
        const LIMIT = 100;
        let searchTimeout;
        let totalGames = 0;

        async function loadCatalog(append) {
            const q = document.getElementById('search').value;
            const p = document.getElementById('platform').value;
            const url = `/api/catalog?q=${encodeURIComponent(q)}&platform=${p}&limit=${LIMIT}&offset=${offset}`;
            const resp = await fetch(url);
            const data = await resp.json();

            totalGames = data.total;
            document.getElementById('stats').textContent =
                `${data.total.toLocaleString()} games available | ${offset + data.items.length} loaded`;

            const grid = document.getElementById('grid');
            if (!append) grid.innerHTML = '';

            // Built as nodes, not innerHTML. The API now returns titles with
            // their HTML entities decoded, which is correct for every consumer
            // -- and would make an innerHTML template here an injection route
            // for whatever text vimm.net happens to publish.
            const node = (tag, cls, text) => {
                const e = document.createElement(tag);
                if (cls) e.className = cls;
                if (text !== undefined) e.textContent = text;
                return e;
            };
            const link = (href, cls, text) => {
                const a = node('a', cls, text);
                a.href = href;
                return a;
            };

            data.items.forEach(item => {
                // `ejs` is empty for a system no browser emulator runs -- Xbox
                // 360, PS3, Wii, GameCube, PS2, Dreamcast, CD-i, about half the
                // catalogue. Offering Play on those was a button that led to a
                // loading bar which never finished.
                const canPlay = Boolean(item.ejs);
                const card = node('div', 'card');
                const h3 = node('h3');
                h3.appendChild(link(
                    canPlay ? `/play/${item.vault_id}` : `/api/download/${item.vault_id}`,
                    null, item.title));

                const meta = node('div', 'meta');
                meta.appendChild(node('span', 'platform-badge', item.core || item.platform || '?'));
                meta.appendChild(node('span', null, `#${item.vault_id}`));

                const actions = node('div', 'actions');
                if (canPlay) actions.appendChild(link(`/play/${item.vault_id}`, 'play', 'Play'));
                actions.appendChild(link(`/api/download/${item.vault_id}`, null, 'Download'));

                card.append(h3, meta, actions);
                grid.appendChild(card);
            });

            const hasMore = (offset + data.items.length) < data.total;
            document.getElementById('loadMore').style.display = hasMore ? 'block' : 'none';

            if (offset === 0 && !append) {
                const pResp = await fetch('/api/platforms');
                const pData = await pResp.json();
                const sel = document.getElementById('platform');
                sel.innerHTML = '<option value="">All Platforms</option>';
                pData.platforms.forEach(([name, count]) => {
                    const opt = document.createElement('option');
                    opt.value = name;
                    opt.textContent = `${name} (${count})`;
                    sel.appendChild(opt);
                });
            }
        }

        function loadMore() {
            offset += LIMIT;
            loadCatalog(true);
        }

        function filterChanged() {
            offset = 0;
            loadCatalog(false);
        }

        function debounceSearch() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(filterChanged, 300);
        }

        loadCatalog(false);
    </script>
</body>
</html>"""


# Static files for EmulatorJS
if EMULATORJS_PATH.exists():
    app.mount("/emulatorjs", StaticFiles(directory=str(EMULATORJS_PATH)), name="emulatorjs")


@app.get("/api/search")
async def api_search(q: str = "", limit: int = 100):
    """Search endpoint returning results in Yarrit card format."""
    cat = load_catalog()
    items = list(cat.values())
    
    if q:
        terms = q.lower().split()
        items = [i for i in items if all(t in (i.get("title") or "").lower() for t in terms)]
    
    items = items[:limit]

    cards = [card_for(item) for item in items if playable(item)]
    return {"cards": cards, "facets": None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8098, log_level="info")
