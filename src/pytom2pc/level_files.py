"""Locating and reading the set of files that together describe one level.

A level is a group of files with the same name in one directory, for example ``tut1.tom``,
``tut1.wad``, ``tut1.swd``, ``tut1.sfx`` and ``tut1.sam``.  ``tom2pc.exe`` only knew this layout:
it derived every other file name from the name of the ``.tom``.  Here that is the fallback; each file
can also be given explicitly (see :class:`LevelFiles`), and names are matched without regard to
letter case on every operating system.
"""
import os

from trle_common.paths import find_existing

#: The files of a level, by the key used in :meth:`LevelFiles.path` and in the overrides.
LEVEL_FILE_KEYS = ('tom', 'wad', 'swd', 'sfx', 'sam', 'raw', 'cin')
#: Files shared by all levels of a directory, by file name.
SHARED_FILE_NAMES = ('font.pc', 'pcsky.raw')


class ConversionError(Exception):
    """The input cannot be converted; the message says why."""


class LevelFiles:
    """Builds the path of each file belonging to the level named by a ``.tom`` path.

    ``overrides`` maps a key of :data:`LEVEL_FILE_KEYS` (``'wad'``, ``'sam'``, ...) or a name of
    :data:`SHARED_FILE_NAMES` (``'font.pc'``, ``'pcsky.raw'``) to an explicit path.  Files without an
    override are looked for beside the ``.tom``, as ``tom2pc.exe`` did.
    """

    def __init__(self, tom_path, overrides=None):
        """Derive the directory and the level name from the path of the .TOM file."""
        self.directory = os.path.dirname(os.path.abspath(tom_path))
        self.level_name = os.path.splitext(os.path.basename(tom_path))[0]
        self.overrides = {key: path for key, path in (overrides or {}).items() if path}
        self.overrides.setdefault('tom', tom_path)

    def path(self, extension):
        """Return the path of the level's file with the given extension."""
        if extension in self.overrides:
            return find_existing(self.overrides[extension])
        return find_existing(os.path.join(self.directory, f'{self.level_name}.{extension}'))

    def shared_path(self, file_name):
        """Return the path of a file that is used by all levels in the directory."""
        if file_name in self.overrides:
            return find_existing(self.overrides[file_name])
        return find_existing(os.path.join(self.directory, file_name))


def read_optional_file(path):
    """Return the content of a file, or ``None`` when it is missing or empty."""
    try:
        with open(path, 'rb') as file:
            return file.read() or None
    except OSError:
        return None


def read_required_file(path):
    """Return the content of a file, raising :class:`ConversionError` when it is missing."""
    try:
        with open(path, 'rb') as file:
            return file.read()
    except OSError as error:
        raise ConversionError(f'ERROR: Could not find {path}') from error
