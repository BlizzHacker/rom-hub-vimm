"""The vault web server: cross-origin isolation, titles, cards, art.

The isolation tests are the important ones. vimm.yarrit.com spent a period
serving a play page that could never start a game: it asked EmulatorJS for
threads while the response carried no COOP/COEP, so the browser withheld
SharedArrayBuffer and the emulator aborted with "Error for site owner" written
over the canvas. Nothing in the server was unhealthy and nothing was logged --
the failure existed only in the browser. These pin the two halves of the fix so
it cannot silently regress into the same invisible outage.
"""

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="the vault server needs FastAPI")
from fastapi.testclient import TestClient  # noqa: E402


CATALOG = {
    "items": [
        {
            "vault_id": "4",
            "title": "Arkista&#039;s Ring",
            "core": "nes",
            "platform": "Nintendo",
            "size_bytes": 24576,
            "background": "/image.php?type=screen&id=4",
            "playable": True,
        },
        {
            "vault_id": "9",
            "title": "Advanced Dungeons &amp; Dragons",
            "core": "psx",
            "platform": "PlayStation",
            "size_bytes": 500,
            "background": None,
            "playable": True,
        },
        {
            # Vimm spells it lower-case; EmulatorJS wants segaCD.
            "vault_id": "12",
            "title": "Sonic CD",
            "core": "segacd",
            "platform": "Sega CD",
            "size_bytes": 900,
            "playable": True,
        },
        {
            # A real entry on a machine no browser emulates. Downloadable,
            # never playable.
            "vault_id": "34",
            "title": "Some PS2 Game",
            "core": "ps2",
            "platform": "PlayStation 2",
            "size_bytes": 4000,
            "playable": True,
        },
        {
            # A system no browser will emulate. It must stay in the catalogue
            # -- the entry is real -- and stay off anything that claims to play.
            "vault_id": "77",
            "title": "Some Xbox 360 Thing",
            "core": "x360-d",
            "platform": "Xbox 360",
            "size_bytes": 0,
            "playable": False,
        },
    ]
}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(CATALOG))
    monkeypatch.setenv("VIMM_CATALOG", str(catalog))
    monkeypatch.setenv("VIMM_ROM_CACHE", str(tmp_path / "rom"))
    monkeypatch.setenv("VIMM_ART_CACHE", str(tmp_path / "art"))
    monkeypatch.setenv("VIMM_EMULATORJS", str(tmp_path / "emulatorjs"))
    monkeypatch.setenv("VIMM_PUBLIC_BASE", "https://vault.example/")

    import importlib

    import vault.server as mod

    return importlib.reload(mod)


@pytest.fixture()
def client(server):
    with TestClient(server.app) as c:
        yield c


# --- cross-origin isolation -------------------------------------------------


def test_play_page_is_cross_origin_isolated(client):
    """Both headers, or the browser does not expose SharedArrayBuffer."""
    r = client.get("/play/4")
    assert r.status_code == 200
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert r.headers["cross-origin-embedder-policy"] == "credentialless"


def test_rom_is_readable_by_another_origin(client, server, monkeypatch):
    """yarrit.com embeds these, so same-origin CORP would break it."""
    monkeypatch.setattr(server, "_get_rom", _fake_rom)
    r = client.get("/api/rom/4", headers={"Origin": "https://yarrit.com"})
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"
    assert r.headers["access-control-allow-origin"] == "*"


def test_threads_are_feature_detected_not_asserted(client):
    """The bug was `EJS_threads = true` on a page that might not be isolated.

    Asserting threads without isolation does not degrade to a slower emulator
    -- EmulatorJS refuses to boot at all. Safari does not implement
    `credentialless`, so a page that assumes the header worked is a page that
    is broken on Safari.
    """
    body = client.get("/play/4").text
    assert "EJS_threads = self.crossOriginIsolated === true;" in body
    assert "EJS_threads = true" not in body


def test_play_page_sets_config_before_loading_emulatorjs(client):
    """EmulatorJS reads its globals as the loader runs; order is the API."""
    body = client.get("/play/4").text
    assert body.index("EJS_core") < body.index("emulatorjs/data/loader.js")


def test_english_skips_the_identity_translation_file(client):
    """en-US.json maps every string to itself and is missing the new ones.

    Loading it buys nothing and logs a "Translation not found" line per absent
    key. Other locales must keep their real translations, so the skip is
    conditional rather than a blanket disable.
    """
    body = client.get("/play/4").text
    assert "EJS_disableAutoLang = false" in body
    assert "navigator.language" in body


# --- titles -----------------------------------------------------------------


def test_titles_are_decoded_once_at_load(client):
    """2,122 catalogue titles arrive still HTML-encoded from the scraper."""
    assert client.get("/api/game/4").json()["title"] == "Arkista's Ring"
    assert client.get("/api/game/9").json()["title"] == "Advanced Dungeons & Dragons"


def test_search_matches_the_decoded_title(client):
    """The encoded form is why searching for an apostrophe found nothing."""
    hits = client.get("/api/catalog", params={"q": "arkista's"}).json()
    assert [i["vault_id"] for i in hits["items"]] == ["4"]


def test_decoded_titles_are_escaped_back_into_the_page(client):
    """Decoding at the edge means every HTML consumer must re-escape."""
    body = client.get("/play/9").text
    assert "Advanced Dungeons &amp; Dragons" in body
    assert "Advanced Dungeons & Dragons" not in body


# --- search cards -----------------------------------------------------------


def test_card_carries_the_declared_core(client):
    """A core must never be inferred downstream from a file extension.

    `.bin` is Colecovision, Atari 2600 and Mega Drive at once, and the wrong
    core boots happily and then runs a black screen with no error. vimm.net
    declares the system, so the answer travels with the URI.
    """
    cards = client.get("/api/search", params={"q": "arkista"}).json()["cards"]
    assert cards[0]["sources"][0]["magnet"].endswith("/api/rom/4#ejs=nes")


def test_card_poster_points_at_this_origin(client):
    """vimm.net/vault/art/<id> -- the old value -- is a 404 for every entry."""
    card = client.get("/api/search", params={"q": "arkista"}).json()["cards"][0]
    assert card["art"]["poster"] == "https://vault.example/api/art/4"


def test_unplayable_systems_stay_off_the_shelf(client):
    """`instant: true` promises it plays. There is no Xbox 360 core."""
    titles = [c["title"] for c in client.get("/api/search").json()["cards"]]
    assert "Some Xbox 360 Thing" not in titles
    assert all(c["instant"] for c in client.get("/api/search").json()["cards"])


def test_platform_is_a_display_name_not_a_core_id(client):
    cards = {c["title"]: c for c in client.get("/api/search").json()["cards"]}
    assert cards["Arkista's Ring"]["platform"] == "NES"
    assert cards["Advanced Dungeons & Dragons"]["platform"] == "PlayStation"


# --- which systems can actually play ----------------------------------------


def test_core_case_is_corrected_for_sega_cd(client):
    """Vimm writes `segacd`; EmulatorJS has `segaCD` and 404s on the other."""
    assert client.get("/api/game/12").json()["ejs"] == "segaCD"
    assert "EJS_core = 'segaCD';" in client.get("/play/12").text


def test_systems_with_no_emulator_are_not_offered_as_playable(client):
    """EmulatorJS has no PS2 core, and half the catalogue is machines like it."""
    assert client.get("/api/game/34").json()["ejs"] == ""
    titles = [c["title"] for c in client.get("/api/search").json()["cards"]]
    assert "Some PS2 Game" not in titles


def test_an_unplayable_entry_says_so_instead_of_loading_forever(client):
    """It is a real entry with a working download, so not a 404 -- an answer."""
    r = client.get("/play/34")
    assert r.status_code == 200
    assert "no browser emulator" in r.text
    assert "/api/download/34" in r.text
    assert "loader.js" not in r.text


def test_unknown_core_never_reaches_the_player(server):
    """An unrecognised name must resolve to "", not pass through and 404."""
    assert server.ejs_core("x360-d") == ""
    assert server.ejs_core("dreamcast") == ""
    assert server.ejs_core("") == ""
    assert server.ejs_core("segaMD") == "segaMD"


# --- art --------------------------------------------------------------------


def test_art_rejects_an_arbitrary_type(client):
    """The type reaches an upstream URL, so it is an allowlist, not a passthrough."""
    assert client.get("/api/art/4", params={"type": "../../etc/passwd"}).status_code == 400


def test_art_for_an_unknown_entry_is_404_not_an_upstream_fetch(client):
    assert client.get("/api/art/999999").status_code == 404


def test_background_is_served_from_this_origin(client):
    """The catalogue stores it relative to vimm.net, which resolves here."""
    body = client.get("/play/4").text
    assert "EJS_backgroundImage = '/api/art/4';" in body
    assert "image.php" not in body


def test_no_background_emits_no_background_global(client):
    """An empty string is still a string, and EmulatorJS treats it as an image."""
    assert "EJS_backgroundImage" not in client.get("/play/9").text


async def _fake_rom(vault_id: str):
    return b"NES\x1a", "test.nes"


# --- reading a catalogue that is being rewritten ----------------------------


def test_a_half_written_catalogue_keeps_the_last_good_one(client, server, tmp_path):
    """The scraper rewrites 14MB in place; a read can land mid-write.

    That raised JSONDecodeError on the request path, which became a 500, which
    EmulatorJS shows the player as "Network Error" -- on a game that works
    perfectly a second later. The catalogue already in memory is the right
    answer while a writer is mid-flight.
    """
    assert client.get("/api/game/4").json()["title"] == "Arkista's Ring"

    catalog = tmp_path / "catalog.json"
    torn = json.dumps(CATALOG)[: len(json.dumps(CATALOG)) // 2]
    catalog.write_text(torn)
    # Force the mtime check to notice the file changed.
    import os

    os.utime(catalog, (9e9, 9e9))

    assert client.get("/api/game/4").status_code == 200
    assert client.get("/api/game/4").json()["title"] == "Arkista's Ring"


def test_the_catalogue_is_picked_up_again_once_the_write_finishes(client, tmp_path):
    """A torn read must not latch. The next good file has to be loaded."""
    catalog = tmp_path / "catalog.json"
    import os

    catalog.write_text(json.dumps(CATALOG)[:200])
    os.utime(catalog, (9e9, 9e9))
    assert client.get("/api/game/4").status_code == 200  # served from memory

    grown = json.loads(json.dumps(CATALOG))
    grown["items"].append({
        "vault_id": "555", "title": "Later Arrival", "core": "gba",
        "platform": "Game Boy Adv", "size_bytes": 1, "playable": True,
    })
    catalog.write_text(json.dumps(grown))
    os.utime(catalog, (9e9 + 10, 9e9 + 10))

    assert client.get("/api/game/555").json()["title"] == "Later Arrival"
