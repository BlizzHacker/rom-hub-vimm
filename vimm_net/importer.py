"""Plan an import only for entries captured with a visible public download."""

from __future__ import annotations

import re
from pathlib import PurePath

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .catalog import CatalogError, by_id
from .platforms import platform_for


class ImportRefused(Exception):
    """The catalogue does not prove that this entry has a supported download."""


def _filename(title: str, captured: str) -> str:
    value = PurePath(captured).name if captured else f"{title}.7z"
    value = re.sub(r"[^\w .\-()\[\]+,'!&~@#=]", "_", value, flags=re.UNICODE)
    value = value.strip(". ")[:200]
    if not value:
        value = "vimm-download.7z"
    return value


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        try:
            entry = by_id(self.ctx.config, result.source_id)
        except CatalogError as exc:
            raise ImportRefused(str(exc)) from exc
        if self.ctx.config.get("allow_downloads", True) is not True:
            raise ImportRefused("Vimm downloads are disabled in this plugin's config")
        if not entry.downloadable or not entry.download_url:
            raise ImportRefused(
                f"Vimm vault id {entry.vault_id} was not captured with a visible "
                "public download action"
            )
        platform = platform_for(entry.platform)
        if platform is None:
            raise ImportRefused(
                f"Vimm platform {entry.platform!r} needs an exact RomM mapping"
            )
        return FetchPlan(
            files=[FetchFile(
                url=entry.download_url,
                filename=_filename(entry.title, entry.filename),
                size_bytes=entry.size_bytes,
            )],
            platform=platform,
            collection=self.ctx.config.get("collection") or "Vimm.net",
        )

