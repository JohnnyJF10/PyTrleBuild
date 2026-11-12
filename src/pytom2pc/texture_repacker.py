"""The PC-specific final pass: packing every used texture into new 256x256 pages.

So far texture pages are simply the images of the source files.  The PC version wants tiles sorted
into three groups, each with its own pages:

* **room** pages (textures with the room flag),
* **object** pages (object textures and all sprites),
* **bump** pages (room textures flagged for bump mapping), present twice: colour and height map.

Each tile is copied to the first free spot on its group's pages, largest tiles first, and every
texture and sprite record is then rewritten to point at the new place.  Tiles identical to one
that is already placed share its pixels.  Finally the pages are appended in 32-bit and in 16-bit
colour, followed by the rest of the level.
"""
import struct
from dataclasses import dataclass
from typing import Optional

import numpy as np

from trle_common.c_arithmetic import c_div, c_mod, to_int16, to_int32
from trle_common.msvc_sort import msvc_qsort
from pytom2pc.level_files import ConversionError
from pytom2pc.rectangle_search import PAGE_SIZE, PagedBuffer

_MAX_PAGES = 20
_ROOM, _OBJECT, _BUMP, _BUMP_HEIGHT = 'room', 'object', 'bump', 'bump height'
_TEXTURE_RECORD_SIZE = 0x26
_SPRITE_RECORD_SIZE = 16

_FLAG_BUMP_LEVEL_1 = 0x800
_FLAG_BUMP_LEVEL_2 = 0x400
_FLAG_ROOM_TEXTURE = 0x8000


@dataclass
class PackItem:
    """A tile or sprite waiting to be placed on a new page."""

    record_offset: int
    is_sprite: bool
    width: int
    height: int
    source_offset: int
    page: int = 0
    x: int = 0
    y: int = 0
    group: Optional[str] = None


class _HighestPositions:
    """How far each group has been filled, used to know the group's page count.

    Which counter a sprite updates depends on which texture was placed just before it: sprites
    never select a counter of their own.  Until the first texture has been placed the "current
    counter" is the first four bytes of the first texture record in the level, so a sprite
    placed before any texture modifies that record.
    """

    def __init__(self, level, first_record_offset):
        """Start with zero counters; no group is selected at first."""
        self.level = level
        self.counters = {_ROOM: 0, _BUMP: 0, _OBJECT: 0}
        self.selected = None
        self.first_record_offset = first_record_offset

    def select(self, group):
        """Choose the group whose counter is used from now on."""
        self.selected = group

    def get(self):
        """Return the selected counter; with no selection it is read from the first record."""
        if self.selected is None:
            return struct.unpack_from('<i', self.level, self.first_record_offset)[0]
        return self.counters[self.selected]

    def set(self, value):
        """Store the selected counter; with no selection it is written to the first record."""
        if self.selected is None:
            struct.pack_into('<i', self.level, self.first_record_offset, to_int32(value))
        else:
            self.counters[self.selected] = value


def _page_count_for(highest_position):
    """Turn a linear position into a page count.

    The original computes the remainder with 0xFFFF where 0x10000 is meant; the extra page this
    can produce is part of the format of real levels.
    """
    pages = c_div(highest_position, 0x10000)
    if c_mod(highest_position, 0xFFFF) != 0:
        pages += 1
    return pages


class TextureRepacker:
    """Runs the final texture pass over an uncompressed level."""

    def __init__(self, level, log):
        """Split the level into its pixel pages and the rest, and create the new pages."""
        self.level = bytearray(level)
        self.log = log
        self.page_count = struct.unpack_from('<I', self.level, 0)[0]
        self.end_of_pages = 4 + self.page_count * 0x40000
        self.source_pixels = np.frombuffer(bytes(self.level[4:self.end_of_pages]), '<u4')
        self.pixels = {group: PagedBuffer(np.uint32, _MAX_PAGES)
                       for group in (_ROOM, _OBJECT, _BUMP, _BUMP_HEIGHT)}
        self.occupancy = {group: PagedBuffer(np.uint8, _MAX_PAGES)
                          for group in (_ROOM, _BUMP, _OBJECT)}

    def run(self):
        """Return the level with the texture section replaced by the packed pages."""
        self.log(f'Found {self.page_count} Texture Pages')
        items, first_record = self._collect_items()
        self.log('Processing Textures')
        msvc_qsort(items, self._compare_area_descending)
        highest = _HighestPositions(self.level, first_record)
        for index, item in enumerate(items):
            if not item.is_sprite and self._reuse_identical_tile(items, index):
                continue
            self._place_item(item, highest)
        return self._assemble(items, highest)

    def _collect_items(self):
        """Create one item per sprite record and texture record (in this order)."""
        level = self.level
        sprite_table = level.find(b'SPR', self.end_of_pages) + 3
        sprite_count = struct.unpack_from('<i', level, sprite_table)[0]
        self.log(f'Found {sprite_count} Sprite Infos')
        texture_table = level.find(b'TEX', self.end_of_pages) + 3
        texture_count = struct.unpack_from('<i', level, texture_table)[0]
        self.log(f'Found {texture_count} Texture Infos')

        items = []
        for index in range(sprite_count):
            record = sprite_table + 4 + _SPRITE_RECORD_SIZE * index
            page, position, width_field, height_field = struct.unpack_from('<4H', level, record)
            items.append(PackItem(record, True, (width_field >> 8) + 1, (height_field >> 8) + 1,
                                  page * 0x10000 + position))
        for index in range(texture_count):
            record = texture_table + 4 + _TEXTURE_RECORD_SIZE * index
            tile_word = struct.unpack_from('<H', level, record + 2)[0]
            x, y, width, height = struct.unpack_from('<4i', level, record + 0x16)
            source = to_int32(((tile_word & 0x7FFF) * 0x100 + y) * 0x100 + x)
            items.append(PackItem(record, False, width + 1, height + 1, source))
        return items, texture_table + 4

    @staticmethod
    def _compare_area_descending(first, second):
        """Order items from the largest area to the smallest."""
        first_area = first.width * first.height
        second_area = second.width * second.height
        if first_area > second_area:
            return -1
        return 1 if first_area < second_area else 0

    def _source_row(self, start, length):
        """Return ``length`` source pixels from ``start``; pixels outside the image are zero."""
        row = np.zeros(length, np.uint32)
        low, high = max(start, 0), min(start + length, self.source_pixels.size)
        if high > low:
            row[low - start:high - start] = self.source_pixels[low:high]
        return row

    def _reuse_identical_tile(self, items, index):
        """Reuse an already placed tile whose top-left corner matches this tile's pixels."""
        item = items[index]
        for earlier in items[:index]:
            if item.width > earlier.width or item.height > earlier.height:
                continue
            if earlier.height * earlier.width <= 0 or earlier.group is None:
                continue
            page = self.pixels[earlier.group].page(earlier.page)
            if all(np.array_equal(page[earlier.y + row, earlier.x:earlier.x + item.width],
                                  self._source_row(item.source_offset + row * PAGE_SIZE,
                                                   item.width))
                   for row in range(item.height)):
                item.x, item.y, item.page, item.group = (earlier.x, earlier.y, earlier.page,
                                                         earlier.group)
                return True
        return False

    def _texture_group(self, item):
        """Return the group of a texture record and the offset used for its bump image."""
        flags = struct.unpack_from('<H', self.level, item.record_offset + 4)[0]
        if flags & _FLAG_BUMP_LEVEL_1:
            return _BUMP, 1
        if flags & _FLAG_BUMP_LEVEL_2:
            return _BUMP, 2
        if flags & _FLAG_ROOM_TEXTURE:
            return _ROOM, 0
        return _OBJECT, 0

    def _place_item(self, item, highest):
        """Find space for an item, copy its pixels there and raise the group's high mark."""
        bump_offset = 0
        if item.is_sprite:
            group = _OBJECT                     # sprites keep whichever counter is selected
        else:
            group, bump_offset = self._texture_group(item)
            highest.select(group)

        position = self._find_space(self.occupancy[group], item)
        if position == -1:
            raise ConversionError('Error : Locating Free Texture Space')
        page, y, x = position >> 16, (position >> 8) & 0xFF, position & 0xFF
        occupancy = self.occupancy[group]
        occupancy.page(page)[y:y + item.height, x:x + item.width] = 1
        occupancy.mark_changed(page)
        if highest.get() < position:
            highest.set(item.height * PAGE_SIZE + position)

        item.group = group
        target = self.pixels[group].page(page)
        for row in range(item.height):
            target[y + row, x:x + item.width] = self._source_row(
                item.source_offset + row * PAGE_SIZE, item.width)
        if group == _BUMP:
            self._write_bump_height_image(item, page, x, y, bump_offset)

    def _find_space(self, occupancy, item):
        """First fit on each page in turn.  A page not used yet always fits at (0, 0)."""
        for page in range(occupancy.page_limit):
            if occupancy.exists(page):
                found = occupancy.find_first_free(page, item.width, item.height,
                                                  PAGE_SIZE - item.height + 1,
                                                  PAGE_SIZE - item.width + 1)
                if found is None:
                    continue
                y, x = found
            else:
                y, x = 0, 0
            item.page, item.x, item.y = page, x, y
            return (page * PAGE_SIZE + y) * PAGE_SIZE + x
        return -1

    def _write_bump_height_image(self, item, page, x, y, offset):
        """Derive the second bump image: each pixel averaged with its inverted neighbour."""
        target = self.pixels[_BUMP_HEIGHT].page(page)
        columns = np.arange(item.width)
        shifted_columns = np.where(columns + offset < item.width, columns + offset, columns)
        for row in range(item.height):
            shifted_row = row + offset if row + offset < item.height else row
            shifted = self._source_row(item.source_offset + shifted_row * PAGE_SIZE,
                                       item.width)[shifted_columns]
            original = self._source_row(item.source_offset + row * PAGE_SIZE, item.width)

            def mix_channel(shift):
                """Average one colour channel of the original with the inverted, shifted image."""
                inverted = (0xFF - ((shifted >> shift) & 0xFF)) >> 1
                return (inverted + (((original >> shift) & 0xFF) >> 1)) & 0xFF

            target[y + row, x:x + item.width] = ((shifted & 0xFF000000) | (mix_channel(16) << 16)
                                                 | (mix_channel(8) << 8) | mix_channel(0))

    def _assemble(self, items, highest):
        """Update the records and build the final data: 32-bit pages, 16-bit pages and the rest."""
        room_pages = to_int16(_page_count_for(highest.counters[_ROOM]))
        bump_pages = to_int16(_page_count_for(highest.counters[_BUMP]))
        object_pages = to_int16(_page_count_for(highest.counters[_OBJECT]))
        self.log(f'RTPages {room_pages} BMTPages {bump_pages} OTPages {object_pages}')
        for item in items:
            self._rewrite_record(item, room_pages, object_pages)

        output = bytearray(struct.pack('<hhh', room_pages, object_pages, to_int16(bump_pages * 2)))
        sections = [self.pixels[_ROOM].flattened(room_pages),
                    self.pixels[_OBJECT].flattened(object_pages),
                    self.pixels[_BUMP].flattened(bump_pages),
                    self.pixels[_BUMP_HEIGHT].flattened(bump_pages)]
        for section in sections:
            output += section.astype('<u4').tobytes()
        for section in sections:
            output += self._to_argb1555(section)
        output += self.level[self.end_of_pages:]
        return bytes(output)

    @staticmethod
    def _to_argb1555(pixels):
        """Convert 32-bit ARGB pixels to 16-bit ARGB 1555."""
        high_colour = (((pixels >> 16) & 0xF8) << 7) | (((pixels >> 8) & 0xF8) << 2) \
            | ((pixels & 0xFF) >> 3)
        high_colour = high_colour | np.where((pixels >> 24) != 0, 0x8000, 0).astype(np.uint32)
        return high_colour.astype('<u2').tobytes()

    def _rewrite_record(self, item, room_pages, object_pages):
        """Point a texture or sprite record at the item's new page and position."""
        level = self.level
        record = item.record_offset
        page_shift = 0
        if item.group == _OBJECT:
            page_shift = room_pages
        elif item.group == _BUMP:
            page_shift = object_pages + room_pages

        if item.is_sprite:
            struct.pack_into('<H', level, record + 8, item.x & 0xFFFF)
            struct.pack_into('<H', level, record + 12, (item.x + item.width) & 0xFFFF)
            struct.pack_into('<H', level, record + 10, item.y & 0xFFFF)
            struct.pack_into('<H', level, record + 14, (item.y + item.height) & 0xFFFF)
            item.page += page_shift
            struct.pack_into('<H', level, record, item.page & 0xFFFF)
            return

        corners = list(struct.unpack_from('<8H', level, record + 6))
        old_x = to_int16(struct.unpack_from('<H', level, record + 0x16)[0])
        old_y = to_int16(struct.unpack_from('<H', level, record + 0x1A)[0])
        for index in (0, 2, 4, 6):
            corners[index] = (to_int16(item.x) * 0x100
                              + ((corners[index] - old_x * 0x100) & 0xFFFF)) & 0xFFFF
        for index in (1, 3, 5, 7):
            corners[index] = (to_int16(item.y) * 0x100
                              + ((corners[index] - old_y * 0x100) & 0xFFFF)) & 0xFFFF
        struct.pack_into('<8H', level, record + 6, *corners)
        item.page += page_shift
        tile_word = struct.unpack_from('<H', level, record + 2)[0]
        struct.pack_into('<H', level, record + 2, (item.page & 0xFFFF) | (tile_word & 0x8000))
