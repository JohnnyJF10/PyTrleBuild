"""Vector maths with the numeric behaviour of the editor.

The editor runs on the x87 FPU in 53-bit precision.  Intermediate results therefore have double
precision and are rounded to 32-bit floats only when stored.  That rounding is what makes the
planarity test below work, so it is reproduced with :func:`to_float32`.
"""
import math
import struct

from trle_common.c_arithmetic import to_int16, to_int32

NO_VERTEX = -32000

#: Degrees to the editor's 16-bit angle units, and degrees to radians, as the editor stores them.
DEGREES_TO_ANGLE = 182.0444444
DEGREES_TO_RADIANS = 0.017453292


def to_float32(value):
    """Round to a 32-bit float, as storing into a C ``float`` does."""
    return struct.unpack('<f', struct.pack('<f', value))[0]


def normalise(vector):
    """Return a unit vector, each component rounded to float.  A zero vector becomes +Z."""
    x, y, z = vector
    length_squared = x * x + y * y + z * z
    if length_squared == 0.0:
        return [0.0, 0.0, 1.0]
    factor = 1.0 / math.sqrt(length_squared)
    return [to_float32(factor * x), to_float32(factor * y), to_float32(factor * z)]


def dot(a, b):
    """Dot product, summed from the last component first, as the editor's code does."""
    return a[2] * b[2] + a[1] * b[1] + a[0] * b[0]


def _unit_normal_of_triangle(a, b, c):
    """Return the unit normal of the triangle a, b, c."""
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    return normalise([to_float32(vz * uy - vy * uz),
                      to_float32(vx * uz - vz * ux),
                      to_float32(vy * ux - vx * uy)])


def face_is_planar(a, b, c, d):
    """True if the four triangles of a quad have exactly the same (float) normal."""
    first = _unit_normal_of_triangle(a, b, c)
    return (first == _unit_normal_of_triangle(b, c, d)
            and first == _unit_normal_of_triangle(c, d, a)
            and first == _unit_normal_of_triangle(d, a, b))


def face_normal(a, b, c):
    """Return the unit normal of a triangle in double precision (corner ``b`` is the origin)."""
    ux, uy, uz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    vx, vy, vz = c[0] - b[0], c[1] - b[1], c[2] - b[2]
    scale = 1.0 / (math.sqrt(ux * ux + uy * uy + uz * uz) * math.sqrt(vx * vx + vy * vy + vz * vz))
    return ((vz * uy - vy * uz) * scale, (vx * uz - vz * ux) * scale, (vy * ux - vx * uy) * scale)


def sine(angle):
    """Sine of a 16-bit angle as a 14-bit fixed-point value (a 4096 step table in the editor)."""
    reduced = angle & 0x7FFF
    if reduced > 0x3FFF:
        reduced = 0x8000 - reduced
    value = round(16384 * math.sin((reduced >> 4) * math.pi / 2048))
    return -value if (angle & 0xFFFF) > 0x8000 else value


def cosine(angle):
    """Return the cosine of an angle in the 16-bit angle unit."""
    return sine(angle + 0x4000)


def rotate_matrix_x(matrix, angle):
    """Apply the X rotation used for light directions to a 3x4 integer matrix."""
    if angle == 0:
        return
    sin_value, cos_value = sine(angle), cosine(angle)
    for offset in (0, 4, 8):
        first, second = matrix[offset + 1], matrix[offset + 2]
        matrix[offset + 1] = (first * cos_value + second * sin_value) >> 14
        matrix[offset + 2] = (second * cos_value - first * sin_value) >> 14


def rotate_matrix_y(matrix, angle):
    """Apply the Y rotation used for light directions to a 3x4 integer matrix."""
    if angle == 0:
        return
    sin_value, cos_value = sine(angle), cosine(angle)
    for offset in (0, 4, 8):
        first, third = matrix[offset], matrix[offset + 2]
        matrix[offset] = (first * cos_value - third * sin_value) >> 14
        matrix[offset + 2] = (first * sin_value + third * cos_value) >> 14


def direction_from_angles(y_angle_degrees, x_angle_degrees):
    """Return the unit vector for a light or camera orientation given in degrees."""
    matrix = [0x4000, 0, 0, 0, 0, 0x4000, 0, 0, 0, 0, 0x4000, 0]
    rotate_matrix_y(matrix, to_int16(int(y_angle_degrees * DEGREES_TO_ANGLE)))
    rotate_matrix_x(matrix, to_int16(int(x_angle_degrees * DEGREES_TO_ANGLE)))
    return normalise([to_float32(float(to_int32(matrix[2] << 10))),
                      to_float32(float(to_int32(matrix[6] << 10))),
                      to_float32(float(to_int32(matrix[10] << 10)))])


