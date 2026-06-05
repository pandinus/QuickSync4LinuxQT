import sys
import os

from . import quicksync

def _run_gui():
    # Desktop-Umgebung erkennen
    de = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    kde = 'kde' in de or 'plasma' in de

    # Kommandozeilen-Override
    if '--qt' in sys.argv:
        backends = ['qt', 'gtk']
    elif '--gtk' in sys.argv:
        backends = ['gtk', 'qt']
    elif kde:
        backends = ['qt', 'gtk']
    else:
        # Cinnamon, GNOME, XFCE etc. → GTK zuerst
        backends = ['gtk', 'qt']

    for b in backends:
        try:
            if b == 'gtk':
                from . import gui_gtk
                gui_gtk.run()
                return
            else:
                from . import gui
                gui.run()
                return
        except Exception as e:
            print(f'[{b.upper()}] nicht verfügbar: {e}')

    print('Fehler: Weder GTK noch Qt verfügbar.')
    print('  GTK: sudo apt install python3-gi gir1.2-gtk-3.0')
    print('  Qt:  pip install PySide6 && sudo apt install libxcb-cursor0')
    sys.exit(1)

if __name__ == '__main__':
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] in ('gui', '--gui', '-g', '--gtk', '--qt')):
        _run_gui()
    else:
        quicksync.main()