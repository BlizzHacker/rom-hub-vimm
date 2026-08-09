"""Exact Vimm label to ROM Hub platform slug mapping. Unknown labels are never guessed."""

PLATFORMS = {
    "Atari 2600": "atari2600",
    "Atari 5200": "atari5200",
    "Atari 7800": "atari7800",
    "Atari Lynx": "lynx",
    "Dreamcast": "dc",
    "Game Boy": "gb",
    "Game Boy Advance": "gba",
    "Game Boy Color": "gbc",
    "Game Gear": "gamegear",
    "GameCube": "ngc",
    "Genesis": "genesis-slash-megadrive",
    "Nintendo 64": "n64",
    "Nintendo DS": "nds",
    "Nintendo Entertainment System": "nes",
    "PlayStation": "psx",
    "PlayStation 2": "ps2",
    "PlayStation 3": "ps3",
    "PlayStation Portable": "psp",
    "Sega 32X": "sega32",
    "Sega CD": "segacd",
    "Sega Master System": "sms",
    "Sega Saturn": "saturn",
    "Super Nintendo": "snes",
    "Super Nintendo Entertainment System": "snes",
    "Wii": "wii",
    "Xbox": "xbox",
}


def platform_for(label: str) -> str | None:
    wanted = str(label or "").strip().casefold()
    for name, slug in PLATFORMS.items():
        if name.casefold() == wanted:
            return slug
    return None
