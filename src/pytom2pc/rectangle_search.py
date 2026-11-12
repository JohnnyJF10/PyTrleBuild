"""First-fit search for free rectangles on 256x256 pixel occupancy maps.

A page is free where its occupancy value is zero.  A free rectangle is found in reading order:
the smallest row, and within that row the smallest column.  An integral image answers "is this
rectangle empty?" in constant time, so a search does not have to test each pixel.
"""
import numpy as np

PAGE_SIZE = 256


def build_integral_image(occupancy):
    """Return the summed-area table (257x257) of a 256x256 occupancy map."""
    integral = np.zeros((PAGE_SIZE + 1, PAGE_SIZE + 1), np.int32)
    integral[1:, 1:] = (occupancy != 0).astype(np.int32).cumsum(0).cumsum(1)
    return integral


def find_first_free(integral, width, height, row_count, column_count):
    """Return ``(y, x)`` of the first empty ``width`` x ``height`` rectangle, or ``None``.

    The rectangle's top-left corner is tried for rows ``0..row_count-1`` and columns
    ``0..column_count-1``.
    """
    if row_count <= 0 or column_count <= 0:
        return None
    filled = (integral[height:height + row_count, width:width + column_count]
              - integral[0:row_count, width:width + column_count]
              - integral[height:height + row_count, 0:column_count]
              + integral[0:row_count, 0:column_count])
    empty = np.flatnonzero(filled.ravel() == 0)
    if empty.size == 0:
        return None
    first = int(empty[0])
    return first // column_count, first % column_count


class PagedBuffer:
    """A stack of 256x256 pages which only allocates pages that are actually used."""

    def __init__(self, dtype, page_limit):
        """Create an empty buffer; pages are allocated when first used."""
        self.dtype = dtype
        self.page_limit = page_limit
        self.pages = {}
        self._integral_images = {}

    def exists(self, page_number):
        """Tell whether a page has been allocated."""
        return page_number in self.pages

    def page(self, page_number):
        """Return a page, creating an empty one on first use."""
        if page_number >= self.page_limit:
            from pytom2pc.level_files import ConversionError
            raise ConversionError(f'page {page_number} exceeds the limit of {self.page_limit}')
        if page_number not in self.pages:
            self.pages[page_number] = np.zeros((PAGE_SIZE, PAGE_SIZE), self.dtype)
        return self.pages[page_number]

    def mark_changed(self, page_number):
        """Forget the cached integral image after a page has been written to."""
        self._integral_images.pop(page_number, None)

    def find_first_free(self, page_number, width, height, row_count, column_count):
        """First-fit search on one existing page (see :func:`find_first_free`)."""
        if page_number not in self._integral_images:
            self._integral_images[page_number] = build_integral_image(self.pages[page_number])
        return find_first_free(self._integral_images[page_number],
                               width, height, row_count, column_count)

    def flattened(self, page_count):
        """Return the first ``page_count`` pages as one array, missing pages being zero."""
        result = np.zeros(max(0, page_count) * PAGE_SIZE * PAGE_SIZE, self.dtype)
        for number, page in self.pages.items():
            if number < page_count:
                result[number * 0x10000:(number + 1) * 0x10000] = page.ravel()
        return result
