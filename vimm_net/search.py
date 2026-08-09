"""Search the local catalogue captured by the companion Chrome extension."""

from __future__ import annotations

import re

from rom_hub_sdk import SearchProvider, SearchResult

from .catalog import load
from .platforms import platform_for


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


class Search(SearchProvider):
    def search(self, query: str, platform: str | None, limit: int) -> list[SearchResult]:
        terms = _words(query or "")
        wanted_platform = str(platform or "").strip().casefold()
        ranked = []
        for entry in load(self.ctx.config):
            mapped = platform_for(entry.platform) or entry.platform
            if wanted_platform and wanted_platform not in {
                entry.platform.casefold(), str(mapped).casefold()
            }:
                continue
            title_words = _words(entry.title)
            title_text = " ".join(title_words)
            if terms and not all(term in title_text for term in terms):
                continue
            exact = title_text == " ".join(terms) if terms else False
            prefix = title_text.startswith(" ".join(terms)) if terms else False
            ranked.append(((not exact, not prefix, entry.title.casefold(), entry.vault_id), entry, mapped))

        results = []
        for _, entry, mapped in sorted(ranked, key=lambda row: row[0])[: max(0, limit)]:
            results.append(SearchResult(
                source_id=entry.vault_id,
                title=entry.title,
                platform=mapped,
                size_bytes=entry.size_bytes,
                url=entry.page_url,
                extra={
                    "playable": "true" if entry.playable else "false",
                    "downloadable": "true" if entry.downloadable else "false",
                    "vimm_platform": entry.platform,
                },
            ))
        return results

