"""Integer arithmetic with the semantics of 32-bit C code.

The level formats are defined by C programs.  Python integers never overflow and Python division
rounds towards negative infinity, while C wraps at the width of the type and truncates towards
zero.  Everywhere the difference could change a result, the converters use these helpers.
"""


def to_int8(value):
    """Wrap ``value`` to a signed 8-bit integer."""
    value &= 0xFF
    return value - 0x100 if value & 0x80 else value


def to_int16(value):
    """Wrap ``value`` to a signed 16-bit integer."""
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def to_int32(value):
    """Wrap ``value`` to a signed 32-bit integer."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def c_div(dividend, divisor):
    """Integer division truncating towards zero, like ``/`` in C."""
    quotient = abs(dividend) // abs(divisor)
    return quotient if (dividend >= 0) == (divisor >= 0) else -quotient


def c_mod(dividend, divisor):
    """Remainder taking the sign of the dividend, like ``%`` in C."""
    return dividend - c_div(dividend, divisor) * divisor
