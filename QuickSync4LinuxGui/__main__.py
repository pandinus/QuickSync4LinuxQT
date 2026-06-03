import sys

# HIER den neuen Dateinamen eintragen (ohne .py)
from . import gui
from . import quicksync

if __name__ == '__main__':
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] in ('gui', '--gui', '-g')):
        gui.run() # HIER auch anpassen
    else:
        quicksync.main()