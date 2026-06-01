#!/usr/bin/env python3
"""QuickSync4Linux GUI – PySide6 version (drop-in replacement for the tkinter gui.py)"""

import glob
import os
import re
import subprocess
import tempfile
import threading
import time
import json

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QTextEdit, QFrame,
    QDialog, QDialogButtonBox, QMessageBox, QFileDialog,
    QTabWidget, QFormLayout, QToolBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QSizePolicy, QStatusBar, QAbstractItemView,
)
from PySide6.QtGui import QColor, QFont, QPixmap

from . import vcard
from . import btserial

OBEX_RECOVERY_DELAY = 1.5
DEFAULT_DEVICE = '/dev/ttyACM0'
DEFAULT_BAUD = '9600'
import datetime as _dt
def _make_log_path():
    ts = _dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_dir = os.path.expanduser('~/.config/QuickSync4LinuxGui')
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f'QuickSync4LinuxGui_{ts}.log')

DEFAULT_LOG_FILE = _make_log_path()
BT_MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(@\d+)?$')
CHECK_TIMEOUT = 60
DISCOVER_TIMEOUT = 10
CLI_DEFAULT_TIMEOUT = 300
DEFAULT_CONFIG_FILE = os.path.expanduser('~/.config/QuickSync4LinuxGui/settings.json')


def _load_settings_from_disk(path=DEFAULT_CONFIG_FILE):
    try:
        if not os.path.exists(path):
            return
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if 'CHECK_TIMEOUT' in data:
            globals()['CHECK_TIMEOUT'] = int(data['CHECK_TIMEOUT'])
        if 'DISCOVER_TIMEOUT' in data:
            globals()['DISCOVER_TIMEOUT'] = int(data['DISCOVER_TIMEOUT'])
        if 'CLI_DEFAULT_TIMEOUT' in data:
            globals()['CLI_DEFAULT_TIMEOUT'] = int(data['CLI_DEFAULT_TIMEOUT'])
        if 'DEFAULT_BAUD' in data:
            globals()['DEFAULT_BAUD'] = str(data['DEFAULT_BAUD'])
    except Exception:
        pass


_load_settings_from_disk()


def _save_settings_to_disk(path=DEFAULT_CONFIG_FILE):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            'CHECK_TIMEOUT': CHECK_TIMEOUT,
            'DISCOVER_TIMEOUT': DISCOVER_TIMEOUT,
            'CLI_DEFAULT_TIMEOUT': CLI_DEFAULT_TIMEOUT,
            'DEFAULT_BAUD': DEFAULT_BAUD,
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def discover_devices():
    entries = []
    for path in sorted(glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*') + glob.glob('/dev/rfcomm*')):
        entries.append((f'{path} (Serial)', path))
    try:
        out = subprocess.run(
            ['bluetoothctl', 'devices', 'Paired'],
            capture_output=True, text=True, timeout=DISCOVER_TIMEOUT,
        ).stdout
        for line in out.splitlines():
            parts = line.strip().split(' ', 2)
            if len(parts) >= 3 and parts[0] == 'Device':
                mac, name = parts[1], parts[2]
                entries.append((f'{name} ({mac}) [Bluetooth]', mac))
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return entries


class _WorkerSignals(QObject):
    append_text = Signal(str)
    status_update = Signal(str, str)
    connection_state = Signal(bool)
    action_finished = Signal(str, str)
    show_info = Signal(str, str)   # (title, text)
    clear_output = Signal()


class QuickSyncGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('QuickSync4LinuxGui')
        self.resize(720, 560)

        self._device_map: dict[str, str] = {}
        self._device_connected: bool | None = None
        self._signals = _WorkerSignals()
        self._signals.append_text.connect(self._append_output)
        self._signals.status_update.connect(self._update_status_bar)
        self._signals.connection_state.connect(self._set_connection_state)
        self._signals.action_finished.connect(self._parse_and_fill_ui)
        self._signals.show_info.connect(self._show_info_window)
        self._log_file: str | None = None
        self._log_file_lock = threading.Lock()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        content_row = QHBoxLayout()
        content_row.setSpacing(8)

        # Sidebar
        sidebar = QWidget()
        sidebar.setFixedWidth(155)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(1)

        style = self.style()
        from PySide6.QtWidgets import QStyle

        def sb_group(text):
            lbl = QLabel(text)
            lbl.setStyleSheet('color: palette(window-text); font-size: 13px; font-weight: bold; padding: 12px 4px 2px 4px;')
            sidebar_layout.addWidget(lbl)
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setFrameShadow(QFrame.Plain)
            sidebar_layout.addWidget(sep)

        def sb_btn(label, icon_name, slot, danger=False, success=False):
            b = QPushButton(' ' + label)
            b.setIcon(style.standardIcon(icon_name))
            b.clicked.connect(slot)
            color = '#c9302c' if danger else ('#449d44' if success else 'palette(window-text)')
            b.setStyleSheet(f'text-align: left; padding: 4px 6px; border: none; color: {color};')
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            return b

        sb_group('Gerät')
        sidebar_layout.addWidget(sb_btn('Verbinden',   QStyle.SP_DialogApplyButton,     self.test_connection,   success=True))
        sidebar_layout.addWidget(sb_btn('Trennen',     QStyle.SP_DialogCancelButton,    self.disconnect_device, danger=True))
        sidebar_layout.addWidget(sb_btn('Info',        QStyle.SP_MessageBoxInformation, lambda: self.run_action('info')))
        sidebar_layout.addWidget(sb_btn('Obex Info',   QStyle.SP_MessageBoxInformation, lambda: self.run_action('obexinfo')))
        
        
        sb_group('Kontakte')
        sidebar_layout.addWidget(sb_btn('Verwalten',   QStyle.SP_FileDialogDetailedView, self.open_contacts_manager))
        sidebar_layout.addWidget(sb_btn('Exportieren', QStyle.SP_ArrowDown,              self.get_contacts))
        sidebar_layout.addWidget(sb_btn('Importieren', QStyle.SP_ArrowUp,               lambda: self.choose_file_and_run('createcontacts')))

        sb_group('Dateien')
        sidebar_layout.addWidget(sb_btn('Dateimanager', QStyle.SP_DirOpenIcon, self.open_file_manager))

        sb_group('Einstellungen')
        sidebar_layout.addWidget(sb_btn('Timeouts', QStyle.SP_DialogHelpButton, self.open_settings_timeouts))
        sidebar_layout.addWidget(sb_btn('Baudrate', QStyle.SP_DialogHelpButton, self.open_settings_baudrate))

        sb_group('Log / Konsole')
        sidebar_layout.addWidget(sb_btn('Log öffnen', QStyle.SP_FileIcon, self.open_log))

        sidebar_layout.addStretch()
        content_row.addWidget(sidebar)

        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        content_row.addWidget(line)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        right_col.setContentsMargins(0, 0, 0, 0)

        dev_row_widget = QWidget()
        dev_row_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        dev_row = QHBoxLayout(dev_row_widget)
        dev_row.setContentsMargins(0, 0, 0, 0)
        dev_row.setSpacing(4)
        self.device = QComboBox()
        self.device.setEditable(True)
        self.device.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.device.currentIndexChanged.connect(lambda _: self.check_device_connection())
        dev_row.addWidget(self.device, stretch=1)

        btn_refresh = QPushButton('⟳')
        btn_refresh.setFixedWidth(32)
        btn_refresh.clicked.connect(self.refresh_devices_and_check)
        dev_row.addWidget(btn_refresh)

        # Baudrate wird in den Einstellungen konfiguriert
        self.baud = QLineEdit(DEFAULT_BAUD)
        self.baud.setVisible(False)
        right_col.addWidget(dev_row_widget)

        # Geräteinfo-Formular
        form_layout = QFormLayout()
        form_layout.setSpacing(6)

        self.ui_hersteller = QLineEdit("-"); self.ui_hersteller.setReadOnly(True)
        self.ui_modell = QLineEdit("-"); self.ui_modell.setReadOnly(True)
        self.ui_mac = QLineEdit("-"); self.ui_mac.setReadOnly(True)
        self.ui_firmware = QLineEdit("-"); self.ui_firmware.setReadOnly(True)
        self.ui_seriennummer = QLineEdit("-"); self.ui_seriennummer.setReadOnly(True)
        self.ui_kontakt_anzahl = QLineEdit("-"); self.ui_kontakt_anzahl.setReadOnly(True)

        form_layout.addRow("<b>Geräteinformationen</b>", QLabel(""))
        form_layout.addRow("Hersteller:", self.ui_hersteller)
        form_layout.addRow("Modell / Produkt:", self.ui_modell)
        form_layout.addRow("MAC-Adresse:", self.ui_mac)
        form_layout.addRow("Firmware-Version:", self.ui_firmware)
        form_layout.addRow("Seriennummer (IPUI):", self.ui_seriennummer)
        form_layout.addRow("Anzahl Kontakte:", self.ui_kontakt_anzahl)
        right_col.addLayout(form_layout)

        # Trennlinie
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        right_col.addWidget(line)

        # Konsole / Log unterhalb
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont('Monospace', 9))
        right_col.addWidget(self.output, stretch=1)
        self._signals.clear_output.connect(self.output.clear)

        content_row.addLayout(right_col, stretch=1)
        root.addLayout(content_row, stretch=1)
        self.set_log_file(DEFAULT_LOG_FILE)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_dot = QLabel('●')
        self._status_dot.setStyleSheet('color: gray;')
        self._status_label = QLabel('Gerät-Status wird überprüft…')
        sb.addWidget(self._status_dot)
        sb.addWidget(self._status_label, 1)

        self.refresh_devices(prefer=DEFAULT_DEVICE)
        QTimer.singleShot(100, self.check_device_connection)

    def _parse_and_fill_ui(self, action, text):
        if not text:
            return

        if action == 'contact_count':
            self.ui_kontakt_anzahl.setText(text)
            return

        def find_val(pattern, string):
            match = re.search(pattern, string, re.IGNORECASE)
            return match.group(1).strip() if match else None

        current_dev = self.current_device()
        if BT_MAC_RE.match(current_dev or ''):
            self.ui_mac.setText(current_dev.split('@')[0])

        hersteller = find_val(r'(?:Hersteller|Manufacturer)\s*:\s*(.*)', text)
        modell = find_val(r'(?:Modell|Model|Product)\s*:\s*(.*)', text)
        mac = find_val(r'(?:MAC-Adresse|MAC)\s*:\s*(.*)', text)
        firmware = find_val(r'(?:Firmware-Version|Firmware)\s*:\s*([0-9\.]+)', text)
        seriennummer = find_val(r'(?:Seriennummer|Serial(?:\s*\(IPUI\))?)\s*:\s*(.*)', text)
        
        # Falls das CLI beim "info" oder "getcontacts" Befehl Zahlen ausgibt
        anzahl = find_val(r'(?:Received|Found|Total)\s*([0-9]+)\s*(?:contacts|Kontakte)', text)

        if hersteller: self.ui_hersteller.setText(hersteller)
        if modell: self.ui_modell.setText(modell.split(',')[-1].strip() if ',' in modell else modell)
        if mac: self.ui_mac.setText(mac)
        if firmware: self.ui_firmware.setText(firmware)
        if seriennummer: self.ui_seriennummer.setText(seriennummer)
        if anzahl: self.ui_kontakt_anzahl.setText(anzahl)

    def refresh_devices(self, prefer=None):
        entries = discover_devices()
        self._device_map = dict(entries)
        self.device.blockSignals(True)
        self.device.clear()
        for label, _ in entries:
            self.device.addItem(label)
        self.device.blockSignals(False)

        if prefer:
            for label, value in entries:
                if value == prefer:
                    self.device.setCurrentText(label)
                    return
        if entries:
            self.device.setCurrentIndex(0)
        elif prefer:
            self.device.setCurrentText(prefer)

    def refresh_devices_and_check(self):
        self.refresh_devices()
        self.check_device_connection()

    def current_device(self):
        text = self.device.currentText().strip()
        return self._device_map.get(text, text)

    def current_device_label(self):
        text = self.device.currentText().strip()
        for suffix in (' [Bluetooth]', ' (Serial)'):
            if text.endswith(suffix):
                return text[: -len(suffix)]
        return text or self.current_device()

    def build_cmd(self, action, options=None, file=None):
        cmd = ['python3', '-m', 'QuickSync4LinuxGui', action]
        if options:
            cmd.append(options)
        dev = self.current_device()
        if dev:
            cmd += ['-d', dev]
        if self.baud.text() and not BT_MAC_RE.match(dev or ''):
            cmd += ['-b', self.baud.text()]
        if file:
            cmd += ['-f', file]
        return cmd

    def _set_connection_state(self, connected: bool):
        self._device_connected = connected

    def is_device_connected(self):
        return bool(self.current_device()) and self._device_connected is not False

    def _append_output(self, text: str):
        if text == 'clear':
            self.output.clear()
            return
        self.output.append(text)
        self._log_raw_output(text)

    def _log_raw_output(self, text: str):
        if not self._log_file:
            return
        try:
            with self._log_file_lock:
                with open(self._log_file, 'a', encoding='utf-8') as f:
                    f.write(text if text.endswith('\n') else text + '\n')
        except OSError:
            pass

    def set_log_file(self, path: str | None):
        self._log_file = path
        if not path:
            self._append_output('Log-Datei deaktiviert')
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8'):
                pass
            self._append_output(f'Log-Datei gesetzt: {path}')
        except OSError as e:
            self._append_output(f'✗ Log-Datei konnte nicht geöffnet werden: {e}')
            self._log_file = None

    def _update_status_bar(self, text: str, colour: str):
        self._status_dot.setStyleSheet(f'color: {colour};')
        self._status_label.setText(text)

    def _show_info_window(self, title, text):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(520, 400)
        layout = QVBoxLayout(dlg)
        out = QTextEdit()
        out.setReadOnly(True)
        out.setFont(QFont('Monospace', 9))
        out.setPlainText(text)
        layout.addWidget(out)
        btn = QDialogButtonBox(QDialogButtonBox.Close)
        btn.rejected.connect(dlg.reject)
        layout.addWidget(btn)
        dlg.exec()

    def run_action(self, action, options=None, file=None):
        dev = self.current_device()
        if not dev:
            self.output.clear()
            self._append_output('✗ Kein Gerät ausgewählt oder Verbindung getrennt')
            return

        if self._device_connected is False and action not in ('info', 'obexinfo'):
            QMessageBox.warning(self, 'Verbindung getrennt',
                'Das Gerät ist derzeit getrennt. Bitte stellen Sie die Verbindung erneut her.')
            return

        cmd = self.build_cmd(action, options, file)
        self.output.clear()

        if action == 'info':
            msg = 'Info wird abgerufen...'
        elif action == 'obexinfo':
            msg = 'OBEX Info wird abgerufen...'
        elif action == 'getcontacts':
            msg = 'Kontakteverwaltung wird aufgerufen...'
        else:
            msg = f"Running: {' '.join(cmd)}"
        self._append_output(msg)
        sig = self._signals

        def worker():
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_DEFAULT_TIMEOUT)
                if proc.returncode == 0:
                    if proc.stdout:
                        if action in ('info', 'obexinfo'):
                            title = 'Geräte-Info' if action == 'info' else 'Obex Info'
                            sig.show_info.emit(title, proc.stdout)
                            sig.clear_output.emit()
                            sig.append_text.emit('clear')
                        else:
                            sig.append_text.emit(proc.stdout)
                        sig.action_finished.emit(action, proc.stdout)
                else:
                    if proc.stdout:
                        sig.append_text.emit(proc.stdout)
                    if proc.stderr:
                        sig.append_text.emit('ERROR:\n' + proc.stderr)
            except Exception as e:
                sig.append_text.emit(f'Exception: {e}')

        threading.Thread(target=worker, daemon=True).start()

    def choose_file_and_run(self, action):
        path, _ = QFileDialog.getOpenFileName(self, 'VCF-Datei wählen', '', 'VCF-Dateien (*.vcf);;Alle Dateien (*)')
        if path:
            self.run_action(action, None, path)

    def get_contacts(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Kontakte speichern als', '', 'VCF-Dateien (*.vcf);;Alle Dateien (*)')
        if path:
            self.run_action('getcontacts', None, path)

    def open_log(self):
        initial = self._log_file if self._log_file else ''
        initial_dir = os.path.dirname(os.path.abspath(initial)) if initial else os.getcwd()
        path, _ = QFileDialog.getOpenFileName(self, 'Log-Datei wählen', initial_dir, 'Log-Dateien (*.log);;Alle Dateien (*)')
        if not path:
            return
        try:
            subprocess.Popen(['xdg-open', path])
        except Exception as e:
            self._append_output(f'✗ Log-Datei konnte nicht geöffnet werden: {e}')

    def open_settings_timeouts(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('Einstellungen')
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        from PySide6.QtWidgets import QSpinBox, QComboBox as _CB
        chk = QSpinBox(); chk.setRange(1, 3600); chk.setValue(CHECK_TIMEOUT); form.addRow('Verbindungs-Timeout (s):', chk)
        dsk = QSpinBox(); dsk.setRange(1, 3600); dsk.setValue(DISCOVER_TIMEOUT); form.addRow('Bluetooth Timeout (s):', dsk)
        cli = QSpinBox(); cli.setRange(1, 36000); cli.setValue(CLI_DEFAULT_TIMEOUT); form.addRow('CLI Timeout (s):', cli)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject); layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            globals()['CHECK_TIMEOUT'] = chk.value()
            globals()['DISCOVER_TIMEOUT'] = dsk.value()
            globals()['CLI_DEFAULT_TIMEOUT'] = cli.value()
            _save_settings_to_disk()

    def open_settings_baudrate(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('Einstellungen')
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        from PySide6.QtWidgets import QSpinBox, QComboBox as _CB
        baud_combo = _CB()
        baud_combo.setEditable(True)
        for b in ['1200', '2400', '4800', '9600', '19200', '38400', '57600', '115200']:
            baud_combo.addItem(b)
        baud_combo.setCurrentText(self.baud.text() or DEFAULT_BAUD)
        form.addRow('Baudrate (Seriell):', baud_combo)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject); layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            globals()['DEFAULT_BAUD'] = baud_combo.currentText()
            self.baud.setText(baud_combo.currentText())
            _save_settings_to_disk()
    

    def open_file_manager(self):
        if not self.current_device():
            QMessageBox.warning(self, 'Kein Gerät verbunden', 'Bitte wählen Sie zuerst ein Gerät aus.')
            return
        if hasattr(self, '_file_manager_win') and self._file_manager_win is not None:
            self._file_manager_win.raise_()
            self._file_manager_win.activateWindow()
            return
        self._file_manager_win = FileManagerWindow(self)
        self._file_manager_win.finished.connect(lambda: setattr(self, '_file_manager_win', None))

    def open_contacts_manager(self):
        if not self.current_device():
            QMessageBox.warning(self, 'Kein Gerät verbunden', 'Bitte wählen Sie zuerst ein Gerät aus.')
            return
        if self._device_connected is not True:
            QMessageBox.warning(self, 'Gerät nicht verbunden', 'Das Gerät ist nicht verbunden. Bitte klicken Sie auf "Verbindung herstellen".')
            return
        if hasattr(self, '_contacts_win') and self._contacts_win is not None:
            self._contacts_win.raise_()
            self._contacts_win.activateWindow()
            return
        self._contacts_win = ContactsWindow(self)
        self._contacts_win.finished.connect(lambda: setattr(self, '_contacts_win', None))

    def _interpret_connection_error(self, text: str) -> str:
        """Gibt eine benutzerfreundliche Fehlermeldung für bekannte Verbindungsfehler zurück."""
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

    def run_cli_sync(self, action, options=None, file=None, timeout=CLI_DEFAULT_TIMEOUT):
        if not self.current_device():
            raise RuntimeError('Kein Gerät ausgewählt.')
        cmd = self.build_cmd(action, options, file)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.stdout: self._log_raw_output(proc.stdout)
        if proc.stderr: self._log_raw_output('ERROR:\n' + proc.stderr)
        if proc.returncode != 0:
            raise RuntimeError(f'{action} fehlgeschlagen (exit {proc.returncode})')
        return proc

    def download_file(self):
        remote = simple_input(self, 'Remote file', 'Remote-Dateiname eingeben:')
        if not remote: return
        out, _ = QFileDialog.getSaveFileName(self, 'Speichern als')
        if out: self.run_action('download', remote, out)

    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Datei zum Hochladen wählen')
        if not path: return
        remote = simple_input(self, 'Remote-Name', 'Remote-Dateiname auf dem Gerät:')
        if not remote: return
        self.run_action('upload', remote, path)

    def delete_file(self):
        remote = simple_input(self, 'Remote file', 'Remote-Dateiname zum Löschen:')
        if remote: self.run_action('delete', remote)

    def check_device_connection(self):
        label = self.current_device_label()
        self._update_status_bar(f'{label}: Status prüfen …' if label else 'Verbindung wird geprüft …', '#888888')
        sig = self._signals

        def worker():
            connected = False
            status_text = 'Kein Gerät verbunden'
            status_color = '#d9534f'
            try:
                dev = self.current_device()
                if dev:
                    cmd_info = self.build_cmd('info')
                    proc_info = subprocess.run(cmd_info, capture_output=True, text=True, timeout=CHECK_TIMEOUT)
                    if proc_info.returncode == 0:
                        status_text = f'{label} verbunden' if label else 'Gerät verbunden'
                        status_color = '#5cb85c'
                        connected = True
                        sig.append_text.emit(f'✓ {status_text}')
                        self._log_raw_output(proc_info.stdout)
                        sig.action_finished.emit('info', proc_info.stdout)
                        # Kontaktanzahl im Hintergrund abrufen
                        try:
                            import tempfile, os as _os
                            fd, vcf_path = tempfile.mkstemp(suffix='.vcf', prefix='quicksync_count_')
                            _os.close(fd)
                            cmd_contacts = self.build_cmd('getcontacts', file=vcf_path)
                            proc_contacts = subprocess.run(cmd_contacts, capture_output=True, text=True, timeout=CHECK_TIMEOUT)
                            if proc_contacts.returncode == 0:
                                with open(vcf_path, 'r', encoding='utf-8') as f_vcf:
                                    vcf_data = f_vcf.read()
                                count = len(vcard.parseCards(vcf_data))
                                sig.action_finished.emit('contact_count', str(count))
                            try:
                                _os.unlink(vcf_path)
                            except OSError:
                                pass
                        except Exception:
                            pass
            except Exception as e:
                friendly = self._interpret_connection_error(str(e))
                if friendly:
                    sig.append_text.emit(friendly)
            sig.connection_state.emit(connected)
            sig.status_update.emit(status_text, status_color)

        threading.Thread(target=worker, daemon=True).start()

    def test_connection(self):
        self.output.clear()
        self._append_output('--- Verbindung wird hergestellt ---')
        label = self.current_device_label()
        self._update_status_bar(f'{label}: Verbinde …', '#888888')
        sig = self._signals

        def worker():
            connected = False
            try:
                dev = self.current_device()
                if not dev:
                    result = 'Kein Gerät ausgewählt.'
                else:
                    cmd_info = self.build_cmd('info')
                    proc_info = subprocess.run(cmd_info, capture_output=True, text=True, timeout=CHECK_TIMEOUT)
                    if proc_info.returncode == 0:
                        result = f'✓ Gerät verbunden: {label or dev}'
                        connected = True
                        self._log_raw_output(proc_info.stdout)
                        sig.action_finished.emit('info', proc_info.stdout)
                    else:
                        friendly = self._interpret_connection_error(proc_info.stderr)
                        result = friendly or ('✗ Gerät nicht erreichbar\n' + proc_info.stderr)
            except Exception as e:
                friendly = self._interpret_connection_error(str(e))
                result = friendly or f'✗ Fehler: {e}'

            status = ((f'{label} verbunden' if label else 'Gerät verbunden', '#5cb85c') if connected else ('Kein Gerät verbunden', '#d9534f'))
            sig.connection_state.emit(connected)
            sig.append_text.emit(result)
            sig.status_update.emit(*status)

        threading.Thread(target=worker, daemon=True).start()

    def disconnect_device(self):
        dev = self.current_device()
        label = self.current_device_label() or dev
        if dev and btserial.isBluetoothAddress(dev):
            try: subprocess.run(['bluetoothctl', 'disconnect', dev.split('@')[0]], capture_output=True, timeout=5)
            except Exception: pass
        self.output.clear()
        self._append_output(f'✓ Verbindung zu {label} getrennt')
        self._set_connection_state(False)
        self._update_status_bar('Kein Gerät verbunden', '#d9534f')




# ─── File Manager Window (Robust & Fixed Parser) ──────────────────────────────

class _FileManagerSignals(QObject):
    setup_ui     = Signal(list, str)
    set_status   = Signal(str)
    do_reload    = Signal()
    show_preview = Signal(str)   # tmp_path
    show_preview_err = Signal(str)  # error text


class FileManagerWindow(QDialog):
    """Dateimanager im KDE/Dolphin-Look mit fixiertem Gigaset-Parser."""

    IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}

    def __init__(self, parent):
        super().__init__(parent)
        self.parent_win = parent
        self.setWindowTitle('Dateimanager — Dolphin Style')
        self.resize(950, 550)
        self._files: list[dict] = []
        self._all_files: list[dict] = []
        self._fm_signals = _FileManagerSignals()

        # Hauptlayout (Vertikal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 1. Toolbar (Dolphin-like)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)
        from PySide6.QtWidgets import QStyle
        
        def tbtn(label, icon_name, slot, is_danger=False):
            b = QPushButton(' ' + label)
            b.setIcon(self.style().standardIcon(icon_name))
            b.clicked.connect(slot)
            if is_danger:
                b.setStyleSheet("QPushButton { color: #cc241d; }")
            else:
                b.setStyleSheet("QPushButton { text-align: left; }")
            return b

        btn_reload = tbtn('Aktualisieren', QStyle.SP_BrowserReload, self.reload)
        btn_download = tbtn('Herunterladen', QStyle.SP_ArrowDown, self.download_selected)
        btn_upload = tbtn('Hochladen', QStyle.SP_ArrowUp, self.upload_file)
        btn_delete = tbtn('Löschen', QStyle.SP_TrashIcon, self.delete_selected, is_danger=True)
        
        toolbar.addWidget(btn_reload)
        toolbar.addWidget(btn_download)
        toolbar.addWidget(btn_upload)
        toolbar.addWidget(btn_delete)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Trennlinie unter Toolbar
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Plain)
        layout.addWidget(sep)

        # 2. Haupt-Inhaltsbereich mit Dreifach-Splitting (Sidebar | Tabelle | Info-Panel)
        main_hbox = QHBoxLayout()
        main_hbox.setSpacing(6)

        # Links: Orte/Ordner-Sidebar (KDE-Style)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        self.folder_sidebar = QListWidget()
        self.folder_sidebar.setFixedWidth(180)
        self.folder_sidebar.setStyleSheet("QListWidget { background: palette(window); border: none; font-weight: bold; }")
        self.folder_sidebar.itemClicked.connect(self._on_sidebar_folder_changed)
        main_hbox.addWidget(self.folder_sidebar)

        # Vertikale Trennlinie
        v_sep1 = QFrame()
        v_sep1.setFrameShape(QFrame.VLine)
        v_sep1.setFrameShadow(QFrame.Plain)
        main_hbox.addWidget(v_sep1)

        # Mitte: Die Dateitabelle
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Name', 'Datum', 'Größe'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection)
        main_hbox.addWidget(self.table, stretch=2)

        # Vertikale Trennlinie
        v_sep2 = QFrame()
        v_sep2.setFrameShape(QFrame.VLine)
        v_sep2.setFrameShadow(QFrame.Plain)
        main_hbox.addWidget(v_sep2)

        # Rechts: Dolphins Informations-Panel (Vorschau)
        preview_panel = QWidget()
        preview_panel.setFixedWidth(220)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(4, 0, 4, 0)

        self.preview_label = QLabel('Keine Auswahl')
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet('font-weight: bold; color: palette(window-text);')
        
        self.preview_image = QLabel()
        self.preview_image.setAlignment(Qt.AlignCenter)
        self.preview_image.setMinimumHeight(180)
        self.preview_image.setStyleSheet('background: palette(base); border: 1px solid palette(mid); border-radius: 4px;')

        self.preview_info = QTextEdit()
        self.preview_info.setReadOnly(True)
        self.preview_info.setFont(QFont('Monospace', 8))
        self.preview_info.setStyleSheet("QTextEdit { border: none; background: transparent; }")
        self.preview_info.setMaximumHeight(120)

        preview_layout.addWidget(self.preview_label)
        preview_layout.addWidget(self.preview_image)
        preview_layout.addWidget(self.preview_info)
        preview_layout.addStretch()
        main_hbox.addWidget(preview_panel)

        layout.addLayout(main_hbox, stretch=1)

        # Trennlinie über Statusbar
        sep_bottom = QFrame()
        sep_bottom.setFrameShape(QFrame.HLine)
        sep_bottom.setFrameShadow(QFrame.Plain)
        layout.addWidget(sep_bottom)

        # 3. Statuszeile
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(6, 2, 6, 2)
        self.status_label = QLabel('')
        self.space_label = QLabel('')
        self.space_label.setStyleSheet('color: palette(mid); font-size: 11px;')
        bottom_row.addWidget(self.status_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self.space_label)
        layout.addLayout(bottom_row)

        self.setModal(False)
        # Signals jetzt verbinden, da status_label und space_label bereits existieren
        self._fm_signals.setup_ui.connect(self._setup_ui_data)
        self._fm_signals.set_status.connect(self.status_label.setText)
        self._fm_signals.do_reload.connect(self.reload)
        self._fm_signals.show_preview.connect(self._show_preview)
        self._fm_signals.show_preview_err.connect(self.preview_image.setText)
        self.show()
        QTimer.singleShot(50, self.reload)

    def reload(self):
        self.status_label.setText('Lade Dateiliste …')
        self.space_label.setText('')
        self.table.setRowCount(0)
        self.folder_sidebar.clear()
        self._all_files = []
        self._files = []
        
        fm_sig = self._fm_signals

        def worker():
            try:
                cmd = self.parent_win.build_cmd('listfiles')
                log = self.parent_win._signals
                log.append_text.emit('Lade Dateiliste …')
                fm_sig.set_status.emit('Lade Dateiliste … (CLI läuft)')

                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_DEFAULT_TIMEOUT)
                except subprocess.TimeoutExpired:
                    log.append_text.emit(f"[FileManager] ✗ Timeout nach {CLI_DEFAULT_TIMEOUT}s")
                    fm_sig.set_status.emit(f'✗ Timeout ({CLI_DEFAULT_TIMEOUT}s überschritten)')
                    return

                output = proc.stdout or ''
                stderr = proc.stderr or ''

                if stderr:
                    log.append_text.emit(f"[FileManager] stderr: {stderr[:300]}")

                if not output and proc.returncode != 0:
                    error_msg = f"✗ CLI Fehler (Code {proc.returncode}): {stderr[:200] or 'kein Fehlertext'}"
                    log.append_text.emit(f"[FileManager] {error_msg}")
                    fm_sig.set_status.emit(error_msg)
                    return

                try:
                    files = self._parse_listfiles(output)
                except Exception as parse_exc:
                    import traceback
                    log.append_text.emit(f"[FileManager] Parse-Fehler:\n{traceback.format_exc()}")
                    fm_sig.set_status.emit(f'✗ Parse-Fehler: {parse_exc}')
                    return

                import re as _re
                total = _re.search(r'Total Space:\s*([\d\.]+\s*\w+)', output, _re.IGNORECASE)
                free  = _re.search(r'Free Space:\s*([\d\.]+\s*\w+)', output, _re.IGNORECASE)
                space_text = ''
                if total and free:
                    space_text = f'Frei: {free.group(1)}  |  Gesamt: {total.group(1)}'

                if not files:
                    msg = "✗ Keine Dateien verarbeitet. Rohdaten im Konsole-Tab prüfen."
                    log.append_text.emit(f"[FileManager] {msg}")
                    fm_sig.set_status.emit(msg)
                    return

                log.append_text.emit(f'✓ Dateiliste geladen: {len(files)} Dateien in {len(set(f["folder"] for f in files))} Ordner')
                fm_sig.setup_ui.emit(files, space_text)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                try:
                    self.parent_win._signals.append_text.emit(f"[FileManager] Exception:\n{tb}")
                except Exception:
                    pass
                fm_sig.set_status.emit(f'✗ Fehler: {e}')
        threading.Thread(target=worker, daemon=True).start()

    def _parse_listfiles(self, text):
        import re as _re
        files = []
        current_folder = '/'
        
        # Ersetze hartnäckige geschützte Leerzeichen (NBSP) durch reguläre Spaces
        clean_text = text.replace('\xa0', ' ')
        
        # Super-robuste Regex, die auf IDs, Datums-Formate und Dateigrößen-Endungen matcht
        line_re = _re.compile(r'^(\d+):\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+[A-Z]\s+([\d\.]+\s+\w+)')

        for line in clean_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            
            # Ordner-Wechsel erkennen (z.B. === /Pictures)
            if stripped.startswith('==='):
                current_folder = stripped.replace('===', '').strip()
                continue
                
            m = line_re.match(stripped)
            if m:
                file_id, name, date_str, time_str, size_str = m.groups()
                files.append({
                    'id':     file_id,
                    'folder': current_folder,
                    'name':   name.strip(),
                    'date':   f"{date_str} {time_str}",
                    'size':   size_str,
                })
        return files

    def _setup_ui_data(self, files, space_text):
        self._all_files = files
        self.space_label.setText(space_text)
        folders = sorted(list(set(f['folder'] for f in files)))
        if not folders:
            folders = ['/']
        from PySide6.QtWidgets import QStyle, QListWidgetItem as _LWI
        self.folder_sidebar.clear()
        for folder in folders:
            item = _LWI(self.style().standardIcon(QStyle.SP_DirIcon), folder)
            self.folder_sidebar.addItem(item)
        if self.folder_sidebar.count() > 0:
            self.folder_sidebar.setCurrentRow(0)
            self._filter_by_folder(self.folder_sidebar.item(0).text())
        else:
            self.status_label.setText('0 Dateien gefunden.')

    def _on_sidebar_folder_changed(self, item):
        self._filter_by_folder(item.text())

    def _filter_by_folder(self, folder_name):
        self._files = [f for f in self._all_files if f['folder'] == folder_name]
        self._populate(self._files)

    def _populate(self, files):
        self.table.setRowCount(0)
        from PySide6.QtWidgets import QStyle as _QStyle
        img_exts = self.IMAGE_EXTS
        for f in files:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            ext = os.path.splitext(f['name'])[1].lower()
            icon_type = _QStyle.SP_FileDialogDetailedView if ext in img_exts else _QStyle.SP_FileIcon
            icon = self.style().standardIcon(icon_type)
            
            name_item = QTableWidgetItem(f['name'])
            name_item.setIcon(icon)
            
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(f.get('date', '-')))
            self.table.setItem(row, 2, QTableWidgetItem(f['size']))
            
        self.table.resizeRowsToContents()
        current_folder = self.folder_sidebar.currentItem().text() if self.folder_sidebar.currentItem() else ""
        self.status_label.setText(f'{len(files)} Datei(en) in "{current_folder}"')

    def _selected_file(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._files):
            return None
        return self._files[row]

    def _on_selection(self):
        f = self._selected_file()
        if not f:
            self.preview_image.clear()
            self.preview_label.setText('Keine Auswahl')
            self.preview_info.clear()
            return
        ext = os.path.splitext(f['name'])[1].lower()
        self.preview_label.setText(f['name'])
        self.preview_info.setText(f"<b>ID:</b> {f['id']}<br><b>Pfad:</b> {f['folder']}/{f['name']}<br><b>Größe:</b> {f['size']}<br><b>Datum:</b> {f['date']}")
        if ext in self.IMAGE_EXTS:
            self.preview_image.setText('⏳ Lade Bild …')
            self._load_preview(f)
        else:
            self.preview_image.setText('Keine Bildvorschau')

    def _load_preview(self, f):
        fm_sig = self._fm_signals
        def worker():
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(f['name'])[1])
                os.close(fd)
                remote_path = f"{f['folder']}/{f['name']}"
                cmd = self.parent_win.build_cmd('download', options=remote_path, file=tmp_path)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if proc.returncode == 0 and os.path.exists(tmp_path):
                    fm_sig.show_preview.emit(tmp_path)
                else:
                    fm_sig.show_preview_err.emit('✗ Vorschau nicht verfügbar')
            except Exception as e:
                fm_sig.show_preview_err.emit(f'✗ {e}')
        threading.Thread(target=worker, daemon=True).start()

    def _show_preview(self, path):
        pixmap = QPixmap(path)
        try:
            os.unlink(path)
        except OSError:
            pass
        if pixmap.isNull():
            self.preview_image.setText('✗ Bildfehler')
            return
        scaled = pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_image.setPixmap(scaled)

    def download_selected(self):
        f = self._selected_file()
        if not f:
            QMessageBox.information(self, 'Hinweis', 'Bitte eine Datei auswählen.')
            return
        save_path, _ = QFileDialog.getSaveFileName(self, 'Speichern als', f['name'])
        if not save_path:
            return
        self.status_label.setText(f'⏳ Download: {f["name"]} …')
        fm_sig = self._fm_signals
        def worker():
            try:
                remote_path = f"{f['folder']}/{f['name']}"
                cmd = self.parent_win.build_cmd('download', options=remote_path, file=save_path)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_DEFAULT_TIMEOUT)
                msg = f'✓ Gespeichert: {save_path}' if proc.returncode == 0 else '✗ Fehler beim Download'
                fm_sig.set_status.emit(msg)
            except Exception as e:
                fm_sig.set_status.emit(f'✗ {e}')
        threading.Thread(target=worker, daemon=True).start()

    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Datei zum Hochladen wählen')
        if not path:
            return
        self.status_label.setText(f'⏳ Upload: {os.path.basename(path)} …')
        fm_sig = self._fm_signals
        def worker():
            try:
                cmd = self.parent_win.build_cmd('upload', options=os.path.basename(path), file=path)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_DEFAULT_TIMEOUT)
                if proc.returncode == 0:
                    fm_sig.set_status.emit('✓ Upload abgeschlossen')
                    fm_sig.do_reload.emit()
                else:
                    fm_sig.set_status.emit('✗ Fehler beim Upload')
            except Exception as e:
                fm_sig.set_status.emit(f'✗ {e}')
        threading.Thread(target=worker, daemon=True).start()

    def delete_selected(self):
        f = self._selected_file()
        if not f:
            QMessageBox.information(self, 'Hinweis', 'Bitte eine Datei auswählen.')
            return
        r = QMessageBox.question(self, 'Löschen', f'Datei "{f["name"]}" wirklich löschen?')
        if r != QMessageBox.Yes:
            return
        self.status_label.setText(f'⏳ Lösche: {f["name"]} …')
        fm_sig = self._fm_signals
        def worker():
            try:
                remote_path = f"{f['folder']}/{f['name']}"
                cmd = self.parent_win.build_cmd('delete', options=remote_path)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_DEFAULT_TIMEOUT)
                if proc.returncode == 0:
                    fm_sig.set_status.emit(f'✓ Gelöscht: {f["name"]}')
                    fm_sig.do_reload.emit()
                else:
                    fm_sig.set_status.emit('✗ Fehler beim Löschen')
            except Exception as e:
                fm_sig.set_status.emit(f'✗ {e}')
        threading.Thread(target=worker, daemon=True).start()

class ContactsWindow(QDialog):
    COLUMNS = [('name', 'Name', 200), ('cell', 'Mobil', 130), ('home', 'Privat', 130), ('work', 'Geschäftl.', 130), ('email', 'E-Mail', 200)]

    def __init__(self, parent: QuickSyncGUI):
        super().__init__(parent)
        self.parent_win = parent
        self.setWindowTitle('Kontakte verwalten')
        self.resize(900, 500)
        self.cards: list[dict] = []
        self._row_to_index: dict[int, int] = {}
        self.modified_luids: set[str] = set()
        self.deleted_luids: set[str] = set()
        self.temp_vcf_path: str | None = None
        self._reload_thread: threading.Thread | None = None

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        def tbtn(label, slot):
            b = QPushButton(label); b.clicked.connect(slot); toolbar.addWidget(b); return b
        tbtn('Neu',        self.new_contact)
        tbtn('Bearbeiten', self.edit_selected)
        tbtn('Löschen',    self.delete_selected)
        toolbar.addSpacing(12)
        tbtn('Neu laden',  self.reload_with_confirm)
        tbtn('Speichern',  self.save)
        tbtn('Übertragen', self.transmit)
        tbtn('Schließen',  self._on_close_request)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in self.COLUMNS])
        for i, (_, _, w) in enumerate(self.COLUMNS): self.table.setColumnWidth(i, w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _: self.edit_selected())
        layout.addWidget(self.table, stretch=1)

        self.status_label = QLabel('')
        layout.addWidget(self.status_label)
        self.setModal(False)
        self.show()
        
        # Sofortiger visueller Indikator im Hauptfenster
        self.parent_win.ui_kontakt_anzahl.setText("wird geladen...")
        QTimer.singleShot(50, self.reload)

    def _refresh_status(self):
        pending = sum(1 for c in self.cards if not c.get('luid')) + len(self.modified_luids) + len(self.deleted_luids)
        suffix = f' — {pending} ungespeicherte Änderung(en)' if pending else ''
        self.status_label.setText(f'{len(self.cards)} Kontakt(e){suffix}')
        
        # Absolut direkte und sichere Aktualisierung des Hauptfenster-Labels aus dem UI-Thread
        self.parent_win.ui_kontakt_anzahl.setText(str(len(self.cards)))

    def reload(self):
        if self._reload_thread and self._reload_thread.is_alive():
            self.parent_win._signals.append_text.emit('⚠ Reload läuft bereits — bitte warten.')
            return
        self.parent_win.output.clear()
        self.status_label.setText('Lade Kontakte …')
        sig = self.parent_win._signals

        def worker():
            if not self.parent_win.current_device():
                QTimer.singleShot(0, self, lambda: self._on_error('Laden fehlgeschlagen', RuntimeError('Kein Gerät verbunden.')))
                return
            fd, path = tempfile.mkstemp(suffix='.vcf', prefix='quicksync_')
            os.close(fd)
            try:
                self.parent_win.run_cli_sync('getcontacts', file=path)
                size = os.path.getsize(path)
                sig.append_text.emit(f'VCF geladen: {size} Bytes')
                with open(path, 'r', encoding='utf-8') as f:
                    vcf = f.read()
                if not vcf.strip():
                    raise RuntimeError('Telefon hat eine leere Antwort geliefert.')
                cards = vcard.parseCards(vcf)
                sig.append_text.emit(f'{len(cards)} Kontakt(e) verarbeitet')
                QTimer.singleShot(0, self.parent_win, lambda: self.parent_win.ui_kontakt_anzahl.setText(str(len(cards))))
                QTimer.singleShot(0, self, lambda: self._populate(cards, path))
            except Exception as e:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                QTimer.singleShot(0, self, lambda: self._on_error('Laden fehlgeschlagen', e))

        self._reload_thread = threading.Thread(target=worker, daemon=True)
        self._reload_thread.start()

    def reload_with_confirm(self):
        pending = sum(1 for c in self.cards if not c.get('luid')) + len(self.modified_luids) + len(self.deleted_luids)
        if pending > 0:
            r = QMessageBox.question(self, 'Neu laden', 'Es gibt ungespeicherte Änderungen. Trotzdem neu laden?')
            if r != QMessageBox.Yes:
                return
        self.modified_luids.clear()
        self.deleted_luids.clear()
        self.reload()

    def _on_error(self, title, exc):
        self.status_label.setText('')
        QMessageBox.critical(self, title, str(exc))

    def _populate(self, cards, temp_path=None):
        self.temp_vcf_path = temp_path
        self.cards = cards
        self._rebuild_table()

    def _rebuild_table(self):
        self.table.setRowCount(0)
        self._row_to_index = {}
        for i, c in enumerate(self.cards):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._row_to_index[row] = i
            values = [
                vcard.displayName(c),
                c.get('tels', {}).get('CELL', ''),
                c.get('tels', {}).get('HOME', ''),
                c.get('tels', {}).get('WORK', ''),
                (c.get('emails', {}).get('HOME', '') or c.get('emails', {}).get('WORK', '') or c.get('emails', {}).get('OTHER', '')),
            ]
            for col, val in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(val))
        self._refresh_status()

    def new_contact(self):
        blank = {'luid': None, 'last_name': '', 'first_name': '', 'middle_name': '', 'prefix': '', 'suffix': '', 'nickname': '', 'org': '', 'title': '', 'bday': '', 'note': '', 'url': '', 'tels': {'HOME': '', 'CELL': '', 'WORK': '', 'FAX': '', 'OTHER': ''}, 'emails': {'HOME': '', 'WORK': '', 'OTHER': ''}, 'addresses': {'HOME': {'pobox': '', 'ext': '', 'street': '', 'city': '', 'region': '', 'zip': '', 'country': ''}, 'WORK': {'pobox': '', 'ext': '', 'street': '', 'city': '', 'region': '', 'zip': '', 'country': ''}}, 'extras': []}
        ContactEditor(self, blank, on_save=lambda c: (self.cards.append(c), self._rebuild_table()))

    def edit_selected(self):
        rows = self.table.selectedItems()
        if not rows: return
        idx = self._row_to_index.get(self.table.currentRow())
        if idx is not None:
            ContactEditor(self, self.cards[idx], on_save=lambda c: (self.modified_luids.add(c['luid']) if c.get('luid') else None, self._rebuild_table()))

    def delete_selected(self):
        rows = self.table.selectedItems()
        if not rows: return
        idx = self._row_to_index.get(self.table.currentRow())
        if idx is not None:
            luid = self.cards[idx].get('luid')
            if luid: self.deleted_luids.add(luid)
            del self.cards[idx]
            self._rebuild_table()

    def save(self):
        if not self.temp_vcf_path: return
        with open(self.temp_vcf_path, 'w', encoding='utf-8') as f:
            for c in self.cards: f.write(vcard.formatCard(c))

    def transmit(self):
        deletes = list(self.deleted_luids)
        creates = [c for c in self.cards if not c.get('luid')]
        
        def worker():
            try:
                for luid in deletes: self.parent_win.run_cli_sync('deletecontact', options=luid)
                if creates:
                    fd, path = tempfile.mkstemp(suffix='.vcf')
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        for c in creates: f.write(vcard.formatCard(c))
                    self.parent_win.run_cli_sync('createcontacts', file=path)
                    try: os.unlink(path)
                    except OSError: pass
                QTimer.singleShot(0, self.reload)
            except Exception as e:
                QTimer.singleShot(0, lambda: QMessageBox.critical(self, 'Fehler', str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _on_close_request(self): self.close()


class ContactEditor(QDialog):
    def __init__(self, parent, card, on_save):
        super().__init__(parent)
        self.setWindowTitle('Kontakt bearbeiten' if card.get('luid') else 'Neuer Kontakt')
        self.resize(400, 450)
        self.card = card
        self.on_save = on_save
        self.entries = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.entries['first_name'] = QLineEdit(card.get('first_name', ''))
        self.entries['last_name'] = QLineEdit(card.get('last_name', ''))
        self.entries['cell'] = QLineEdit(card.get('tels', {}).get('CELL', ''))
        self.entries['home'] = QLineEdit(card.get('tels', {}).get('HOME', ''))
        self.entries['email'] = QLineEdit(card.get('emails', {}).get('HOME', ''))
        
        form.addRow('Vorname:', self.entries['first_name'])
        form.addRow('Nachname:', self.entries['last_name'])
        form.addRow('Mobil:', self.entries['cell'])
        form.addRow('Telefon:', self.entries['home'])
        form.addRow('E-Mail:', self.entries['email'])
        layout.addLayout(form)
        
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save); btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.show()

    def _save(self):
        self.card['first_name'] = self.entries['first_name'].text()
        self.card['last_name'] = self.entries['last_name'].text()
        self.card.setdefault('tels', {})['CELL'] = self.entries['cell'].text()
        self.card.setdefault('tels', {})['HOME'] = self.entries['home'].text()
        self.card.setdefault('emails', {})['HOME'] = self.entries['email'].text()
        self.on_save(self.card)
        self.accept()


def simple_input(parent, title, prompt) -> str | None:
    dlg = QDialog(parent); dlg.setWindowTitle(title)
    l = QVBoxLayout(dlg); l.addWidget(QLabel(prompt)); e = QLineEdit(); l.addWidget(e)
    b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); b.accepted.connect(dlg.accept); b.rejected.connect(dlg.reject); l.addWidget(b)
    if dlg.exec() == QDialog.Accepted: return e.text().strip() or None
    return None


def run():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    win = QuickSyncGUI()
    win.show()
    app.exec()


if __name__ == '__main__':
    run()