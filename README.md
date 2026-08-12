# Vimm.net catalogue and ROM Hub provider

By **BlizzHacker**.

This project is library-neutral. It works with ROM Hub consumers such as
RomM, Gaseous, Retrom, LaunchBox-oriented workflows, ROMarr, Yarr.it, and
other hosted or desktop libraries.

It contains two pieces that share one catalogue:

1. A ROM Hub RPP v1 provider with `search`, `importer`, and `stream`
   capabilities. Any compatible frontend can search the Vimm catalogue, import
   an entry that exposes a download action, and launch an entry that exposes
   **Play Online**.
2. `vault/`, an optional web server that turns the same catalogue into a
   browsable library with an in-browser player.

The provider validates every URL in the catalogue and accepts only HTTPS Vimm
URLs.

## The catalogue is included. Do not scan for it.

`catalog.json` in this repository is the finished thing: **23,980 entries**,
23,916 of them carrying the EmulatorJS core their vault page declared, plus
title, platform, filename, size and the play and download actions each entry
exposes.

It is committed rather than generated for one reason. A catalogue is built by
walking somebody else's website a page at a time, and there is no good outcome
in every reader of this repository being handed the tool to do that again. The
scan has been run once; the result is here; nobody needs to repeat it.

The extension that produced it is **not public**, and its removal from this
repository's history is deliberate rather than cosmetic.

## Enable any hosted library

No MoveWeight domain is built in. This works with self-hosted RomM, Gaseous,
Retrom, ROMarr, Yarr.it, or another library. LaunchBox and other desktop
clients consume `catalog.json` through ROM Hub or their own adapter.

## Install into ROM Hub

```sh
rom-hub plugin install https://git.moveweight.com/blizzhacker/rom-hub-vimm --ref v0.2.7
rom-hub plugin config vimm-net --set catalog_path=/path/to/catalog.json
rom-hub search "sonic" --platform genesis-slash-megadrive
rom-hub stream vimm-net VAULT_ID
rom-hub import vimm-net VAULT_ID
```

The import command requires a captured download action. The stream command
requires a captured Play Online action. Missing actions are reported rather
than guessed.

To make the provider appear in a ROMarr or ROM Hub plugin directory:

```sh
rom-hub catalog add blizzhacker-vimm \
  https://git.moveweight.com/blizzhacker/rom-hub-vimm/raw/tag/v0.2.7/catalog/plugins.json
rom-hub catalog refresh
```

A Yarr.it or other server-side integration consumes the same RPP v1 provider
and `catalog.json`; no private domain or vendor-specific API is required.

## The vault server

`vault/server.py` is a small FastAPI application that turns the
catalogue into a browsable library with an in-browser player. It is optional --
the ROM Hub provider above works without it -- and it is what runs at
vimm.yarrit.com.

```sh
sh vault/fetch-emulatorjs.sh          # deploy-time dependency, not vendored
VIMM_CATALOG=/path/to/catalog-enriched.json python3 vault/server.py
```

Everything it touches is an environment variable, so nothing about one
deployment is baked in:

| Variable | Default | What it is |
| --- | --- | --- |
| `VIMM_CATALOG` | `/opt/rom-hub-vimm-v026/catalog-enriched.json` | the enriched catalogue |
| `VIMM_EMULATORJS` | `/opt/rom-hub-vimm-v026/emulatorjs` | unpacked EmulatorJS release |
| `VIMM_ROM_CACHE` | `/opt/vimm-vault/cache` | extracted ROMs |
| `VIMM_ART_CACHE` | `/opt/vimm-vault/art-cache` | proxied box art |
| `VIMM_ROMM_LIBRARY` | `/mnt/usb4/romm/library/roms` | where `save-to-romm` writes |
| `VIMM_PUBLIC_BASE` | `https://vimm.yarrit.com` | origin used in URLs given to other sites |

`GET /api/search` returns Yarr.it-shaped cards, so a Yarr.it deployment can
consume this directly. Each card's URI carries the core as a fragment --
`/api/rom/4#ejs=nes` -- because the consumer must never infer a core from a
file extension: `.bin` is Colecovision, Atari 2600 and Mega Drive at once, and
the wrong core boots successfully and then runs a black screen with no error.

### `ejs` is the field that says whether something can play

`GET /api/catalog` adds one field to each entry that is not in the scraped
catalogue: **`ejs`**, the EmulatorJS system id, or `""` for an entry no browser
can run. It is the answer to "can this play?" and it is the field an integrator
should read.

Read it rather than the raw `core` beside it. `core` is what vimm.net's own
player declared, and it is not always an EmulatorJS id: Sega CD is `segacd`
there and `segaCD` in the player, and about half the catalogue names machines
-- Xbox 360, PS3, Wii, GameCube, PS2, Dreamcast, CD-i -- that have no core at
all. Resolving one into the other needs the core list of the EmulatorJS release
being served, which is here and not at the consumer. Of 23,980 entries, 23,916
carry a core the vault could read; the rest are machines with no browser core.

That is how yarrit.com consumes this: its importer reads `ejs`, stores it, and
turns those entries into cards that play in its own player while the rest stay
links to vimm.net. An import that finds no `ejs` produces no playable entries,
which is the correct answer rather than a guess.

### The player must be cross-origin isolated

**If you serve the play page without `Cross-Origin-Opener-Policy: same-origin`
and `Cross-Origin-Embedder-Policy`, no game will start.** EmulatorJS asks for
threads, threads need `SharedArrayBuffer`, and the browser withholds it outside
an isolated context. The failure is silent server-side: the page is a 200, the
service is healthy, nothing is logged, and the only symptom is "Error for site
owner / Check console" printed over the canvas.

The server sends both headers itself, so a direct deployment is correct by
default. A reverse proxy in front of it must pass them through.

COEP is `credentialless` rather than `require-corp` on purpose: vimm.net's
artwork carries no `Cross-Origin-Resource-Policy` header, and require-corp
would isolate the page by blocking every image on it. Safari does not implement
`credentialless`, so the page feature-detects (`self.crossOriginIsolated`)
instead of assuming, and falls back to the single-threaded core rather than
refusing to boot. Only `ppsspp` and `dosbox_pure` genuinely require threads.

## Test

The test suite needs ROM Hub's `src/` on `PYTHONPATH`:

```sh
PYTHONPATH=/opt/rom-hub/src pytest -q
```

All release builds are produced on the Proxmox server.
