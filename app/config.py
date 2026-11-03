"""Paths and constants. Everything lives inside the project folder."""
import os
import socket
from datetime import date
from pathlib import Path

APP_NAME = "Avocat AI Moldova"
REPO_URL = "https://github.com/Akulav/ai-lawyer-moldova"
# Set by AvocatAI.exe from its own version; a source checkout reports "dev".
VERSION = os.environ.get("AVOCAT_VERSION") or "dev"

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MAL_DATA", ROOT / "data"))
RUNTIME = ROOT / ".runtime"
DB_PATH = DATA / "lege.db"
MODELS_DIR = DATA / "models"
OLLAMA_DIR = RUNTIME / "ollama"
STATIC = ROOT / "app" / "static"

APP_PORT = int(os.environ.get("MAL_PORT", 18917))
# The interface listens on 127.0.0.1, or on 0.0.0.0 for other devices on the LAN or VPN when the "lan_access"
# setting is on (Setări page). There is no login, so anyone who can reach the port can use the app. The AI
# engine always stays on 127.0.0.1.
LOCAL_HOST, LAN_HOST = "127.0.0.1", "0.0.0.0"
# Exit code that asks whoever started the app (AvocatAI.exe, run.bat) to start it again, e.g. to listen on
# another interface after the LAN setting changed.
RESTART_EXIT_CODE = 3
OLLAMA_PORT_START = int(os.environ.get("MAL_OLLAMA_PORT", 18971))  # first port tried; the next free one is used
OLLAMA_ZIP = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"

# The crawler names itself and links to the project, so the gazette's operators can see who is asking.
USER_AGENT = f"AvocatAI/{VERSION} (+{REPO_URL}; personal local legal index)"
GAZETTE = "https://monitorul.gov.md"

DATA.mkdir(parents=True, exist_ok=True)
RUNTIME.mkdir(parents=True, exist_ok=True)


def port_is_free(port: int) -> bool:
    """Free means nothing answers and we can bind it. On Windows a bind alone can succeed on
    127.0.0.1 while another program listens on 0.0.0.0, which would silently shadow that program."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket() as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def today() -> str:
    return date.today().isoformat()
