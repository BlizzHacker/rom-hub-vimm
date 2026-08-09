"""Read and validate the catalogue produced by the companion extension."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

CATALOG_VERSION = 1
DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "catalog.json"
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_ITEMS = 100_000
VAULT_PATH = re.compile(r"^/vault/(\d+)$")


class CatalogError(Exception):
    """The catalogue is missing or is not safe to consume."""


@dataclass(frozen=True)
class Entry:
    vault_id: str
    title: str
    platform: str
    playable: bool
    downloadable: bool
    page_url: str
    play_url: str = ""
    download_url: str = ""
    filename: str = ""
    size_bytes: int | None = None


def catalog_path(config: dict) -> Path:
    configured = str(config.get("catalog_path") or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_CATALOG


def _https_vimm(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and parts.username is None
        and parts.password is None
        and (host == "vimm.net" or host.endswith(".vimm.net"))
    )


def _page_id(url: str) -> str | None:
    if not _https_vimm(url):
        return None
    match = VAULT_PATH.fullmatch(urlsplit(url).path.rstrip("/"))
    return match.group(1) if match else None


def _play_id(url: str) -> str | None:
    if not _https_vimm(url):
        return None
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if not any(value.lower() == "play" for value in query.get("p", [])):
        return None

    # Vimm currently links players as /vault/?p=play&mediaId=<id>. Keep
    # accepting the older detail-page form for existing catalogues too.
    vault_id = _page_id(url)
    if vault_id:
        return vault_id
    if parts.path.rstrip("/") != "/vault":
        return None
    media_ids = query.get("mediaId", [])
    return media_ids[0] if len(media_ids) == 1 and media_ids[0].isdecimal() else None


def _as_size(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _entry(row: dict) -> Entry | None:
    vault_id = str(row.get("vault_id") or "").strip()
    title = str(row.get("title") or "").strip()
    platform = str(row.get("platform") or "").strip()
    page_url = str(row.get("page_url") or "").strip()
    playable = row.get("playable") is True
    downloadable = row.get("downloadable") is True
    play_url = str(row.get("play_url") or "").strip()
    download_url = str(row.get("download_url") or "").strip()
    filename = str(row.get("filename") or "").strip()

    if not vault_id.isdecimal() or not title or _page_id(page_url) != vault_id:
        return None
    if playable and _play_id(play_url) != vault_id:
        return None
    if not playable:
        play_url = ""
    if downloadable and not _https_vimm(download_url):
        return None
    if not downloadable:
        download_url = ""
        filename = ""

    return Entry(
        vault_id=vault_id,
        title=title,
        platform=platform,
        playable=playable,
        downloadable=downloadable,
        page_url=page_url,
        play_url=play_url,
        download_url=download_url,
        filename=filename,
        size_bytes=_as_size(row.get("size_bytes")),
    )


def load(config: dict) -> list[Entry]:
    path = catalog_path(config)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CatalogError(f"Vimm catalogue is unavailable at {path}: {exc}") from exc
    if size > MAX_CATALOG_BYTES:
        raise CatalogError(
            f"Vimm catalogue is {size} bytes; the limit is {MAX_CATALOG_BYTES}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"Vimm catalogue at {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != CATALOG_VERSION:
        raise CatalogError(f"Vimm catalogue at {path} must use version {CATALOG_VERSION}")
    rows = payload.get("items")
    if not isinstance(rows, list):
        raise CatalogError("Vimm catalogue items must be a list")
    if len(rows) > MAX_ITEMS:
        raise CatalogError(f"Vimm catalogue has {len(rows)} items; limit is {MAX_ITEMS}")

    out: list[Entry] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _entry(row)
        if item is None or item.vault_id in seen:
            continue
        seen.add(item.vault_id)
        out.append(item)
    return out


def by_id(config: dict, source_id: str) -> Entry:
    wanted = str(source_id or "").strip()
    for item in load(config):
        if item.vault_id == wanted:
            return item
    raise CatalogError(f"Vimm vault id {wanted!r} is not in the local catalogue")
