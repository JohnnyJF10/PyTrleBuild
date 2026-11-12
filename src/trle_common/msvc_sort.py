"""The ``qsort`` of the Microsoft Visual C++ 6 runtime library.

Both original tools sort records with this routine.  Quicksort is not stable: records that compare
equal end up in an order that depends on the exact algorithm, and that order is visible in the
output files.  Python's ``sorted`` keeps equal records in input order, so it cannot be used here.
"""

#: Partitions of at most this many elements are finished by a selection sort.
_SELECTION_SORT_LIMIT = 8


def msvc_qsort(items, compare):
    """Sort the list ``items`` in place, exactly as MSVC 6 ``qsort`` would.

    ``compare(a, b)`` returns a negative, zero or positive number, like a C comparator.
    """
    if len(items) < 2:
        return

    def swap(first, second):
        """Exchange two items."""
        if first != second:
            items[first], items[second] = items[second], items[first]

    def selection_sort(low, high):
        """Repeatedly move the largest element of [low, high] to the end of the range."""
        while high > low:
            largest = low
            for position in range(low + 1, high + 1):
                if compare(items[position], items[largest]) > 0:
                    largest = position
            swap(largest, high)
            high -= 1

    pending = []
    low, high = 0, len(items) - 1
    while True:
        size = high - low + 1
        if size <= _SELECTION_SORT_LIMIT:
            selection_sort(low, high)
        else:
            # The middle element becomes the pivot and is parked at the start of the range.
            swap(low + size // 2, low)
            left, right = low, high + 1
            while True:
                left += 1
                while left <= high and compare(items[left], items[low]) <= 0:
                    left += 1
                right -= 1
                while right > low and compare(items[right], items[low]) >= 0:
                    right -= 1
                if right < left:
                    break
                swap(left, right)
            swap(low, right)

            # Continue with the smaller partition and remember the larger one for later.
            if right - low > high - left:
                if low + 1 < right:
                    pending.append((low, right - 1))
                if left < high:
                    low = left
                    continue
            else:
                if left < high:
                    pending.append((left, high))
                if low + 1 < right:
                    high = right - 1
                    continue
        if not pending:
            return
        low, high = pending.pop()
