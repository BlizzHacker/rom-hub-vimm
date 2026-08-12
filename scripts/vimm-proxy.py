#!/usr/bin/env python3
"""
Vimm.net streaming proxy for Yarrit.com.

On-the-fly token scraping + ROM proxy. No storage needed.
"""

import json
import re
import sys
import zipfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs
import threading
import os

ENRICHED_PATH = "/opt/rom-hub-vimm-v026/catalog-enriched.json"
EMULATORJS_PATH = "/opt/rom-hub-vimm-v026/emulatorjs"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
PORT = 8099

# EJS core to emulator mapping
CORE_TO_EMULATOR = {
    "nes": "nes",
    "snes": "snes",
    "n64": "n64",
    "gba": "gba",
    "gb": "gb",
    "gbc": "gbc",
    "genesis": "segaMD",
    "segacd": "segaCD",
    "sega32x": "sega32x",
    "sms": "segaMS",
    "gamegear": "segaGG",
    "psx": "psx",
    "atari2600": "atari2600",
    "atari5200": "atari5200",
    "atari7800": "atari7800",
    "lynx": "atari7800",
    "dc": "dc",
    "saturn": "segaSaturn",
    "nds": "nds",
    "ngc": "ngc",
    "wii": "wii",
    "psp": "psp",
}

def load_catalog():
    """Load enriched catalogue."""
    try:
        with open(ENRICHED_PATH) as f:
            data = json.load(f)
        return {item["vault_id"]: item for item in data.get("items", []) if item.get("core")}
    except Exception as e:
        print(f"Error loading catalog: {e}", file=sys.stderr)
        return {}

def scrape_token(vault_id):
    """Scrape fresh token from Vimm play page."""
    url = f"https://vimm.net/vault/{vault_id}?p=play"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
        
        core_match = re.search(r"EJS_core='([^']+)'", html)
        game_url_match = re.search(r"EJS_gameUrl='([^']+)'", html)
        title_match = re.search(r"<title>Play ([^<]+) for", html)
        bg_match = re.search(r"EJS_backgroundImage='([^']+)'", html)
        
        return {
            "core": core_match.group(1) if core_match else None,
            "game_url": game_url_match.group(1) if game_url_match else None,
            "title": title_match.group(1) if title_match else None,
            "background": bg_match.group(1) if bg_match else None,
        }
    except Exception as e:
        print(f"Error scraping {vault_id}: {e}", file=sys.stderr)
        return None

def download_rom(game_url, vault_id):
    """Download ROM with proper referer."""
    referer = f"https://vimm.net/vault/{vault_id}?p=play"
    req = Request(game_url, headers={
        "User-Agent": USER_AGENT,
        "Referer": referer,
    })
    
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.read()
    except Exception as e:
        print(f"Error downloading {game_url}: {e}", file=sys.stderr)
        return None

def extract_rom_from_zip(zip_data):
    """Extract ROM file from zip."""
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        tmp.write(zip_data)
        tmp_path = tmp.name
    
    try:
        with zipfile.ZipFile(tmp_path) as zf:
            rom_files = [f for f in zf.namelist() if not f.endswith(".txt") and not f.endswith("/")]
            if not rom_files:
                return None, None
            rom_filename = rom_files[0]
            rom_data = zf.read(rom_filename)
            return rom_data, rom_filename
    finally:
        import os
        os.unlink(tmp_path)

class VimmProxyHandler(BaseHTTPRequestHandler):
    catalog = {}
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass
    
    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        
        if parsed.path == "/":
            self.serve_index()
        elif parsed.path == "/play":
            self.serve_player(parsed)
        elif parsed.path == "/rom":
            self.serve_rom(parsed)
        elif parsed.path == "/catalog":
            self.serve_catalog()
        elif parsed.path == "/api/info":
            self.serve_info(parsed)
        elif parsed.path.startswith("/emulatorjs/"):
            self.serve_emulatorjs(parsed)
        else:
            self.send_error(404)
    
    def serve_emulatorjs(self, parsed):
        """Serve EmulatorJS static files."""
        # Remove /emulatorjs/ prefix
        rel_path = parsed.path[len("/emulatorjs/"):]
        file_path = os.path.join(EMULATORJS_PATH, rel_path)
        
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            self.send_error(404)
            return
        
        # Determine content type
        ext = os.path.splitext(file_path)[1].lower()
        content_types = {
            ".js": "application/javascript",
            ".css": "text/css",
            ".html": "text/html",
            ".json": "application/json",
            ".wasm": "application/wasm",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".gif": "image/gif",
        }
        content_type = content_types.get(ext, "application/octet-stream")
        
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            
            self.send_response(200)
            self.send_header("Content-type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print(f"Error serving {file_path}: {e}", file=sys.stderr)
            self.send_error(500)
    
    def serve_index(self):
        """Serve the main catalog page."""
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        html = """<!DOCTYPE html>
<html>
<head>
    <title>Yarrit ROM Vault</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #1a1a1a; color: #fff; }
        h1 { text-align: center; color: #00ff00; }
        .search { max-width: 600px; margin: 20px auto; }
        input { width: 100%; padding: 10px; font-size: 16px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; max-width: 1200px; margin: 20px auto; }
        .card { background: #2a2a2a; padding: 15px; border-radius: 8px; text-align: center; }
        .card a { color: #00ff00; text-decoration: none; font-weight: bold; }
        .card a:hover { text-decoration: underline; }
        .platform { color: #888; font-size: 12px; }
    </style>
</head>
<body>
    <h1>🎮 Yarrit ROM Vault</h1>
    <div class="search">
        <input type="text" id="search" placeholder="Search games..." onkeyup="filterGames()">
    </div>
    <div class="grid" id="games"></div>
    <script>
        fetch('/catalog')
            .then(r => r.json())
            .then(data => {
                const grid = document.getElementById('games');
                data.items.forEach(item => {
                    const card = document.createElement('div');
                    card.className = 'card';
                    card.innerHTML = `
                        <a href="/play?vault_id=${item.vault_id}" target="_blank">${item.title}</a>
                        <div class="platform">${item.platform || item.core}</div>
                    `;
                    card.dataset.title = item.title.toLowerCase();
                    grid.appendChild(card);
                });
            });
        
        function filterGames() {
            const query = document.getElementById('search').value.toLowerCase();
            document.querySelectorAll('.card').forEach(card => {
                card.style.display = card.dataset.title.includes(query) ? 'block' : 'none';
            });
        }
    </script>
</body>
</html>"""
        self.wfile.write(html.encode())
    
    def serve_player(self, parsed):
        """Serve the EmulatorJS player page."""
        params = parse_qs(parsed.query)
        vault_id = params.get("vault_id", [None])[0]
        
        if not vault_id:
            self.send_error(400, "Missing vault_id")
            return
        
        # Get item from catalog
        item = self.catalog.get(vault_id)
        if not item:
            self.send_error(404, "Game not found in catalog")
            return
        
        core = item.get("core")
        title = item.get("title", "Unknown")
        emulator = CORE_TO_EMULATOR.get(core, core)
        
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{title} - Yarrit ROM Vault</title>
    <style>
        body {{ margin: 0; padding: 0; background: #000; }}
        #game {{ width: 100vw; height: 100vh; }}
    </style>
</head>
<body>
    <div id="game"></div>
    <script src="/emulatorjs/data/loader.js"></script>
    <script>
        EJS_player = '#game';
        EJS_core = '{emulator}';
        EJS_gameUrl = '/rom?vault_id={vault_id}';
        EJS_biosUrl = '';
        EJS_language = 'en-US';
    </script>
</body>
</html>"""
        self.wfile.write(html.encode())
    
    def serve_rom(self, parsed):
        """Serve the ROM data (scrape + download on-the-fly)."""
        params = parse_qs(parsed.query)
        vault_id = params.get("vault_id", [None])[0]
        
        if not vault_id:
            self.send_error(400, "Missing vault_id")
            return
        
        print(f"Serving ROM for vault {vault_id}")
        
        # Scrape fresh token
        token_data = scrape_token(vault_id)
        if not token_data or not token_data.get("game_url"):
            self.send_error(500, "Failed to get game URL")
            return
        
        # Download ROM
        zip_data = download_rom(token_data["game_url"], vault_id)
        if not zip_data:
            self.send_error(500, "Failed to download ROM")
            return
        
        # Extract ROM from zip
        rom_data, rom_filename = extract_rom_from_zip(zip_data)
        if not rom_data:
            self.send_error(500, "Failed to extract ROM")
            return
        
        # Determine content type based on extension
        ext = rom_filename.lower().split(".")[-1] if "." in rom_filename else ""
        content_types = {
            "z64": "application/octet-stream",
            "v64": "application/octet-stream",
            "n64": "application/octet-stream",
            "nes": "application/octet-stream",
            "sfc": "application/octet-stream",
            "smc": "application/octet-stream",
            "gb": "application/octet-stream",
            "gbc": "application/octet-stream",
            "gba": "application/octet-stream",
            "bin": "application/octet-stream",
            "iso": "application/octet-stream",
        }
        content_type = content_types.get(ext, "application/octet-stream")
        
        self.send_response(200)
        self.send_header("Content-type", content_type)
        self.send_header("Content-Length", str(len(rom_data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(rom_data)
    
    def serve_catalog(self):
        """Serve the enriched catalog as JSON."""
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        
        catalog_data = {
            "items": list(self.catalog.values())
        }
        self.wfile.write(json.dumps(catalog_data).encode())
    
    def serve_info(self, parsed):
        """Serve info about a specific game."""
        params = parse_qs(parsed.query)
        vault_id = params.get("vault_id", [None])[0]
        
        if not vault_id:
            self.send_error(400, "Missing vault_id")
            return
        
        item = self.catalog.get(vault_id)
        if not item:
            self.send_error(404, "Game not found")
            return
        
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(item).encode())

def run_server():
    """Run the proxy server."""
    print(f"Loading catalog from {ENRICHED_PATH}...")
    VimmProxyHandler.catalog = load_catalog()
    print(f"Loaded {len(VimmProxyHandler.catalog)} games")
    
    server = HTTPServer(("0.0.0.0", PORT), VimmProxyHandler)
    print(f"Vimm proxy server running on port {PORT}")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == "__main__":
    run_server()
