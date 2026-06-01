# readme.md

# QuickSync4LinuxGui

This repository is a fork of [schorschii/QuickSync4Linux](https://github.com/schorschii/QuickSync4Linux). It extends the original command-line utility with a modern, graphical user interface (GUI) implementation of the Gigaset QuickSync software for Linux.

The communication with the device is based on AT commands over a USB/Bluetooth serial port. For file transfer, the device is set into Obex mode. The GUI acts as a seamless Dolphin-styled interface to manage your device, contacts, and files without touching the command line.

## About this Fork

This project is an unofficial graphical frontend expansion for [QuickSync4Linux](https://github.com/schorschii/QuickSync4Linux). It packages the core synchronization logic into a native Qt/QML-inspired interface using Python and PySide6, while preserving all the underlying features and stability of the original CLI application.

## Prerequisites & Installation

### 1\. Hardware Setup

Make sure your user is in the `dialout` group in order to access the serial port.

```bash
sudo usermod -aG dialout <username>
# logout and login again to apply group membership
```

### 2\. Install Python Dependencies

```
pip install -r requirements.txt
```

### 3\. System Dependencies

The application relies on standard Linux utilities for specific features:

- `bluez` / `bluez-utils` (provides `bluetoothctl` for automatic Bluetooth device discovery)
- `xdg-utils` (provides `xdg-open` to easily view system logs directly from the GUI)

## Usage

To start the graphical interface, simply execute it from your terminal:

```
cd /Pfad_zu_QuickSync4LinuxGui/
python3 -m QuickSync4LinuxGui
```

### GUI Features Overview

- **Automatic Device Discovery:** Automatically scans and lists available serial ports (`/dev/ttyACM*`, `/dev/ttyUSB*`, `/dev/rfcomm*`) as well as paired Bluetooth devices.
- **Device Information:** Displays manufacturer, model, firmware version, and contact counts at a single glance.
- **Contact Manager:** A full visual contact editor to browse, add, edit, or delete handset contacts before synchronizing changes back to the device.
- **File Manager:** A Dolphin-styled file browser supporting separate folders (Pictures, Sounds, etc.), file downloads, local uploads, and even an image preview panel for compatible formats.
- **Persistent Settings:** Adjust timeout configurations and serial baud rates via dedicated configuration dialogs.

_Note: For debugging and logging, the GUI automatically captures terminal logs and saves them to_ `~/.config/QuickSync4LinuxGui/`_. You can view or open them directly via the "Log öffnen" button in the sidebar._

## Screenshots:

Hauptfenster

![alt text](<Screenshots/QuickSync4LinuxGui - Hauptfenster.png>)

Kontaktverwaltung:

![alt text](<Screenshots/QuickSync4LinuxGui - Kontakteverwaltung.png>)

Dateiverwatung:

![alt text](<Screenshots/QuickSync4LinuxGui - Dateiverwaltung.png>)

Einstellungen:

![](<Screenshots/QuickSync4LinuxGui - Einstellung Timeouts.png>)

![alt text](<Screenshots/QuickSync4LinuxGui - Einstellung Baudrate.png>)