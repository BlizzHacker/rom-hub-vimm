import json
from pathlib import Path

import pytest

from vimm_net.catalog import CatalogError, by_id, load

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.json"


def config(path=FIXTURE):
    return {"catalog_path": str(path)}


def test_loads_valid_entries():
    entries = load(config())
    assert [entry.vault_id for entry in entries] == ["101", "202", "303"]
    assert entries[0].download_url.startswith("https://download2.vimm.net/")


def test_unknown_id_is_explicit():
    with pytest.raises(CatalogError, match="999"):
        by_id(config(), "999")


def test_rejects_cross_site_download_url(tmp_path):
    payload = json.loads(FIXTURE.read_text())
    payload["items"][0]["download_url"] = "https://evil.example/rom.7z"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload))
    assert [entry.vault_id for entry in load(config(path))] == ["202", "303"]


def test_rejects_play_url_for_a_different_vault_id(tmp_path):
    payload = json.loads(FIXTURE.read_text())
    payload["items"][1]["play_url"] = "https://vimm.net/vault/?p=play&mediaId=999"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload))
    assert [entry.vault_id for entry in load(config(path))] == ["101", "303"]


def test_accepts_current_and_legacy_vimm_player_urls():
    entries = load(config())
    assert entries[0].play_url == "https://vimm.net/vault/?p=play&mediaId=101"
    assert entries[1].play_url == "https://vimm.net/vault/202?p=play"


def test_duplicate_ids_do_not_override_first_capture(tmp_path):
    payload = json.loads(FIXTURE.read_text())
    payload["items"].append({**payload["items"][0], "title": "Replacement"})
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload))
    assert by_id(config(path), "101").title == "Example Adventure"
