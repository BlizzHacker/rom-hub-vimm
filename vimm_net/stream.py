"""Resolve a captured Vimm Play Online entry to Vimm's hosted player page."""

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget

from .catalog import CatalogError, by_id


class StreamRefused(Exception):
    """The catalogue does not mark the entry as playable."""


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        try:
            entry = by_id(self.ctx.config, result.source_id)
        except CatalogError as exc:
            raise StreamRefused(str(exc)) from exc
        if not entry.playable or not entry.play_url:
            raise StreamRefused(
                f"Vimm vault id {entry.vault_id} was not captured with a visible "
                "Play Online action"
            )
        return StreamTarget(
            kind="url",
            target=entry.play_url,
            mime_type="text/html",
            title=entry.title,
            extra={"vault_id": entry.vault_id, "platform": entry.platform},
        )

