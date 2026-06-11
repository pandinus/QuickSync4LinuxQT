"""
QuickSync4LinuxGui — Backend
Shared logic and CLI communication.
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
    # Create sentinel so subprocesses do not log their own start
    open(_sentinel, 'w').close()
    _start_time = _dt.datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    logger.info('─' * 60)
    logger.info(f'Program start: {_start_time}')

    import atexit as _atexit
    def _log_exit():
        _end_time = _dt.datetime.now().strftime('%d.%m.%Y %H:%M:%S')
        logger.info(f'Program end:  {_end_time}')
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
def _get_bt_device_class(mac: str) -> str:
    """Returns the device class of a Bluetooth device (e.g. 'phone', 'audio')."""
    try:
        out = subprocess.check_output(
            ['bluetoothctl', 'info', mac], text=True, timeout=5
        )
        for line in out.splitlines():
            if 'Icon:' in line:
                return line.split(':', 1)[1].strip().lower()
    except Exception:
        pass
    return ''

def discover_devices() -> list[tuple[str, str]]:
    """Returns a list of (mac, label) for phone devices via bluetoothctl."""
    devices = []
    try:
        out = subprocess.check_output(
            ['bluetoothctl', 'devices'], text=True, timeout=DISCOVER_TIMEOUT
        )
        for line in out.splitlines():
            m = re.match(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)', line)
            if m:
                mac, label = m.group(1), m.group(2).strip()
                device_class = _get_bt_device_class(mac)
                if 'phone' in device_class:
                    devices.append((mac, label))
    except Exception:
        pass
    return devices

# ─── Bluetooth-Verbindungsstatus prüfen ──────────────────────────────────────
def check_bt_connected(mac: str) -> bool | None:
    """Checks if a device is actively connected via bluetoothctl.
    Returns True if connected, False if not connected, None on error."""
    try:
        out = subprocess.check_output(
            ['bluetoothctl', 'info', mac], text=True, timeout=5
        )
        for line in out.splitlines():
            if 'Connected:' in line:
                return 'yes' in line.lower()
        return False
    except Exception:
        return None

# ─── Verbindungsfehler interpretieren ─────────────────────────────────────────
def interpret_connection_error(text: str, mac: str = '') -> str | None:
    t = text.lower()
    if 'host is down' in t or 'errno 112' in t:
        # Prüfen ob Bluetooth-Verbindung besteht
        if mac:
            bt_connected = check_bt_connected(mac)
            if bt_connected is False:
                return '✗ Device not connected — Please enable Bluetooth on your phone.'
            elif bt_connected is True:
                return '✗ Device not reachable — Please turn on the screen and unlock your phone.'
        return '✗ Device not reachable — Please enable Bluetooth and turn on the screen of your phone.'
    if 'connection refused' in t or 'errno 111' in t:
        return '✗ Connection refused — Please enable Bluetooth on your phone.'
    if 'no route to host' in t or 'errno 113' in t:
        return '✗ Device not found — Please enable Bluetooth on your phone.'
    if 'timed out' in t or 'timeout' in t:
        return '✗ Timeout — Please unlock your phone and try again.'
    return None

def log(text: str):
    for line in text.splitlines():
        if line.strip():
            logger.info(line)