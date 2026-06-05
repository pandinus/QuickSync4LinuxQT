#!/usr/bin/env python3
"""QuickSync4Linux GUI – PySide6 version (drop-in replacement for the tkinter gui.py)"""

import glob
import json
import os
import re
import subprocess
import tempfile
import threading

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QTextEdit, QFrame,
    QDialog, QDialogButtonBox, QMessageBox, QFileDialog,
    QFormLayout, QSizePolicy, QStatusBar, QAbstractItemView, QStyle
)
from PySide6.QtGui import QFont

from . import vcard
from . import btserial

OBEX_RECOVERY_DELAY = 1.5
DEFAULT_DEVICE = '/dev/ttyACM0'
DEFAULT_BAUD = '9600'

from . import backend

DEFAULT_LOG_FILE = backend.DEFAULT_LOG_FILE
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


def simple_input(parent, title: str, label: str) -> str:
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, title, label)
    return text.strip() if ok else ''


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

        self.baud = QLineEdit(DEFAULT_BAUD)
        self.baud.setVisible(False)
        right_col.addWidget(dev_row_widget)

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

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        right_col.addWidget(line)

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

    def check_connection_or_warn(self) -> bool:
        if not self.current_device():
            QMessageBox.warning(self, 'Kein Gerät', 'Bitte zuerst ein Gerät auswählen.')
            return False
        if self._device_connected is not True:
            QMessageBox.warning(self, 'Nicht verbunden', 'Bitte zuerst eine Verbindung herstellen.')
            return False
        return True

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
        backend.log(text)

    
    def set_log_file(self, path: str | None = None):
        self._append_output(f'Log-Datei: {backend.DEFAULT_LOG_FILE} (max. 1 MB, 3 Backups)')

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
        if not self.check_connection_or_warn():
            return
        path, _ = QFileDialog.getOpenFileName(self, 'VCF-Datei wählen', os.path.expanduser('~'), '', 'VCF-Dateien (*.vcf);;Alle Dateien (*)')
        if path:
            self.run_action(action, None, path)

    def get_contacts(self):
        if not self.check_connection_or_warn():
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Kontakte speichern als', '', 'VCF-Dateien (*.vcf);;Alle Dateien (*)')
        if path:
            self.run_action('getcontacts', None, path)

    
    def open_log(self):
        # 1. Nutze das Attribut der GUI, falls gesetzt, andernfalls direkt das Backend-Standard-Log
        log_path = getattr(self, '_log_file', None) or backend.DEFAULT_LOG_FILE
        
        # 2. Pfad absolut auflösen (Tilde expandieren)
        path_target = os.path.abspath(os.path.expanduser(log_path))
        
        # 3. Den Ordner ermitteln (egal ob Datei oder Ordner übergeben wurde)
        initial_dir = path_target if os.path.isdir(path_target) else os.path.dirname(path_target)

        # Sicherheitsnetz: Falls die App frisch installiert ist und der Ordner noch fehlt
        if not os.path.exists(initial_dir):
            try:
                os.makedirs(initial_dir, exist_ok=True)
            except Exception:
                initial_dir = os.path.expanduser('~')

        # Dialog im richtigen Verzeichnis öffnen
        path, _ = QFileDialog.getOpenFileName(
            self, 'Log-Datei wählen', initial_dir, 'Log-Dateien (*.log);;Alle Dateien (*)'
        )
        
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
        from PySide6.QtWidgets import QSpinBox
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
        from PySide6.QtWidgets import QComboBox as _CB
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
        if not self.check_connection_or_warn():
            return
        if hasattr(self, '_file_manager_win') and self._file_manager_win is not None:
            self._file_manager_win.raise_()
            self._file_manager_win.activateWindow()
            return
        self._file_manager_win = FileManagerWindow(self)
        self._file_manager_win.finished.connect(lambda: setattr(self, '_file_manager_win', None))

    def open_contacts_manager(self):
        if not self.check_connection_or_warn():
            return
            return
        if hasattr(self, '_contacts_win') and self._contacts_win is not None:
            self._contacts_win.raise_()
            self._contacts_win.activateWindow()
            return
        self._contacts_win = ContactsWindow(self)
        self._contacts_win.finished.connect(lambda: setattr(self, '_contacts_win', None))

    def _interpret_connection_error(self, text: str) -> str:
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
        path, _ = QFileDialog.getOpenFileName(self,  'Datei zum Hochladen wählen')
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
                        try:
                            fd, vcf_path = tempfile.mkstemp(suffix='.vcf', prefix='quicksync_count_')
                            os.close(fd)
                            cmd_contacts = self.build_cmd('getcontacts', file=vcf_path)
                            proc_contacts = subprocess.run(cmd_contacts, capture_output=True, text=True, timeout=CHECK_TIMEOUT)
                            if proc_contacts.returncode == 0:
                                with open(vcf_path, 'r', encoding='utf-8') as f_vcf:
                                    vcf_data = f_vcf.read()
                                count = len(vcard.parseCards(vcf_data))
                                sig.action_finished.emit('contact_count', str(count))
                            try:
                                os.unlink(vcf_path)
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


# ─── Zusätzliche Fenster-Klassen ──────────────────────────────────────────────

class FileManagerWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Dateimanager')
        self.resize(640, 440)
        layout = QVBoxLayout(self)

        lbl = QLabel('Dateimanager-Inhalte (Platzhalter)')
        lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)


class ContactsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Kontakte verwalten')
        self.resize(640, 440)
        layout = QVBoxLayout(self)

        lbl = QLabel('Kontaktverwaltungs-Inhalte (Platzhalter)')
        lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)


def run():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    win = QuickSyncGUI()
    win.show()
    app.exec()


if __name__ == '__main__':
    run()