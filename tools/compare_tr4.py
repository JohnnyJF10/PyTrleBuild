#!/usr/bin/env python3
"""Compare two .TR4 files: whole file, then each decompressed block and the sample section.

    python tools/compare_tr4.py mine.tr4 reference.tr4

Two files whose blocks decompress to the same bytes contain the same level even if the files differ;
that happens when one was compressed with a different zlib (see docs/PYTOM2PC.md, section 6).
Exit code 0 if the levels are identical (file or decompressed content), 1 otherwise.
"""
import struct
import sys
import zlib

_BLOCKS = ('32-bit texture pages', '16-bit texture pages', 'font and sky', 'level data')


def read_tr4(path):
    """Return the header values, the four decompressed blocks and the raw sample section."""
    with open(path, 'rb') as file:
        data = file.read()
    if data[:4] != b'TR4\0':
        raise ValueError(f'{path} is not a .TR4 file')
    header = struct.unpack_from('<3H', data, 4)
    position = 10
    blocks = []
    for _ in _BLOCKS:
        _, compressed_size = struct.unpack_from('<II', data, position)
        position += 8
        blocks.append(zlib.decompress(data[position:position + compressed_size]))
        position += compressed_size
    return header, blocks, data[position:]


def main(arguments):
    """Print the comparison; returns the exit code."""
    if len(arguments) != 2:
        print(__doc__)
        return 2
    first, second = arguments
    with open(first, 'rb') as a, open(second, 'rb') as b:
        if a.read() == b.read():
            print('files are byte-identical')
            return 0
    header_a, blocks_a, samples_a = read_tr4(first)
    header_b, blocks_b, samples_b = read_tr4(second)
    identical = header_a == header_b and samples_a == samples_b
    print(f'page counts (room, object, bump): {header_a} vs {header_b}')
    for name, block_a, block_b in zip(_BLOCKS, blocks_a, blocks_b):
        if block_a == block_b:
            print(f'{name:22s} identical ({len(block_a)} bytes)')
            continue
        identical = False
        differing = sum(x != y for x, y in zip(block_a, block_b)) + abs(len(block_a) - len(block_b))
        first_offset = next((i for i, (x, y) in enumerate(zip(block_a, block_b)) if x != y),
                            min(len(block_a), len(block_b)))
        print(f'{name:22s} DIFFERENT: {differing} bytes, first at offset {first_offset:#x} '
              f'(sizes {len(block_a)} and {len(block_b)})')
    print(f'{"sound samples":22s} {"identical" if samples_a == samples_b else "DIFFERENT"}')
    print('same level content, different compression' if identical else 'levels differ')
    return 0 if identical else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
