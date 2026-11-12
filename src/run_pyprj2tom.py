#!/usr/bin/env python3
"""Run ``pyprj2tom`` from a source checkout without installing it.

    python src/run_pyprj2tom.py level.prj --texture level.tga --wad level.wad --out out
    python src/run_pyprj2tom.py --help

After ``pip install .`` the same command is available as ``pyprj2tom``.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pyprj2tom.cli import main  # noqa: E402  (needs the path set up above)

if __name__ == '__main__':
    sys.exit(main())
