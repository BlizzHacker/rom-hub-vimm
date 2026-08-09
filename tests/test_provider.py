from pathlib import Path

import pytest

from rom_hub.types import SearchResult
from rom_hub_sdk.context import PluginContext

from vimm_net.importer import ImportRefused, Importer
from vimm_net.search import Search
from vimm_net.stream import Stream, StreamRefused

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.json"


class NoHttp:
    def get(self, *_args, **_kwargs):
        raise AssertionError("the local-catalog provider must not open a socket")


def ctx(**extra):
    return PluginContext(config={"catalog_path": str(FIXTURE), **extra}, http=NoHttp())


def result(vault_id, title="Example"):
    return SearchResult(source_id=vault_id, title=title)


def test_search_matches_title_and_maps_platform():
    rows = Search(ctx()).search("example adventure", None, 10)
    assert [row.source_id for row in rows] == ["101"]
    assert rows[0].platform == "gba"
    assert rows[0].extra == {
        "playable": "true",
        "downloadable": "true",
        "vimm_platform": "Game Boy Advance",
    }


def test_search_can_filter_by_romm_slug():
    assert [row.source_id for row in Search(ctx()).search("example", "nes", 10)] == ["303"]


def test_stream_returns_only_captured_play_page():
    target = Stream(ctx()).resolve(result("202", "Stream Example"))
    assert target.kind == "url"
    assert target.target == "https://vimm.net/vault/202?p=play"


def test_stream_refuses_download_only_entry():
    with pytest.raises(StreamRefused, match="Play Online"):
        Stream(ctx()).resolve(result("303"))


def test_importer_returns_captured_public_download():
    plan = Importer(ctx()).plan(result("101", "Example Adventure"))
    assert plan.platform == "gba"
    assert plan.collection == "Vimm.net"
    assert plan.files[0].url == "https://download2.vimm.net/?mediaId=9001"
    assert plan.files[0].filename == "Example Adventure.7z"


def test_importer_refuses_stream_only_entry():
    with pytest.raises(ImportRefused, match="visible public download"):
        Importer(ctx()).plan(result("202"))


def test_operator_can_disable_downloads():
    with pytest.raises(ImportRefused, match="disabled"):
        Importer(ctx(allow_downloads=False)).plan(result("101"))

