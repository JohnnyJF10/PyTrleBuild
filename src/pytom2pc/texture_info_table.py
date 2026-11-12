"""The level's list of texture records ("texture infos") and their de-duplication.

Every polygon in a TR4 level refers to a texture record holding a page number, four corner
coordinates and blending flags.  The converter creates those records on demand while it walks over
room and object polygons.  Requests that match an existing record reuse it.

Texture words in the source data:

* objects: a tile index; a negative number means "mirrored".  Triangles keep the index in the low
  13 bits and an orientation in the high 3 bits.
* rooms: bits 0-9 tile index, bit 10 "double sided", bit 11 "additive blending",
  bits 13-15 triangle orientation, bit 15 of a quad means "mirrored".
"""
import struct
from dataclasses import dataclass

from trle_common.c_arithmetic import c_div, to_int16

#: Bytes per texture record in the level.
RECORD_SIZE = 0x26

_ROOM_TILE_MASK = 0x3FF
_ROOM_DOUBLE_SIDED = 0x400
_ROOM_ADDITIVE = 0x800
_MIRRORED = 0x8000
_OBJECT_TRIANGLE_TILE_MASK = 0x1FFF

#: Blending attribute of a record.  0 and 1 come from the image: opaque or with transparent pixels.
_ATTRIBUTE_ADDITIVE = 2

#: Top bit of the record's "tile" word: this record describes a triangle.
_TILE_IS_TRIANGLE = 0x8000

_FLAG_ROOM_TEXTURE = 0x8000
_FLAG_BUMP_LEVEL_1 = 0x0800
_FLAG_BUMP_LEVEL_2 = 0x0400


# For each triangle orientation: which tile corner (top-left, top-right, bottom-left,
# bottom-right) becomes the triangle's first, second and third vertex.
_TRIANGLE_CORNERS = {
    0: ('TL', 'TR', 'BL'), 1: ('TR', 'BR', 'TL'), 2: ('BR', 'BL', 'TR'), 3: ('BL', 'TL', 'BR'),
    4: ('TR', 'TL', 'BR'), 5: ('TL', 'BL', 'TR'), 6: ('BL', 'BR', 'TL'), 7: ('BR', 'TR', 'BL'),
}


@dataclass
class TextureSource:
    """A tile list and the facts needed to turn its tiles into texture records."""

    tiles: list
    base_page: int
    transparency: bytes


@dataclass
class TextureRecord:
    """One texture record, as written to the level."""

    attribute: int
    tile: int
    flags: int
    corners: list
    x: int
    y: int
    width: int
    height: int

    def pack(self):
        """Return the record as stored in the level."""
        words = [self.attribute, self.tile, self.flags, *self.corners]
        return struct.pack('<11H4i', *[word & 0xFFFF for word in words],
                           self.x, self.y, self.width, self.height)


class TextureInfoTable:
    """Collects texture records and finds existing matches for new requests."""

    def __init__(self, room_attributes):
        """Start with an empty table and empty lookup tables for removing duplicates."""
        self.records = []
        self.room_attributes = room_attributes
        self.transparent_count = 0
        self._quad_lookup = {}
        self._triangle_lookup = {}

    def _store(self, record):
        """Append a record and register it for later lookups (the first match is kept)."""
        index = len(self.records)
        self.records.append(record)
        self._quad_lookup.setdefault((record.tile, record.attribute, *record.corners[:8]), index)
        # Triangle lookups also find quad records, because they only compare three corners.
        self._triangle_lookup.setdefault(
            (record.tile & 0x7FFF, record.attribute, *record.corners[:6]), index)
        if record.attribute != 0:
            self.transparent_count += 1
        return index

    def _flags_for(self, base_flags, tile_index, is_room):
        """Combine orientation bits with the room-only information from the attribute table."""
        if not is_room:
            return base_flags & 0xFFFF
        flags = base_flags | _FLAG_ROOM_TEXTURE
        attribute = 0
        if 0 <= tile_index < len(self.room_attributes):
            attribute = self.room_attributes[tile_index]
        if attribute == 1:
            flags = base_flags | _FLAG_ROOM_TEXTURE | _FLAG_BUMP_LEVEL_1
        if attribute == 2:
            flags |= _FLAG_BUMP_LEVEL_2
        return flags & 0xFFFF

    def add_quad(self, texture_word, source, is_room, tiles=None):
        """Return the record index for a quad texture word, creating the record if needed.

        ``tiles`` can replace ``source.tiles``; animated textures use it to supply a one-entry
        table with a moved tile.
        """
        tiles = source.tiles if tiles is None else tiles
        texture_word &= 0xFFFF
        attribute = _ATTRIBUTE_ADDITIVE
        if is_room:
            mirrored = 1 if texture_word & _MIRRORED else 0
            tile_index = texture_word & _ROOM_TILE_MASK
            if not texture_word & _ROOM_ADDITIVE:
                attribute = source.transparency[tile_index]
        else:
            mirrored = 0
            if texture_word & _MIRRORED:
                mirrored = 1
                texture_word = -to_int16(texture_word) & 0xFFFF
            tile_index = to_int16(texture_word)
            attribute = source.transparency[tile_index]

        tile = tiles[tile_index]
        page = c_div(tile.linear_offset, 0x10000)
        inside_page = tile.linear_offset - page * 0x10000
        row = inside_page >> 8
        column = inside_page - row * 256
        left = column * 256
        top = (row * 256) & 0xFFFF
        target_page = page + source.base_page

        if mirrored:
            first_u, second_u = left + tile.width_field, left
        else:
            first_u, second_u = left, left + tile.width_field
        first_u &= 0xFFFF
        second_u &= 0xFFFF
        bottom = (top + tile.height_field) & 0xFFFF

        corners = [first_u, top, second_u, top, second_u, bottom, first_u, bottom]
        index = self._quad_lookup.get((target_page, attribute, *corners))
        if index is None:
            flags = self._flags_for(mirrored, tile_index, is_room)
            index = self._store(TextureRecord(attribute, target_page & 0xFFFF, flags, corners,
                                              left >> 8, (row * 256) >> 8,
                                              tile.width_field >> 8, tile.height_field >> 8))
        if is_room and texture_word & _ROOM_DOUBLE_SIDED:
            index |= 0x8000
        return index & 0xFFFF

    def add_triangle(self, texture_word, source, is_room, tiles=None):
        """Return the record index for a triangle texture word, creating the record if needed."""
        tiles = source.tiles if tiles is None else tiles
        texture_word &= 0xFFFF
        orientation = (texture_word >> 13) & 7
        attribute = _ATTRIBUTE_ADDITIVE
        if is_room:
            tile_index = texture_word & _ROOM_TILE_MASK
            if not texture_word & _ROOM_ADDITIVE:
                attribute = source.transparency[tile_index]
        else:
            tile_index = texture_word & _OBJECT_TRIANGLE_TILE_MASK
            attribute = source.transparency[tile_index]

        tile = tiles[tile_index]
        page = c_div(tile.linear_offset, 0x10000)
        inside_page = tile.linear_offset - page * 0x10000
        row = c_div(inside_page, 256)
        left = (inside_page - row * 256) * 256
        top = row * 256
        right = left + tile.width_field
        bottom = top + tile.height_field
        tile_corners = {'TL': (left, top), 'TR': (right, top),
                        'BL': (left, bottom), 'BR': (right, bottom)}
        corners = [coordinate & 0xFFFF
                   for name in _TRIANGLE_CORNERS[orientation]
                   for coordinate in tile_corners[name]]

        target_page = page + source.base_page
        index = self._triangle_lookup.get((target_page & 0xFFFF, attribute, *corners))
        if index is None:
            flags = self._flags_for(orientation, tile_index, is_room)
            index = self._store(TextureRecord(attribute, (target_page | _TILE_IS_TRIANGLE) & 0xFFFF,
                                              flags, [*corners, 0, 0], left >> 8, top >> 8,
                                              tile.width_field >> 8, tile.height_field >> 8))
        if is_room and texture_word & _ROOM_DOUBLE_SIDED:
            index |= 0x8000
        return index & 0xFFFF

    def pack_all(self):
        """Return the record count and all records as the level expects them."""
        return struct.pack('<i', len(self.records)) + b''.join(r.pack() for r in self.records)
