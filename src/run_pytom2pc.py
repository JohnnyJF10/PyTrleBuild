#!/usr/bin/env python3
"""Run ``pytom2pc`` from a source checkout without installing it.

    python src/run_pytom2pc.py level.tom --samples sound/samples --dest out
    python src/run_pytom2pc.py --help

After ``pip install .`` the same command is available as ``pytom2pc``.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pytom2pc.cli import main  # noqa: E402  (needs the path set up above)

if __name__ == '__main__':
    sys.exit(main())
