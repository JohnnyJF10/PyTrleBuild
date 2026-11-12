"""A small helper for reading little-endian values from a byte string."""
import struct


class BinaryReader:
    """Reads values one after the other from a byte string."""

    def __init__(self, data, position=0):
        """Start reading ``data`` at ``position``."""
        self.data = data
        self.position = position

    def read(self, format_string):
        """Read values described by a ``struct`` format (little-endian is implied)."""
        values = struct.unpack_from('<' + format_string, self.data, self.position)
        self.position += struct.calcsize('<' + format_string)
        return values

    def i16(self):
        """Read a signed 16-bit integer."""
        return self.read('h')[0]

    def u16(self):
        """Read an unsigned 16-bit integer."""
        return self.read('H')[0]

    def i32(self):
        """Read a signed 32-bit integer."""
        return self.read('i')[0]

    def u32(self):
        """Read an unsigned 32-bit integer."""
        return self.read('I')[0]

    def f32(self):
        """Read a 32-bit float."""
        return self.read('f')[0]

    def u8(self):
        """Read an unsigned byte."""
        return self.read('B')[0]

    def i8(self):
        """Read a signed byte."""
        return self.read('b')[0]

    def take(self, count):
        """Return the next ``count`` bytes."""
        chunk = self.data[self.position:self.position + count]
        self.position += count
        return chunk

    def skip(self, count):
        """Move ahead ``count`` bytes without reading them."""
        self.position += count

    def space_terminated_text(self, limit=80):
        """Read a text terminated by a space, as path names are stored in project files."""
        result = bytearray()
        while len(result) < limit - 1:
            value = self.take(1)
            if not value or value == b' ':
                break
            result += value
        return result.decode('latin-1').rstrip('\0')
