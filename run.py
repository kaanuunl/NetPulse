"""Launcher used by the packaged executable and by autostart entries."""

import sys

from agnabzi.app import main

if __name__ == "__main__":
    sys.exit(main())
