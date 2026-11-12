"""Build the combined RGB page stack and find a place for every sprite.

Room and object images are copied to their pages unchanged.  Sprites, however, are moved: each one
is placed into the first free space of the stack, largest sprites first.  Space counts as used
where a room or object tile lies.
"""
from dataclasses import dataclass

import numpy as np

from pytom2pc.rectangle_search import PAGE_SIZE, build_integral_image, find_first_free
from pytom2pc.texture_sheets import PIXELS_PER_PAGE, RGB_BYTES_PER_PAGE, PAGE_WIDTH

#: Size of the RGB buffer of the original tool (40 pages).
RGB_BUFFER_BYTES = 6 * 0x140000
#: Pages covered by the occupancy map, and pages tried when placing a sprite.
OCCUPANCY_PAGES = 20


@dataclass
class PlacedSprite:
    """A sprite's position in the page stack, in the form written to the level."""

    page: int
    position: int
    width_field: int
    height_field: int
    placement: tuple

    def as_words(self):
        """Return the record of the sprite as a list of words."""
        return [self.page, self.position, self.width_field, self.height_field, *self.placement]


class SpriteAtlas:
    """Holds the combined RGB image and the final position of every sprite."""

    def __init__(self, layout, headers, log):
        """Create the empty RGB pages and the map of the used pixels."""
        self.layout = layout
        self.headers = headers
        self.log = log
        self.rgb_pages = np.zeros(RGB_BUFFER_BYTES, np.uint8)
        self.occupancy = np.zeros(OCCUPANCY_PAGES * PIXELS_PER_PAGE, np.uint8)
        self.placed_sprites = [PlacedSprite(0, 0, 0, 0, (0, 0, 0, 0)) for _ in headers.sprites]
        self.sprite_pixels = np.frombuffer(headers.sprite_pixels, np.uint8)

    def build(self):
        """Fill the page stack and return the number of pages it uses."""
        self._copy_room_and_object_images()
        self._mark_tiles_as_used()
        page_count = self.layout.sprite_base
        remaining = [sprite.linear_offset for sprite in self.headers.sprites]
        for _ in self.headers.sprites:
            index = self._largest_unplaced_sprite(remaining)
            if index is not None:
                page_count = max(page_count, self._place_sprite(index))
                remaining[index] = -1
        self.log(f'Done sprite remap ({page_count})')
        return page_count

    def _copy_room_and_object_images(self):
        """Copy the room and object pages to their positions in the shared area."""
        room = np.frombuffer(self.headers.room_pixels, np.uint8)
        objects = np.frombuffer(self.headers.object_pixels, np.uint8)
        start = self.layout.room_base * RGB_BYTES_PER_PAGE
        self.rgb_pages[start:start + room.size] = room[:max(0, min(room.size,
                                                                   RGB_BUFFER_BYTES - start))]
        start = self.layout.object_base * RGB_BYTES_PER_PAGE
        count = max(0, min(objects.size, RGB_BUFFER_BYTES - start))
        self.rgb_pages[start:start + count] = objects[:count]

    def _mark_tiles_as_used(self):
        """Mark the area of every room and object tile as used, so that sprites avoid them."""
        for tile in self.headers.room_tiles:
            self._mark_used(self.layout.room_base, tile)
        for tile in self.headers.object_tiles[1:]:
            self._mark_used(self.layout.object_base, tile)

    def _mark_used(self, base_page, tile):
        """Mark a tile's pixels; a tile wider than its row spills into the next row."""
        start = base_page * PIXELS_PER_PAGE + tile.linear_offset
        for row in range(tile.height):
            row_start = start + row * PAGE_WIDTH
            if 0 <= row_start < self.occupancy.size:
                self.occupancy[row_start:row_start + tile.width] = 1

    def _largest_unplaced_sprite(self, remaining):
        """Return the index of the largest unplaced sprite; the first one wins a tie."""
        best_area, best_index = 0, None
        for index, sprite in enumerate(self.headers.sprites):
            if remaining[index] >= 0 and sprite.area > best_area:
                best_area, best_index = sprite.area, index
        return best_index

    def _place_sprite(self, index):
        """Find room for one sprite, copy its pixels, return the page count it needs."""
        sprite = self.headers.sprites[index]
        # The search excludes the very last row and column; a sprite as wide or as tall as a
        # whole page therefore never fits.
        row_count = PAGE_SIZE - sprite.height
        column_count = PAGE_SIZE - sprite.width
        for page in range(OCCUPANCY_PAGES):
            map_view = self.occupancy[page * PIXELS_PER_PAGE:(page + 1) * PIXELS_PER_PAGE]
            map_view = map_view.reshape(PAGE_SIZE, PAGE_SIZE)
            found = find_first_free(build_integral_image(map_view), sprite.width, sprite.height,
                                    row_count, column_count)
            if found is None:
                continue
            y, x = found
            self._copy_sprite_pixels(sprite, page, x, y)
            map_view[y:y + sprite.height, x:x + sprite.width] = 1
            self.placed_sprites[index] = PlacedSprite(page, ((y << 8) + x) & 0xFFFF,
                                                      sprite.width_field, sprite.height_field,
                                                      sprite.placement)
            return page + 1
        self.log(f"ERROR: Sprite{index} w{sprite.width} h{sprite.height} doesn't fit")
        return 0

    def _copy_sprite_pixels(self, sprite, page, x, y):
        """Copy a sprite's pixels to its place, row by row."""
        for row in range(sprite.height):
            target = ((page * PAGE_WIDTH + y + row) * PAGE_WIDTH + x) * 3
            source = (sprite.linear_offset + row * PAGE_WIDTH) * 3
            chunk = self.sprite_pixels[source:source + sprite.width * 3]
            self.rgb_pages[target:target + chunk.size] = chunk
