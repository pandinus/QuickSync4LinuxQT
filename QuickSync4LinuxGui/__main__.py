import sys

# HIER den neuen Dateinamen eintragen (ohne .py)
from . import gui_qt 
from . import quicksync

if __name__ == '__main__':
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] in ('gui', '--gui', '-g')):
        gui_qt.run() # HIER auch anpassen
    else:
        quicksync.main()