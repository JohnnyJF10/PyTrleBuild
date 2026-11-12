"""A first pass over the rooms of the .TOM, collecting what water effects need to know.

Vertex effects depend on neighbouring rooms, so all rooms must be known before the first one is
converted.  This module parses the whole room list once and keeps the geometry facts it needs,
and it also finds the list of animated texture ranges further down in the file.
"""
import struct
from dataclasses import dataclass, field

_DOOR_SIZE = 32
_LIGHT_SIZE = 0x2E
_STATIC_MESH_SIZE = 0x14

#: Maximum number of animated ranges the original tool can store.
MAX_ANIMATED_RANGES = 16

# Polygon kinds of the .TOM: their sizes in bytes and how many vertex numbers count as geometry.
_FACE_SHAPES = {0x20: (14, 3), 0x24: (14, 3), 0x21: (16, 4), 0x25: (16, 4),
                0x22: (14, 0), 0x23: (16, 0), 0x27: (6, 0)}


@dataclass
class RoomOverview:
    """The parts of a room that are needed while its neighbours are converted."""

    x: int
    z: int
    y_bottom: int
    flags: int
    vertices: list
    polygon_count: int
    polygon_bytes: bytes
    doors: list
    water_strength: int
    reflection_level: int
    _used_vertices: set = field(default=None, repr=False)

    def used_vertex_positions(self):
        """Return the world position of each vertex used by a textured wall or floor."""
        if self._used_vertices is None:
            self._used_vertices = self._collect_used_vertices()
        return self._used_vertices

    def _collect_used_vertices(self):
        """Return the world positions of the vertices that polygons actually use."""
        positions = set()
        offset = 0
        for _ in range(self.polygon_count):
            if offset + 2 > len(self.polygon_bytes):
                break
            kind = struct.unpack_from('<H', self.polygon_bytes, offset)[0]
            size, corner_count = _FACE_SHAPES.get(kind, (2, 0))
            corners = struct.unpack_from(f'<{corner_count}H', self.polygon_bytes, offset + 2)
            offset += size
            for vertex_index in corners:
                if vertex_index < len(self.vertices):
                    x, y, z = self.vertices[vertex_index]
                    positions.add((x + self.x, y, z + self.z))
        return positions


@dataclass
class TomOverview:
    """All rooms plus the animated texture ranges."""

    rooms: list
    animated_ranges: list


def read_tom_overview(tom_data):
    """Walk through the complete .TOM and return its :class:`TomOverview`."""
    offset = 4
    tile_count = struct.unpack_from('<H', tom_data, offset)[0]
    offset += 2 + tile_count * 8 + tile_count
    image_bytes = struct.unpack_from('<i', tom_data, offset)[0]
    offset += 4 + image_bytes + 0x300

    room_count = struct.unpack_from('<H', tom_data, offset)[0]
    offset += 2
    rooms = []
    for _ in range(room_count):
        room, offset = _read_room(tom_data, offset)
        rooms.append(room)

    offset = _skip_to_animated_ranges(tom_data, offset)
    range_count = struct.unpack_from('<I', tom_data, offset)[0]
    offset += 4
    ranges = [struct.unpack_from('<ii', tom_data, offset + 8 * index)
              for index in range(range_count)]
    if range_count > MAX_ANIMATED_RANGES:
        from pytom2pc.level_files import ConversionError
        raise ConversionError(f'{range_count} animation ranges: the original tom2pc overflows '
                              'its 16 entry table here (memory corruption)')
    return TomOverview(rooms, ranges)


def _read_room(data, offset):
    """Read the parts of a room needed for the overview; return it with the offset of the next room.
    """
    x, z, y_bottom, _y_top = struct.unpack_from('<4i', data, offset)
    flags = struct.unpack_from('<H', data, offset + 0x14)[0]
    mesh_words = struct.unpack_from('<h', data, offset + 0x16)[0]
    vertex_count = struct.unpack_from('<h', data, offset + 0x1A)[0]
    offset += 0x1C

    vertices = []
    if vertex_count >= 1:
        vertices = [struct.unpack_from('<3h', data, offset + 12 * index)
                    for index in range(vertex_count)]
        offset += 12 * vertex_count

    polygon_count = struct.unpack_from('<H', data, offset)[0]
    offset += 2
    polygon_bytes = b''
    if polygon_count:
        # All mesh words, minus the vertex list and the two counters.
        polygon_size = (mesh_words * 2 - vertex_count * 12 - 4) & 0xFFFF
        polygon_bytes = data[offset:offset + polygon_size]
        offset += polygon_size

    door_count = struct.unpack_from('<H', data, offset)[0]
    offset += 2
    doors = [struct.unpack_from('<16h', data, offset + _DOOR_SIZE * index)
             for index in range(door_count)]
    offset += _DOOR_SIZE * door_count

    # Two sector tables with a width/height header each; the sizes alone matter here.
    for bytes_per_sector in (8, 14):
        columns, rows = struct.unpack_from('<HH', data, offset)
        offset += 4 + columns * rows * bytes_per_sector

    offset += 6  # room colour and an unused word
    light_count = struct.unpack_from('<H', data, offset)[0]
    offset += 2 + light_count * _LIGHT_SIZE
    static_count = struct.unpack_from('<H', data, offset)[0]
    offset += 2 + static_count * _STATIC_MESH_SIZE

    _alternate, _flags2, water_strength, reflection = struct.unpack_from('<HHBB', data, offset)
    offset += 8
    room = RoomOverview(x, z, y_bottom, flags, vertices, polygon_count, polygon_bytes, doors,
                        water_strength, reflection)
    return room, offset


def _skip_to_animated_ranges(data, offset):
    """Skip floor data, cameras, flyby cameras, sound sources, boxes, overlaps and zones."""
    for record_size in (2, 16, 0x28, 16):
        count = struct.unpack_from('<i', data, offset)[0]
        offset += 4 + count * record_size
    box_count = struct.unpack_from('<i', data, offset)[0]
    offset += 4 + box_count * 0x14
    overlap_count = struct.unpack_from('<i', data, offset)[0]
    offset += 4 + overlap_count * 2
    offset += box_count * 0x14  # ten zone tables with two bytes per box
    return offset
