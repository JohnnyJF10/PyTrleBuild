"""Operating-system independent handling of the file names the level editor stores.

The Tomb Raider Level Editor was a Windows program.  Its files contain paths like
``C:\\PROGRA~1\\COREDE~1\\TRLE\\MAPS\\ANGKOR\\angtext.TGA``: backslashes as separators, a drive letter,
8.3 short names, and a letter case that often does not match the files on disk (``Catacomb.TOM``
next to ``catacomb.sam``).  On Windows none of this matters, because the file system ignores case.
On Linux and macOS (case-sensitive file systems) a plain ``open()`` of such a name fails.

The helpers here make the converters behave the same on every system:

* :func:`split_stored_path` splits a stored path at either kind of separator;
* :func:`find_existing` looks a path up component by component, ignoring letter case where the file
  system does not, and returns the real name of the file on disk.
"""
import os
import re

_SEPARATORS = re.compile(r'[\\/]+')


def split_stored_path(text):
    """Split a path written by a Windows program into its components (empty parts dropped)."""
    return [part for part in _SEPARATORS.split(text) if part]


def native_path(text):
    """Return a stored path with the separators of the current system."""
    return text.replace('\\', os.sep).replace('/', os.sep)


def find_existing(path):
    """Return ``path`` as it exists on disk, matching each component without regard to case.

    If the path exists as given it is returned unchanged.  Otherwise every component that does not
    exist with the given spelling is looked up case-insensitively in its parent directory.  When no
    match is found the original path is returned, so that the caller's ``open()`` reports a normal
    "file not found" error naming the path the user expects.
    """
    if not path or os.path.exists(path):
        return path
    absolute = os.path.abspath(native_path(path))
    drive, rest = os.path.splitdrive(absolute)
    current = drive + os.sep
    for component in [part for part in rest.split(os.sep) if part]:
        candidate = os.path.join(current, component)
        if not os.path.exists(candidate):
            candidate = _match_case_insensitively(current, component)
            if candidate is None:
                return path
        current = candidate
    return current


def _match_case_insensitively(directory, name):
    """Return the entry of ``directory`` whose name equals ``name`` ignoring case, or ``None``."""
    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    wanted = name.casefold()
    matches = sorted(entry for entry in entries if entry.casefold() == wanted)
    return os.path.join(directory, matches[0]) if matches else None


def with_extension(path, extension):
    """Return ``path`` with its extension replaced (``extension`` includes the dot)."""
    return os.path.splitext(path)[0] + extension
