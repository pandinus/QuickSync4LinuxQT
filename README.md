# QuickSync4LinuxGui

A graphical user interface (GUI) for [QuickSync4Linux](https://github.com/schorschii/QuickSync4Linux). It extends the original command-line utility with a modern PySide6-based interface to manage your Gigaset device, contacts, and files without touching the command line.

The communication with the device is based on AT commands over a USB/Bluetooth serial port. For file transfer, the device is set into Obex mode.

## Prerequisites & Installation

### 1. Hardware Setup

Make sure your user is in the `dialout` group in order to access the serial port.

```bash
sudo usermod -aG dialout <username>
# logout and login again to apply group membership
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

For GUI support, install the optional `gui` extra (PySide6) instead:

```bash
pip install .[gui]
```

### 3. System Dependencies

- `bluez` / `bluez-utils` (provides `bluetoothctl` for automatic Bluetooth device discovery)
- `xdg-utils` (provides `xdg-open` to view log files from the GUI)

## Usage

To start the graphical interface:

```bash
python3 -m QuickSync4LinuxGui
```

Alternatively, install the desktop entry to launch the GUI from your application menu or file manager:

```bash
cp QuickSync4LinuxGui.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

The CLI remains fully usable without GUI components:

```bash
python3 -m QuickSync4LinuxGui listfiles -d <MAC>
python3 -m QuickSync4LinuxGui getcontacts -d <MAC> -f contacts.vcf
```

## GUI Features

- **Automatic Device Discovery:** Scans paired Bluetooth devices and available serial ports (`/dev/ttyACM*`, `/dev/ttyUSB*`, `/dev/rfcomm*`).
- **Device Information:** Displays manufacturer, model, firmware version, and contact count.
- **Contact Manager:** Browse, add, edit, or delete contacts and sync them back to the device.
- **File Manager:** Dolphin-styled file browser with folder navigation, download, upload, and image preview.
- **Settings:** Configure timeouts and serial baud rate via settings dialogs.
- **Logging:** Logs are saved to `~/.config/QuickSync4LinuxGui/` and can be opened via the sidebar.

## Screenshots

Main window:

![Main window](Screenshots/QuickSync4LinuxGui%20-%20Main%20Window.png)

Contact manager:

![Contact manager](Screenshots/QuickSync4LinuxGui%20-%20Contact%20Manager.png)

File manager:

![File manager](Screenshots/QuickSync4LinuxGui%20-%20File%20Manager.png)

Settings:

![Settings timeouts](Screenshots/QuickSync4LinuxGui%20-%20Settings%20Timeouts.png)

![Settings baudrate](Screenshots/QuickSync4LinuxGui%20-%20Settings%20Baudrate.png)

![Settings Language](Screenshots/QuickSync4LinuxGui%20-%20Settings%20Language.png)