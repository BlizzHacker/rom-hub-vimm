#!/bin/sh
# Fetch the EmulatorJS release the vault server serves from /emulatorjs.
#
# It is NOT vendored. EmulatorJS is GPL-3.0 and this repository is MIT, so
# keeping a copy in the tree would mix the two licences and add 297MB to every
# clone. It is a deploy-time dependency, like a package from a registry.
#
# The server mounts whatever this leaves at VIMM_EMULATORJS (default
# /opt/rom-hub-vimm-v026/emulatorjs). Cores are served from that directory, so
# the `data/` layout must be preserved exactly as the release ships it.
set -eu

VERSION="${EJS_VERSION:-4.2.3}"
DEST="${VIMM_EMULATORJS:-/opt/rom-hub-vimm-v026/emulatorjs}"
URL="https://github.com/EmulatorJS/EmulatorJS/releases/download/v${VERSION}/${VERSION}.7z"

if [ -f "$DEST/data/loader.js" ]; then
	echo "EmulatorJS already present at $DEST"
	exit 0
fi

command -v 7z >/dev/null 2>&1 || {
	echo "7z is required to unpack the EmulatorJS release" >&2
	exit 1
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Fetching EmulatorJS ${VERSION}"
curl -fsSL "$URL" -o "$tmp/ejs.7z"
7z x -o"$tmp/ejs" "$tmp/ejs.7z" >/dev/null

# The release unpacks to a single directory whose name has changed between
# versions, so find the one that actually holds the loader rather than
# assuming.
src="$(dirname "$(find "$tmp/ejs" -name loader.js -path '*/data/*' -print -quit)")"
[ -n "$src" ] || { echo "no data/loader.js in the release" >&2; exit 1; }

mkdir -p "$DEST"
cp -a "$(dirname "$src")/." "$DEST/"
echo "EmulatorJS ${VERSION} installed at $DEST"
