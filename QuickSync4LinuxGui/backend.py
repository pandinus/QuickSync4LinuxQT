"""
QuickSync4LinuxGui — Backend
Gemeinsame Logik und CLI-Kommunikation.
"""
import os
import re
import json
import subprocess
import logging
from logging.handlers import RotatingFileHandler

# ─── Konstanten & Standardwerte ───────────────────────────────────────────────
CHECK_TIMEOUT      = 10
DISCOVER_TIMEOUT   = 10
CLI_DEFAULT_TIMEOUT = 300
DEFAULT_BAUD       = '9600'

BT_MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')

_CONFIG_DIR = os.path.expanduser('~/.config/QuickSync4LinuxGui')
DEFAULT_LOG_FILE = os.path.join(_CONFIG_DIR, 'QuickSync4LinuxGui.log')
DEFAULT_CONFIG_FILE = os.path.join(_CONFIG_DIR, 'settings.json')

# ─── Logger initialisieren ────────────────────────────────────────────────────
import datetime as _dt

def _setup_rotating_logger():
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    #os.makedirs(_LOG_DIR, exist_ok=True)
    handler = RotatingFileHandler(
        DEFAULT_LOG_FILE,
        maxBytes=1 * 1024 * 1024,  # 1 MB
        backupCount=3,
        encoding='utf-8',
    )
    handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s',
                                           datefmt='%d.%m.%Y %H:%M:%S'))
    logger = logging.getLogger('QuickSync4LinuxGui')
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(handler)
    return logger

logger = _setup_rotating_logger()

# Trennlinie + Programmstart — nur einmal, über eine Sentinel-Datei
_sentinel = os.path.join(_CONFIG_DIR, '.started')
if not os.path.exists(_sentinel):
    # Sentinel anlegen damit Subprozesse (CLI-Aufrufe) keinen eigenen Start loggen
    open(_sentinel, 'w').close()
    _start_time = _dt.datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    logger.info('─' * 60)
    logger.info(f'Programmstart: {_start_time}')

    import atexit as _atexit
    def _log_exit():
        _end_time = _dt.datetime.now().strftime('%d.%m.%Y %H:%M:%S')
        logger.info(f'Programmende:  {_end_time}')
        logger.info('─' * 60)
        try: os.unlink(_sentinel)
        except: pass
    _atexit.register(_log_exit)

# ─── JSON-Einstellungen laden/speichern ───────────────────────────────────────
def load_settings(path=DEFAULT_CONFIG_FILE):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        global CHECK_TIMEOUT, DISCOVER_TIMEOUT, CLI_DEFAULT_TIMEOUT, DEFAULT_BAUD
        if 'CHECK_TIMEOUT'       in data: CHECK_TIMEOUT       = int(data['CHECK_TIMEOUT'])
        if 'DISCOVER_TIMEOUT'    in data: DISCOVER_TIMEOUT    = int(data['DISCOVER_TIMEOUT'])
        if 'CLI_DEFAULT_TIMEOUT' in data: CLI_DEFAULT_TIMEOUT = int(data['CLI_DEFAULT_TIMEOUT'])
        if 'DEFAULT_BAUD'        in data: DEFAULT_BAUD        = str(data['DEFAULT_BAUD'])
    except Exception:
        pass

def save_settings(path=DEFAULT_CONFIG_FILE):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            'CHECK_TIMEOUT':      CHECK_TIMEOUT,
            'DISCOVER_TIMEOUT':   DISCOVER_TIMEOUT,
            'CLI_DEFAULT_TIMEOUT': CLI_DEFAULT_TIMEOUT,
            'DEFAULT_BAUD':       DEFAULT_BAUD,
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# Direkt beim Import laden
load_settings()

# ─── Bluetooth-Geräteerkennung ────────────────────────────────────────────────
# Bekannte Gigaset-Gerätekennzeichnungen
GIGASET_KEYWORDS = ['gigaset', 's700', 's650', 's810', 'maxwell', 'sl450', 'cl660']

def discover_devices() -> list[tuple[str, str]]:
    """Gibt nur Gigaset-Geräte als Liste von (mac, label) via bluetoothctl zurück."""
    devices = []
    try:
        out = subprocess.check_output(
            ['bluetoothctl', 'devices'], text=True, timeout=DISCOVER_TIMEOUT
        )
        for line in out.splitlines():
            m = re.match(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)', line)
            if m:
                mac   = m.group(1)
                label = m.group(2).strip()
                if any(kw in label.lower() for kw in GIGASET_KEYWORDS):
                    devices.append((mac, label))
    except Exception:
        pass
    return devices

# ─── Befehle für die CLI-Schnittstelle vorbereiten ────────────────────────────
def build_cmd(action: str, device: str, baud: str = '',
              options: str = '', file: str = '') -> list[str]:
    cmd = ['python3', '-m', 'QuickSync4LinuxGui', action]
    if device:
        cmd += ['-d', device]
    if baud and not BT_MAC_RE.match(device or ''):
        cmd += ['-b', baud]
    if options:
        cmd += [options]
    if file:
        cmd += ['-f', file]
    return cmd

# ─── Verbindungsfehler interpretieren ─────────────────────────────────────────
def interpret_connection_error(text: str) -> str | None:
    t = text.lower()
    if 'host is down' in t or 'errno 112' in t:
        return '✗ Gerät nicht erreichbar — Bitte Telefon entsperren und Bildschirm einschalten.'
    if 'connection refused' in t or 'errno 111' in t:
        return '✗ Verbindung abgelehnt — Bitte Bluetooth am Telefon aktivieren.'
    if 'no route to host' in t or 'errno 113' in t:
        return '✗ Gerät nicht gefunden — Bitte Bluetooth am Telefon aktivieren.'
    if 'timed out' in t or 'timeout' in t:
        return '✗ Zeitüberschreitung — Bitte Telefon entsperren und erneut versuchen.'
    return None

def log(text: str):
    for line in text.splitlines():
        if line.strip():
            logger.info(line)