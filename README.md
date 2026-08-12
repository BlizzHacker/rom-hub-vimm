# Vimm.net catalogue and ROM Hub provider

By **BlizzHacker**.

This project is library-neutral. It works with ROM Hub consumers such as
RomM, Gaseous, Retrom, LaunchBox-oriented workflows, ROMarr, Yarr.it, and
other hosted or desktop libraries.

It contains two pieces that share one catalogue:

1. A ROM Hub RPP v1 provider with `search`, `importer`, and `stream`
   capabilities. Any compatible frontend can search the captured Vimm
   catalogue, import an entry whose page exposed a download action, and launch
   an entry whose page exposed **Play Online**.
2. A Manifest V3 Chromium extension for Brave and Chrome. It builds the
   catalogue and, on operator-approved hosted library origins, adds a local
   instant search panel with exact play and download actions.

The extension records vault id, media id, title, platform, page URL, filename,
size, and whether the detail page exposed Play Online and/or a download action.
The provider validates every captured URL and accepts only HTTPS Vimm URLs.

## Build the Chromium extension

Build and test on the Proxmox build host:

```sh
./scripts/build-extension.sh
```

This creates `dist/vimm-catalogue-extension.zip`. For development, load the
unpacked `extension/` directory at `brave://extensions` or
`chrome://extensions` with Developer mode enabled.

## Build a catalogue

Open the extension, choose **Open cataloguer**, and click **Start / Resume**.
The cataloguer:

- checks `robots.txt` before scanning;
- discovers Vault list and detail pages from links published by Vimm;
- makes one request at a time with a configurable delay and backs off after
  HTTP 429 responses;
- checkpoints after every page and resumes after browser restarts;
- records exact play and download actions without activating either during the
  catalogue pass.

The popup searches completed records immediately, so instant actions become
available during the scan. Export `catalog.json` when the pass is complete and
place it at the path configured for the ROM Hub plugin.

## Enable any hosted library

Open the extension popup and add the full origin of each hosted library, for
example `https://games.example.com`. Chromium asks for permission for that site
only. Reload the library tab once, then use the floating **Vimm** button.

No MoveWeight domain is built in. The same mechanism works for self-hosted
RomM, Gaseous, Retrom, ROMarr, Yarr.it, or another browser-based library.
LaunchBox and other desktop clients consume the exported catalogue through ROM
Hub or their own adapter instead of browser injection. The extension stores no
library credentials.

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

Any hosted Yarr.it deployment can use the extension panel immediately. A
Yarr.it or other server-side integration can consume the same RPP v1 provider
and exported `catalog.json`; no private domain or vendor-specific API is
required.

## The vault server

`vault/server.py` is a small FastAPI application that turns the captured
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
node --check extension/background.js
node --check extension/vimm-capture.js
node --check extension/integration.js
node --check extension/popup.js
node --check extension/cataloguer.js
```

All release builds are produced on the Proxmox server.
