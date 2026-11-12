"""Animated texture ranges.

An animated range is a run of consecutive room tiles that the game cycles through.  In TR4 each
*cell* of a 256x256 page animates independently, so the converter finds out which texture regions
are really used by polygons, and only those become animation groups in the level.

The grouping key is the 64-pixel grid cell (0-15) which a tile lies in.
"""
import struct
from dataclasses import dataclass

from trle_common.c_arithmetic import c_div, c_mod, to_int32
from pytom2pc.texture_sheets import TextureTile

KIND_TRIANGLE = 1
KIND_QUAD = 2

_MAX_GROUPS_PER_RANGE = 0x20
_TILE_CELL_SIZE = 64


@dataclass
class AnimationUse:
    """One distinct way a polygon uses an animated region."""

    texture_flags: int
    kind: int
    x_in_cell: int
    y_in_cell: int
    width_field: int
    height_field: int


def _grid_cell(linear_offset):
    """Return the number of the 64-pixel grid cell (0-15) containing a tile."""
    column = c_mod(linear_offset, 256)
    row = c_div(linear_offset, 256)
    return (((column & 0xFFFF) >> 6) + (((row & 0xFFFF) >> 6) * 4)) & 0xFFFF


class AnimatedTextures:
    """Remembers how polygons use animated tiles and later writes the animation table."""

    def __init__(self, ranges, room_tiles, log):
        """Keep the ranges and tiles; ``uses`` lists the distinct uses per range."""
        self.ranges = ranges
        self.room_tiles = room_tiles
        self.log = log
        self.uses = [[] for _ in range(16)]

    def register_use(self, texture_word, kind):
        """Record that a polygon uses this texture, if it lies within an animated range."""
        texture_word &= 0xFFFF
        tile = self.room_tiles[texture_word & 0x3FF]
        cell = _grid_cell(tile.linear_offset)
        for range_index, (first, last) in enumerate(self.ranges):
            if not any(_grid_cell(self.room_tiles[index].linear_offset) == cell
                       for index in range(first, last + 1)):
                continue
            self._register_in_range(self.uses[range_index], texture_word, kind, tile)
            return

    def _register_in_range(self, uses, texture_word, kind, tile):
        """Add a new way of using a range, unless the same shape and mapping is already listed."""
        x_in_cell = c_mod(tile.linear_offset, 256) & (_TILE_CELL_SIZE - 1)
        y_in_cell = c_div(tile.linear_offset, 256) & (_TILE_CELL_SIZE - 1)
        for use in uses:
            same_shape = ((use.kind ^ kind) & 4) == 0 and use.x_in_cell == x_in_cell and \
                use.y_in_cell == y_in_cell << 8 and use.width_field == tile.width_field and \
                use.height_field == tile.height_field
            if same_shape and self._is_duplicate(use, texture_word, kind):
                return
        uses.append(AnimationUse(texture_word & 0xFC00, kind, x_in_cell,
                                 (y_in_cell << 8) & 0xFFFF, tile.width_field, tile.height_field))
        if len(uses) > _MAX_GROUPS_PER_RANGE:
            self.log('anim range overflow')

    @staticmethod
    def _is_duplicate(use, texture_word, kind):
        """Decide whether a new use is covered by an existing entry.

        Identical flags and kind always are.  A triangle in default orientation also counts as
        the same as a quad that is not mirrored, and vice versa.
        """
        if ((texture_word & 0xFC00 & 0xFBFF) ^ (use.texture_flags & 0xFBFF)) == 0 \
                and use.kind == kind:
            return True
        if (kind & KIND_TRIANGLE) and (use.kind & KIND_QUAD):
            if ((texture_word >> 13) & 7) == 0 and not use.texture_flags & 0x8000:
                return True
        if (kind & KIND_QUAD) and (use.kind & KIND_TRIANGLE):
            if not texture_word & 0x8000 and (use.texture_flags & 0xE000) == 0:
                return True
        return False

    def write(self, output, texture_table, room_textures):
        """Append the animation table, creating one texture record per frame and use."""
        word_count = 1
        group_count = 0
        for range_index, (first, last) in enumerate(self.ranges):
            count = len(self.uses[range_index])
            if count:
                word_count += ((last - first) + 2) * count
                group_count += count
        output += struct.pack('<iH', word_count, group_count & 0xFFFF)

        for range_index, (first, last) in enumerate(self.ranges):
            for use in self.uses[range_index]:
                output += struct.pack('<H', (last - first) & 0xFFFF)
                for tile_index in range(first, last + 1):
                    moved_tile = self._frame_tile(tile_index, use)
                    if use.kind & KIND_TRIANGLE:
                        record = texture_table.add_triangle(
                            use.texture_flags, room_textures, True, tiles=[moved_tile])
                    else:
                        record = texture_table.add_quad(
                            use.texture_flags, room_textures, True, tiles=[moved_tile])
                    output += struct.pack('<H', record & 0x7FFF)

        # The game wants the first range's group count again as the "UV ranges" byte.
        uv_ranges = len(self.uses[0]) & 0xFF
        output += bytes([uv_ranges])
        self.log(f'nAnimRanges = {len(self.ranges)}, nRanges = {group_count & 0xFFFF} '
                 f'nUVRanges {uv_ranges}')

    def _frame_tile(self, tile_index, use):
        """Return a tile describing this use's sub-rectangle in one animation frame."""
        offset = to_int32(self.room_tiles[tile_index].linear_offset + use.y_in_cell + use.x_in_cell)
        return TextureTile(offset, use.width_field, use.height_field)
