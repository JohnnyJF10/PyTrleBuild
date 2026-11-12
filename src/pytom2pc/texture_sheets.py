"""Texture sheets: the tile lists and RGB pixels stored at the start of the input files.

The .TOM (room textures), .WAD (object textures) and .SWD (sprites) each begin with a list of
rectangular *tiles* followed by an image that is 256 pixels wide, stored as RGB triplets.  This
module reads those headers and works out how the three images share one stack of texture pages.

Addresses inside an image are *linear offsets*: ``page * 0x10000 + y * 0x100 + x``.
"""
import struct
from dataclasses import dataclass

import numpy as np

from trle_common.c_arithmetic import c_div
from pytom2pc.level_files import ConversionError, read_required_file

PAGE_WIDTH = 256
PIXELS_PER_PAGE = 0x10000
RGB_BYTES_PER_PAGE = PIXELS_PER_PAGE * 3

TILE_RECORD_SIZE = 8
SPRITE_RECORD_SIZE = 16

#: Magenta marks transparent pixels in all source images.
TRANSPARENT_RGB = (255, 0, 255)

#: The original keeps the per-tile attribute bytes in a table of this size.
ATTRIBUTE_TABLE_SIZE = 256


@dataclass(frozen=True)
class TextureTile:
    """A rectangle in a texture image.

    Width and height are stored in 8.8 fixed point as ``(pixels - 1) * 256 + 255``, the format
    texture coordinates use, so ``width_field`` can be added directly to a coordinate.
    """

    linear_offset: int
    width_field: int
    height_field: int

    @property
    def width(self):
        """Return the width in pixels."""
        return (self.width_field >> 8) + 1

    @property
    def height(self):
        """Return the height in pixels."""
        return (self.height_field >> 8) + 1


@dataclass(frozen=True)
class SpriteSource:
    """A sprite in the .SWD image plus its four placement values in world units."""

    linear_offset: int
    width_field: int
    height_field: int
    placement: tuple

    @property
    def width(self):
        """Return the width in pixels."""
        return (self.width_field >> 8) + 1

    @property
    def height(self):
        """Return the height in pixels."""
        return (self.height_field >> 8) + 1

    @property
    def area(self):
        """Return the area in pixels."""
        return self.width * self.height


@dataclass
class SourceHeaders:
    """Everything read from the headers of the .TOM, .WAD and .SWD files."""

    tom_version: int
    room_tiles: list
    room_attributes: bytes
    room_pixels: bytes
    tom_rest_offset: int
    object_tiles: list
    object_pixels: bytes
    wad_rest_offset: int
    sprites: list
    sprite_pixels: bytes
    swd_rest_offset: int


def _read_tiles(data, offset, count):
    """Read a table of tiles."""
    return [TextureTile(*struct.unpack_from('<iHH', data, offset + TILE_RECORD_SIZE * index))
            for index in range(count)]


def read_source_headers(files, log):
    """Read and cross-check the headers of the three texture-carrying files."""
    tom = read_required_file(files.path('tom'))
    position = 0
    version = struct.unpack_from('<I', tom, position)[0]
    position += 4
    if version & 0xFF180000 == 0:
        raise ConversionError('ERROR: This is not a RoomEdit 24 .TOM')

    tile_count = struct.unpack_from('<h', tom, position)[0]
    position += 2
    log(f'Room Tex Infos : {tile_count}')
    room_tiles = _read_tiles(tom, position, tile_count)
    position += TILE_RECORD_SIZE * tile_count
    room_attributes = tom[position:position + tile_count]
    position += tile_count
    pixel_bytes = struct.unpack_from('<I', tom, position)[0]
    position += 4
    room_pixels = tom[position:position + pixel_bytes]
    position += pixel_bytes
    position += 0x300  # the 256-colour palette is of no use to the PC converter
    tom_rest_offset = position

    wad = read_required_file(files.path('wad'))
    position = 0
    wad_version = struct.unpack_from('<I', wad, position)[0]
    position += 4
    if wad_version != version & 0xFFFF:
        raise ConversionError(f'ERROR: Object wad version {wad_version}, '
                              f'room wad version {version & 0xFFFF}')
    tile_count_objects = struct.unpack_from('<i', wad, position)[0]
    position += 4
    object_tiles = _read_tiles(wad, position, tile_count_objects)
    position += TILE_RECORD_SIZE * tile_count_objects
    pixel_bytes = struct.unpack_from('<I', wad, position)[0]
    position += 4
    object_pixels = wad[position:position + pixel_bytes]
    position += pixel_bytes
    wad_rest_offset = position
    room_attributes = _apply_attribute_table_overflow(room_attributes, wad_rest_offset, log)

    swd = read_required_file(files.path('swd'))
    position = 0
    swd_version = struct.unpack_from('<I', swd, position)[0]
    position += 4
    if swd_version != version & 0xFFFF:
        raise ConversionError(f'ERROR: Sprite wad version {swd_version}, '
                              f'room version {version & 0xFFFF}')
    sprite_count = struct.unpack_from('<i', swd, position)[0]
    position += 4
    sprites = []
    for index in range(sprite_count):
        offset, width, height, *placement = struct.unpack_from(
            '<iHH4h', swd, position + SPRITE_RECORD_SIZE * index)
        sprites.append(SpriteSource(offset, width, height, tuple(placement)))
    position += SPRITE_RECORD_SIZE * sprite_count
    pixel_bytes = struct.unpack_from('<I', swd, position)[0]
    position += 4
    sprite_pixels = swd[position:position + pixel_bytes]
    position += pixel_bytes

    return SourceHeaders(version, room_tiles, room_attributes, room_pixels, tom_rest_offset,
                         object_tiles, object_pixels, wad_rest_offset, sprites, sprite_pixels,
                         position)


def _apply_attribute_table_overflow(attributes, wad_rest_offset, log):
    """Reproduce what happens to tile attributes beyond the original's 256 byte table.

    Attributes 256 to 259 share memory with the variable that records where the .WAD texture
    section ends, so they are replaced by the four bytes of that value.  Levels with more than
    256 room tiles therefore get those four attributes from an unrelated number.
    """
    if len(attributes) <= ATTRIBUTE_TABLE_SIZE:
        return attributes
    log('WARNING: more than 256 room tiles overflow the attribute table of the original')
    patched = bytearray(attributes)
    overflow = struct.pack('<I', wad_rest_offset & 0xFFFFFFFF)
    for index, value in enumerate(overflow):
        if ATTRIBUTE_TABLE_SIZE + index < len(patched):
            patched[ATTRIBUTE_TABLE_SIZE + index] = value
    return bytes(patched)


@dataclass(frozen=True)
class PageLayout:
    """Where each source image starts in the combined page stack."""

    room_base: int
    object_base: int
    sprite_base: int


def compute_page_layout(room_tiles, object_tiles):
    """Stack the images: rooms first, then objects, then the first free page for sprites.

    Each following image starts on the page after the highest tile of the previous one.  The
    first object tile is not considered when looking for the highest object tile.
    """
    highest_room_tile = max((max(tile.linear_offset, 0) for tile in room_tiles), default=0)
    object_base = c_div(highest_room_tile, PIXELS_PER_PAGE) + 1
    highest_object_tile = max((max(tile.linear_offset, 0) for tile in object_tiles[1:]), default=0)
    sprite_base = c_div(highest_object_tile, PIXELS_PER_PAGE) + 1 + object_base
    return PageLayout(0, object_base, sprite_base)


def detect_transparent_tiles(tiles, pixels):
    """Return one byte per tile: 1 if the tile contains any magenta pixel, else 0."""
    image = np.frombuffer(pixels, np.uint8)
    flags = bytearray(len(tiles))
    for index, tile in enumerate(tiles):
        for row in range(tile.height):
            start = (tile.linear_offset + row * PAGE_WIDTH) * 3
            row_bytes = image[start:start + tile.width * 3]
            if row_bytes.size < 3:
                continue
            rgb = row_bytes[:(row_bytes.size // 3) * 3].reshape(-1, 3)
            red, green, blue = TRANSPARENT_RGB
            if np.any((rgb[:, 0] == red) & (rgb[:, 1] == green) & (rgb[:, 2] == blue)):
                flags[index] = 1
                break
    return bytes(flags)
