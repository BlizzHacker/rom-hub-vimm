# Vimm.net addon for RomM + ROMarr

This repository contains two pieces that share one catalogue:

1. **A ROM Hub RPP v1 provider** with `search`, `importer`, and `stream`
   capabilities. ROMarr/ROM Hub can search the captured Vimm catalogue, import
   an entry whose page exposed a public download action, and resolve an entry
   whose page exposed **Play Online** to Vimm's hosted player.
2. **A Manifest V3 Chrome extension** that builds the catalogue and adds a
   local Vimm search panel to `romm.moveweight.com` and
   `romarr.moveweight.com`.

The extension records vault id, title, platform, page URL, size, and whether
the detail page exposed Play Online and/or a public GET download action. The
provider revalidates every URL and only accepts HTTPS URLs on `vimm.net` or a
Vimm subdomain.

## Build the Chrome extension

Run on the build host:

```sh
./scripts/build-extension.sh
```

This creates `dist/vimm-catalogue-extension.zip`. For development, load the
unpacked `extension/` directory at `chrome://extensions` with Developer mode
enabled.

## Build a catalogue

Open the extension, choose **Open cataloguer**, and click **Start / Resume**.
The cataloguer:

- checks `robots.txt` before scanning;
- discovers Vault list and detail pages from links already published by Vimm;
- makes one request at a time with a configurable delay (minimum 1000 ms);
- checkpoints after every page so a long pass can resume;
- never opens Play Online sessions or downloads game files during the scan.

Click **Export catalog.json** when ready. Replace this repository's empty
`catalog.json` with the export, or configure the installed provider to read an
absolute path to the exported file.

## Install into ROM Hub

```sh
rom-hub plugin install https://git.moveweight.com/badkid/rom-hub-vimm --ref v0.1.0
rom-hub plugin config vimm-net --set catalog_path=/path/to/catalog.json
rom-hub search "sonic" --platform genesis-slash-megadrive
rom-hub stream vimm-net VAULT_ID
rom-hub import vimm-net VAULT_ID
```

The import command works only when the catalogue entry includes a public GET
download action captured from the live detail page. The stream command works
only when Play Online was captured. A missing action is an explicit refusal,
not a guessed URL.

To make the addon appear in ROMarr's **Hub → Plugins** catalogue, add the
repository's published directory once:

```sh
rom-hub catalog add moveweight-vimm \
  https://git.moveweight.com/badkid/rom-hub-vimm/raw/tag/v0.1.0/catalog/plugins.json
rom-hub catalog refresh
```

## Live MoveWeight integration

The extension runs its integration content script only on:

- `https://romm.moveweight.com/*`
- `https://romarr.moveweight.com/*`

The floating **Vimm** button searches the catalogue stored in Chrome. **Play on
Vimm** opens the hosted player. **Open download page** returns to the Vimm
detail page where its public download control is available. The extension does
not receive or store RomM/ROMarr credentials.

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

All tests and release builds for the MoveWeight deployment are run on the
Proxmox server.
