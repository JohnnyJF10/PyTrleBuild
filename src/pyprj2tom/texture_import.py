"""Importing the project's texture image and building the colour tables derived from it.

The texture must be a 24-bit TGA file.  Besides the image, the export needs:

* a 256-colour **palette** made by quantising the image,
* a **colour cache**: the palette entry for each 5:5:5 colour,
* a **light table**: for each of 32 brightness levels, the palette entry of each darkened colour,
* for each 64 x 64 pixel **cell**, whether it contains transparent (magenta) pixels.
"""
import struct
from dataclasses import dataclass

import numpy as np

from pyprj2tom import neuquant

_CELL_SIZE = 64
_MAX_CELLS = 256
_IMAGE_WIDTH = 256
_KEY_COLOUR = (255, 0, 255)
_CACHE_ENTRIES = 0x8000
_EMPTY_CACHE_ENTRY = -1
_BRIGHTNESS_LEVELS = 32

# Layout of the editor's memory behind the colour cache, as offsets from the start of the cache.
_MEMORY_AFTER_CACHE_SIZE = 0x20000
_CACHE_BASE_TO_PALETTE_6BIT = 0x100E0
_CACHE_BASE_TO_IMAGE_SIZE = 0x10463
_CACHE_BASE_TO_PALETTE_8BIT = 0x10471
_EDITOR_STATE_AFTER_CACHE = ((0x103E0, 0), (0x103E4, -1), (0x103E8, -1), (0x10408, 0x14),
                             (0x1040C, 0x14), (0x10414, 1), (0x10420, 1), (0x10424, 1),
                             (0x10440, 1), (0x10450, 1))

#: Fixed interface colours, 6 bits per channel, which must be available in the palette.
_INTERFACE_COLOURS = ((38, 38, 38), (25, 25, 25), (63, 63, 63), (63, 0, 0), (0, 40, 0),
                      (0, 50, 50), (50, 0, 50), (0, 20, 0), (0, 60, 0), (63, 63, 0), (0, 0, 63),
                      (63, 32, 0), (32, 0, 0), (63, 24, 24))


class TextureError(Exception):
    """The texture file cannot be used."""


@dataclass
class ImportedTexture:
    """The texture image in the form the export uses."""

    width: int
    height: int
    rgb: bytes
    palette_6bit: bytes
    light_table: bytes
    cell_transparency: list


def _read_tga(path):
    """Return ``(width, height, rgb_bytes)``; rows always run from top to bottom."""
    with open(path, 'rb') as file:
        data = file.read()
    if data[2] != 2 or data[16] != 24:
        raise TextureError(f'{path}: only uncompressed 24-bit TGA files can be used')
    width, source_height = struct.unpack_from('<HH', data, 12)
    height = (source_height + 63) & ~63          # always a whole number of 64-pixel rows
    row_bytes = width * 3
    top_down = bool(data[17] & 0x20)
    rgb = bytearray(row_bytes * height)
    for row in range(min(source_height, height)):
        source_row = row if top_down else source_height - 1 - row
        start = 18 + source_row * row_bytes
        rgb[row * row_bytes:(row + 1) * row_bytes] = _bgr_to_rgb(data[start:start + row_bytes])
    return width, height, bytes(rgb)


def _bgr_to_rgb(pixels):
    """Swap the red and blue channels of a row of pixels."""
    array = np.frombuffer(pixels, np.uint8).reshape(-1, 3)
    return array[:, ::-1].tobytes()


def _build_palette(rgb):
    """Quantise the image and return ``(palette_8bit, has_key_colour)``.

    The first entry stays black (index 0 means transparent), the other 255 are learnt from all
    non-magenta pixels.
    """
    pixels = np.frombuffer(rgb, np.uint8).reshape(-1, 3)
    red, green, blue = _KEY_COLOUR
    is_key = (pixels[:, 0] == red) & (pixels[:, 1] == green) & (pixels[:, 2] == blue)
    # Transparency is always reserved, whether the image contains magenta or not.
    network_size = 0x7F
    network = neuquant.learn_palette(pixels[~is_key].tobytes(), network_size=network_size)
    full_network = np.zeros((256, 3), dtype=np.int64)
    full_network[:network_size] = network
    neuquant.fill_colour_cube(full_network)
    neuquant.swap_nearest_to_front(full_network, _INTERFACE_COLOURS)

    palette = bytearray(3)
    palette += full_network[:255].astype(np.uint8).tobytes()
    return bytes(palette), pixels, is_key


def _build_colour_cache(pixels, is_key, palette):
    """Assign, to each 5:5:5 cell, the palette entry nearest the first pixel found in the cell."""
    wide = pixels.astype(np.int64)
    keys = (wide[:, 0] >> 3) * 0x400 + (wide[:, 1] >> 3) * 0x20 + (wide[:, 2] >> 3)
    colours = np.frombuffer(palette, dtype=np.uint8).astype(np.int64).reshape(256, 3)[1:]
    normal_pixels = np.nonzero(~is_key)[0]
    unique_keys, first_seen = np.unique(keys[normal_pixels], return_index=True)
    cache = np.full(_CACHE_ENTRIES, _EMPTY_CACHE_ENTRY, dtype=np.int64)
    for key, position in zip(unique_keys.tolist(), normal_pixels[first_seen].tolist()):
        distances = ((colours - wide[position]) ** 2).sum(axis=1)
        cache[key] = int(distances.argmin()) + 1
    return cache, keys


def _complete_colour_cache(cache, palette_6bit):
    """Fill the remaining cache entries with the nearest palette colour (cell centres, 6-bit)."""
    colours = np.frombuffer(palette_6bit, dtype=np.uint8).astype(np.int64).reshape(256, 3)
    for key in np.nonzero(cache == _EMPTY_CACHE_ENTRY)[0].tolist():
        wanted = np.array([(key >> 10) * 2, ((key >> 5) & 31) * 2, (key & 31) * 2], dtype=np.int64)
        cache[key] = int(((wanted - colours) ** 2).sum(axis=1).argmin())


def _memory_after_cache(cache, palette_6bit, palette_8bit, width, height):
    """Rebuild the editor memory that the light table calculation reads.

    The calculation multiplies 6-bit colours by up to 62 and uses the result as a 5:5:5 index.
    For bright colours this is beyond the end of the 64 KB cache, so the original reads whatever
    follows it in memory: the palettes and a few editor settings, and otherwise zeros.  The bytes
    that end up in the table depend on them, so the same layout is reconstructed here.
    """
    memory = bytearray(_MEMORY_AFTER_CACHE_SIZE)
    memory[:_CACHE_ENTRIES * 2] = cache.astype('<i2').tobytes()
    memory[_CACHE_BASE_TO_PALETTE_6BIT:_CACHE_BASE_TO_PALETTE_6BIT + 0x300] = palette_6bit
    memory[_CACHE_BASE_TO_PALETTE_8BIT:_CACHE_BASE_TO_PALETTE_8BIT + 0x300] = palette_8bit
    memory[_CACHE_BASE_TO_IMAGE_SIZE:_CACHE_BASE_TO_IMAGE_SIZE + 4] = struct.pack('<HH', width,
                                                                                  height)
    for offset, value in _EDITOR_STATE_AFTER_CACHE:
        struct.pack_into('<i', memory, offset, value)
    return memory


def _build_light_table(cache, palette_6bit, palette_8bit, width, height):
    """For each brightness level, map each palette colour to its darkened palette entry."""
    memory = _memory_after_cache(cache, palette_6bit, palette_8bit, width, height)
    table = bytearray()
    for level in range(_BRIGHTNESS_LEVELS):
        scale = (31 - level) * 2
        for index in range(256):
            red = (palette_6bit[index * 3] * scale) >> 6
            green = (palette_6bit[index * 3 + 1] * scale) >> 6
            blue = (palette_6bit[index * 3 + 2] * scale) >> 6
            table.append(memory[((red * 32 + green) * 32 + blue) * 2])
    return bytes(table)


def _cell_transparency(is_key, width, height):
    """Mark the cells holding transparent pixels; a cell entirely transparent ends the list."""
    columns = width // _CELL_SIZE
    rows = height // _CELL_SIZE
    index_is_key = is_key.reshape(height, width)
    flags = []
    for row in range(rows):
        for column in range(columns):
            if len(flags) == _MAX_CELLS:
                return flags    # the editor shows a message and uses the first 256 cells
            cell = index_is_key[row * _CELL_SIZE:(row + 1) * _CELL_SIZE,
                                column * _CELL_SIZE:(column + 1) * _CELL_SIZE]
            if cell.all():
                return flags
            flags.append(1 if cell.any() else 0)
    return flags


def load_project_texture(project, inputs, log):
    """Load the project's texture and store the results in the project.

    ``inputs`` (a :class:`~pyprj2tom.input_files.ProjectInputs`) decides which file is used.
    """
    path = inputs.texture_path(project.texture_path)
    width, height, rgb = _read_tga(path)
    palette_8bit, pixels, is_key = _build_palette(rgb)
    palette_6bit = bytes(value >> 2 for value in palette_8bit)
    cache, _ = _build_colour_cache(pixels, is_key, palette_8bit)
    _complete_colour_cache(cache, palette_6bit)
    transparency = _cell_transparency(is_key, width, height)
    if width != _IMAGE_WIDTH:
        rgb, height = _rearrange_cells(rgb, width, height)
    project.texture = ImportedTexture(
        _IMAGE_WIDTH, height, rgb, palette_6bit,
        _build_light_table(cache, palette_6bit, palette_8bit, _IMAGE_WIDTH, height), transparency)


def _rearrange_cells(rgb, width, height):
    """Move the 64 x 64 cells of an image of another width into rows of four cells."""
    columns = width // _CELL_SIZE
    cell_count = (height * width) // (_CELL_SIZE * _CELL_SIZE)
    target = bytearray((height + _CELL_SIZE) * width * 3)
    for cell in range(cell_count):
        source_row, source_column = cell // columns, cell % columns
        target_row, target_column = cell >> 2, cell & 3
        for line in range(_CELL_SIZE):
            source = ((source_row * _CELL_SIZE + line) * width + source_column * _CELL_SIZE) * 3
            destination = (target_row * _CELL_SIZE + line) * _IMAGE_WIDTH * 3 \
                + target_column * _CELL_SIZE * 3
            target[destination:destination + _CELL_SIZE * 3] = rgb[source:source + _CELL_SIZE * 3]
    return bytes(target), (cell_count + 3) // 4 * _CELL_SIZE
