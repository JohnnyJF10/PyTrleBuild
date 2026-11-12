"""Finding the input files of an export: explicit paths first, the editor's folder layout second.

A project (``.PRJ``) does not contain everything the export needs.  It refers to three more files:

* the **object list**, which names the object slots (the editor reads ``objects.h`` from its folder;
  this package ships the same list as ``pyprj2tom/data/objects.json``);
* the **texture** (a 24-bit ``.TGA``), stored in the project as a full Windows path;
* the **object file** (``.WAD``, with the sprites in a ``.SWD`` of the same name), also stored as a
  full Windows path.

The original editor took those paths literally, so a project only exported on the computer it was
made on, or in an installation with the same layout.  :class:`ProjectInputs` resolves each file in
this order:

1. a path given explicitly (on the command line: ``--objects``, ``--texture``, ``--wad``, ``--swd``);
2. for the object list: the bundled ``pyprj2tom/data/objects.json`` (no editor files needed);
3. for the texture and the object files: the stored path, translated into the editor folder given as
   ``root`` (``--root``): everything from ``MAPS\\`` or ``GRAPHICS\\`` onward is kept and attached
   to the root.

All lookups ignore letter case on case-sensitive file systems (see :mod:`trle_common.paths`).
"""
import os
import re
from dataclasses import dataclass
from typing import Optional

from trle_common.paths import find_existing, native_path, split_stored_path, with_extension

#: The object list of the original editor (``OBJECTS.H``, object version 129) as JSON.
BUNDLED_OBJECTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data',
                                    'objects.json')

_INSTALL_FOLDERS = ('MAPS', 'GRAPHICS')
_DRIVE_LETTER = re.compile(r'^[A-Za-z]:')


class InputFileError(Exception):
    """An input file of the export cannot be found; the message says how to provide it."""


def resolve_install_path(install_root, stored_path):
    """Translate a path stored in a project into a path inside an editor installation.

    Project files hold paths like ``C:\\PROGRA~1\\COREDE~1\\TRLE\\MAPS\\X\\Y.TGA``.  Everything from
    the ``MAPS`` or ``GRAPHICS`` folder onward is kept and attached to ``install_root``.  A path
    without either folder is taken relative to the root (or, on Windows, as it is if it is absolute).
    """
    parts = split_stored_path(stored_path)
    upper = [part.upper() for part in parts]
    for folder in _INSTALL_FOLDERS:
        # A folder name only counts if something follows it (the original searches "\MAPS\").
        if folder in upper[:-1]:
            return find_existing(os.path.join(install_root, *parts[upper.index(folder):]))
    if _DRIVE_LETTER.match(stored_path):
        if os.name == 'nt':
            return find_existing(native_path(stored_path))
        parts = parts[1:]                               # a drive letter means nothing elsewhere
    return find_existing(os.path.join(install_root, *parts))


@dataclass
class ProjectInputs:
    """Where to find the files a project refers to.  Every field is optional.

    ``root`` is the editor folder used as the fallback for files not given explicitly.
    """
    root: Optional[str] = None
    objects: Optional[str] = None
    texture: Optional[str] = None
    wad: Optional[str] = None
    swd: Optional[str] = None

    def objects_path(self):
        """Return the object list to use: the one given explicitly, else the bundled JSON."""
        if self.objects:
            return self._explicit(self.objects, '--objects')
        return BUNDLED_OBJECTS_PATH

    def texture_path(self, stored_path):
        """Return the texture image to use for the path stored in the project."""
        if self.texture:
            return self._explicit(self.texture, '--texture')
        return self._from_root(stored_path, 'texture', '--texture')

    def wad_path(self, stored_path):
        """Return the ``.WAD`` to use.  The project usually stores the ``.SWD`` name instead."""
        if self.wad:
            return self._explicit(self.wad, '--wad')
        return self._from_root(stored_path, 'object file (.WAD)', '--wad', '.wad')

    def swd_path(self, stored_path):
        """Return the ``.SWD`` (sprites) to use; it is optional, so a missing file is allowed."""
        if self.swd:
            return self._explicit(self.swd, '--swd')
        if self.wad:                                    # look beside an explicitly given .WAD
            return find_existing(with_extension(self.wad, '.swd'))
        if not self.root:
            return None
        return find_existing(with_extension(resolve_install_path(self.root, stored_path), '.swd'))

    @staticmethod
    def _explicit(path, option):
        """Return an explicitly given path as it exists on disk, or fail with a clear message."""
        found = find_existing(path)
        if not os.path.isfile(found):
            raise InputFileError(f'{option}: file not found: {path}')
        return found

    def _from_root(self, stored_path, description, option, extension=None):
        """Resolve a stored path inside the root folder, or fail naming the option to use."""
        if not self.root:
            raise InputFileError(f'The project refers to the {description} "{stored_path}". '
                                 f'Give it with {option}, or give the editor folder with --root.')
        path = resolve_install_path(self.root, stored_path)
        if extension:
            path = find_existing(with_extension(path, extension))
        if not os.path.isfile(path):
            raise InputFileError(f'The project refers to the {description} "{stored_path}", '
                                 f'which is not at {path}. Give it with {option}.')
        return path
