"""Deflate compression that matches the original zlib, even on Python builds using zlib-ng.

``tom2pc.exe`` compresses with the classic zlib.  Python 3.14 and later on Windows link
``zlib-ng`` instead.  Its streams decompress to the same data, but the compressed bytes differ,
so a .TR4 written with it is valid yet not byte-identical to one written by the original tool.

:func:`compress` therefore looks for a classic zlib in this order:

1. Python's own ``zlib`` module, unless it is zlib-ng;
2. the library named by the ``CLASSIC_ZLIB_DLL`` environment variable (a ``.dll``, ``.so`` or
   ``.dylib``);
3. the ``zlib1.dll`` installed with Git for Windows;
4. the system's ``libz`` (Linux, macOS), found with :func:`ctypes.util.find_library`.

On Linux and macOS step 1 normally succeeds, because Python uses the system's classic zlib.

If none is found it falls back to Python's ``zlib``, and :data:`IS_BYTE_EXACT` is ``False``.
"""
import ctypes
import ctypes.util
import os
import zlib

_GIT_INSTALL_DIRECTORIES = (
    r'C:\Program Files\Git\mingw64\bin',
    r'C:\Program Files\Git\usr\bin',
    r'C:\Program Files (x86)\Git\mingw64\bin',
)
_DLL_NAMES = ('zlib1.dll', 'msys-z.dll')


def _candidate_dll_paths():
    """Yield the places to look for the DLL: the environment variable, then Git for Windows."""
    configured = os.environ.get('CLASSIC_ZLIB_DLL')
    if configured:
        yield configured
    if os.name == 'nt':
        for directory in _GIT_INSTALL_DIRECTORIES:
            for name in _DLL_NAMES:
                yield os.path.join(directory, name)
    else:
        system_library = ctypes.util.find_library('z')
        if system_library:
            yield system_library


def _load_classic_library():
    """Return a loaded classic zlib, or ``None`` when there is none we can use."""
    if not hasattr(zlib, 'ZLIBNG_VERSION'):
        return None
    for path in _candidate_dll_paths():
        if os.path.isabs(path) and not os.path.exists(path):
            continue
        try:
            library = ctypes.CDLL(path)
            library.compress2.restype = ctypes.c_int
            library.compressBound.restype = ctypes.c_ulong
        except (OSError, AttributeError):
            continue
        return library
    return None


_classic_library = _load_classic_library()

#: Whether the streams produced by :func:`compress` equal those of the classic zlib.
IS_BYTE_EXACT = _classic_library is not None or not hasattr(zlib, 'ZLIBNG_VERSION')


def compress(data, level):
    """Return ``data`` compressed to a zlib stream at the given compression level."""
    if _classic_library is None:
        return zlib.compress(data, level)
    source = bytes(data)
    capacity = _classic_library.compressBound(ctypes.c_ulong(len(source)))
    target = ctypes.create_string_buffer(capacity)
    target_length = ctypes.c_ulong(capacity)
    status = _classic_library.compress2(target, ctypes.byref(target_length), source,
                                        ctypes.c_ulong(len(source)), ctypes.c_int(level))
    if status != 0:
        raise RuntimeError(f'classic zlib failed with status {status}')
    return target.raw[:target_length.value]
