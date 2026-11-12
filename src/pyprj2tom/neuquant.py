"""NeuQuant colour quantisation, in the variant used by the level editor.

NeuQuant (Dekker, 1994) trains a one-dimensional self-organising map of colours.  The editor uses
integer arithmetic, so the rounding of each division matters; the exact results depend on it.
Network entries are kept as arrays, which allows a whole training step to be computed at once.
"""
import numpy as np

from trle_common.c_arithmetic import c_div

_NETWORK_SIZE = 256
_SAMPLE_FACTOR = 1
_INITIAL_ALPHA = 0x400
_RADIUS_SCALE = 1 << 6
_PRIMES = ((499, 0x5D9), (491, 0x5C1), (487, 0x5B5))
_FALLBACK_PRIME_STEP = 0x5E5


def _radius_powers(radius, alpha, table):
    """Fill the table of neighbour learning rates for the current radius."""
    for distance in range(radius):
        table[distance] = c_div((radius * radius - distance * distance) * 0x100,
                                 radius * radius) * alpha


def _shrink_radius(radius_fixed):
    """Reduce the radius by one thirtieth, with the rounding of the original code."""
    high_part = (radius_fixed * 0x77777777) >> 32
    difference = high_part - radius_fixed
    return radius_fixed + (difference >> 4) - (difference >> 31)


def learn_palette(pixels, network_size=_NETWORK_SIZE):
    """Train the network on ``pixels`` (flat RGB bytes) and return its colours as an Nx3 array."""
    data = np.frombuffer(pixels, dtype=np.uint8)
    length = data.size
    network = np.array([[(index << 12) // network_size] * 3 for index in range(network_size)],
                       dtype=np.int64)
    frequency = np.full(network_size, 0x10000 // network_size, dtype=np.int64)
    bias = np.zeros(network_size, dtype=np.int64)

    alpha_decrement = c_div(_SAMPLE_FACTOR - 1, 3) + 0x1E
    sample_pixels = c_div(length, _SAMPLE_FACTOR * 3)
    radius_fixed = (network_size >> 3) << 6
    alpha = _INITIAL_ALPHA
    radius = radius_fixed >> 6
    if radius < 2:
        radius = 0
    radius_power = [0] * 40
    for distance in range(radius):
        radius_power[distance] = c_div((radius * radius - distance * distance) * 0x100,
                                        radius * radius) << 10

    step = next((value for prime, value in _PRIMES if length % prime != 0), _FALLBACK_PRIME_STEP)
    delta = sample_pixels // 1000
    position = 0
    for iteration in range(1, sample_pixels + 1):
        colour = np.array([int(data[position]) << 4, int(data[position + 1]) << 4,
                           int(data[position + 2]) << 4], dtype=np.int64)
        distances = np.abs(network - colour).sum(axis=1)
        best = int(distances.argmin())
        best_biased = int((distances - (bias >> 12)).argmin())
        decay = frequency >> 10
        frequency -= decay
        bias += decay << 10
        frequency[best] += 0x40
        bias[best] -= 0x10000

        difference = (network[best_biased] - colour) * alpha
        network[best_biased] -= (difference + ((difference >> 31) & 0x3FF)) >> 10
        if radius:
            _move_neighbours(network, best_biased, radius, radius_power, colour)

        position += step
        if position >= length:
            position -= length
        if iteration % delta == 0:
            alpha -= c_div(alpha, alpha_decrement)
            radius_fixed = _shrink_radius(radius_fixed)
            radius = radius_fixed >> 6
            if radius < 2:
                radius = 0
            if radius:
                _radius_powers(radius, alpha, radius_power)
    return network >> 4


def _move_neighbours(network, centre, radius, radius_power, colour):
    """Pull the entries around ``centre`` towards ``colour``, weaker the further away they are."""
    upper = min(centre + radius, len(network))
    lower = max(centre - radius, -1)
    count_above = upper - (centre + 1)
    count_below = centre - 1 - lower
    steps = max(count_above, count_below)
    if steps <= 0:
        return
    rates = np.array(radius_power[1:steps + 1], dtype=np.int64)
    if count_above > 0:
        first = centre + 1
        change = (network[first:first + count_above] - colour) * rates[:count_above, None]
        network[first:first + count_above] -= (change + ((change >> 31) & 0x3FFFF)) >> 18
    if count_below > 0:
        segment = network[centre - count_below:centre][::-1]
        change = (segment - colour) * rates[:count_below, None]
        segment -= (change + ((change >> 31) & 0x3FFFF)) >> 18


def fill_colour_cube(network):
    """Replace entries 128 to 252 with an evenly spaced 5 x 5 x 5 colour cube."""
    index = 128
    for red in _cube_levels():
        for green in _cube_levels():
            for blue in _cube_levels():
                network[index] = (red, green, blue)
                index += 1


def _cube_levels():
    """Return the five levels per colour channel of the fixed colour cube."""
    return (min(level * 0x33, 0xFF) for level in range(5))


def swap_nearest_to_front(network, colours_6bit):
    """Bring the entries closest to some fixed colours to the front of the network.

    The colours are given with 6 bits per channel.  For each of them, the nearest network
    entry changes place with the next front position.
    """
    for front, colour in enumerate(colours_6bit):
        wanted = np.array([(value << 2) & 0xFF for value in colour], dtype=np.int64)
        distance = ((wanted - network) ** 2).sum(axis=1)
        nearest = int(distance.argmin())
        previous_front = network[front] & 0xFF     # only the low byte of each value is kept
        network[front] = network[nearest]
        network[nearest] = previous_front
