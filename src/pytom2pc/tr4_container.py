"""The outer structure of a .TR4 file: compressed blocks followed by the sound samples.

File layout::

    'TR4\\0', room page count, object page count, bump page count   (three 16-bit values)
    four zlib blocks:  32-bit pages, 16-bit pages, font + sky, level data
    sample count, then every sample

Each block is preceded by its uncompressed and its compressed size.
"""
import os
import struct

from trle_common import classic_zlib
from pytom2pc.level_files import read_optional_file
from trle_common.paths import find_existing, split_stored_path

_FONT_AND_SKY_BYTES = 0x80000
_COMPRESSION_LEVEL = 6


def compress_level(level):
    """Build the compressed part of the .TR4 from the repacked level."""
    room_pages, object_pages, bump_pages = struct.unpack_from('<hhh', level, 0)
    position = 6
    output = bytearray(b'TR4\0')
    output += struct.pack('<hhh', room_pages, object_pages, bump_pages)

    total_pages = room_pages + object_pages + bump_pages
    remaining = len(level)
    for block_size in (total_pages * 0x40000, total_pages * 0x20000, _FONT_AND_SKY_BYTES):
        block = level[position:position + block_size]
        position += block_size
        remaining -= block_size
        _append_block(output, block, block_size)

    # "remaining" still includes the six bytes of the page counts, so the original reads six
    # bytes past the end of its buffer here.  They are always zero.
    block = level[position:position + remaining].ljust(remaining, b'\0')
    _append_block(output, block, remaining)
    return output


def _append_block(output, block, uncompressed_size):
    """Append a compressed block with its uncompressed and compressed size."""
    compressed = classic_zlib.compress(block, _COMPRESSION_LEVEL)
    output += struct.pack('<II', uncompressed_size, len(compressed)) + compressed


def build_sample_section(sample_list, samples_directory, log):
    """Return the sample section for the text of a .SAM file (one file name per line).

    When a sample cannot be read, the original still writes a size field holding the size of the
    previous sample, then simply continues with the next name.
    """
    text = bytearray(sample_list)
    line_count = text.count(b'\n')
    for index, value in enumerate(text):
        if value in (10, 13):
            text[index] = 0

    output = bytearray(struct.pack('<i', line_count))
    last_size = len(text)
    position = 0
    while position < len(text):
        end = text.find(b'\0', position)
        if end < 0:
            end = len(text)
        name = text[position:end].decode('latin-1')
        data = read_optional_file(_sample_path(samples_directory, name))
        if data:
            last_size = len(data)
        output += struct.pack('<I', last_size)
        if data is None:
            log(f'*** File Not Found {name}')
        else:
            output += struct.pack('<I', len(data)) + data
        position = end + 2                              # skips the CR LF line ending
    return bytes(output)


def _sample_path(samples_directory, name):
    """Return the path of a sample named in the .SAM file (the name may contain backslashes)."""
    return find_existing(os.path.join(samples_directory, *split_stored_path(name)))
