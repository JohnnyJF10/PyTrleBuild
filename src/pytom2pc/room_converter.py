"""Converting the rooms of the .TOM into the game's room format.

Most room data is copied.  The changes are:

* Vertices are widened to six words and receive water effect attributes.
* Polygons get texture-record numbers, and are grouped into quads, triangles and sprites.
* The second sector table, only used by the editor, is dropped.
* The room colour is reordered, and the room's last bytes are rewritten.
"""
import struct

from trle_common.c_arithmetic import to_int16
from pytom2pc.animated_textures import KIND_QUAD, KIND_TRIANGLE
from pytom2pc.vertex_effects import WaterScheme

_ROOM_HEADER_BYTES = 16
_DOOR_SIZE = 32
_SECTOR_SIZE = 8
_EDITOR_SECTOR_SIZE = 0xE
_LIGHT_SIZE = 0x2E
_STATIC_MESH_SIZE = 0x14
_TRIANGLES = (0x20, 0x22, 0x24)
_QUADS = (0x21, 0x23, 0x25)
_SPRITE = 0x27
_CONVERTED_TRIANGLES = (0x20, 0x24)   # kinds 0x22/0x23 are flat-colour polygons: not written
_CONVERTED_QUADS = (0x21, 0x25)


def _rgb15_to_bgr15(colour):
    """Exchange the red and blue parts of a 15-bit vertex colour."""
    colour &= 0xFFFF
    return ((colour >> 10) & 0x1F) | ((colour & 0x1F) << 10) | (colour & 0x3E0)


class RoomConverter:
    """Writes all rooms to the level buffer."""

    def __init__(self, tom_rest, texture_table, room_textures, animations, effects, log):
        """Keep the source data and the tables that the rooms add to."""
        self.tom_rest = tom_rest
        self.texture_table = texture_table
        self.room_textures = room_textures
        self.animations = animations
        self.effects = effects
        self.log = log
        self.largest_vertex_count = 0

    def write_rooms(self, output):
        """Append all rooms and the floor data; return the offset of the data after them."""
        data = self.tom_rest
        room_count = to_int16(struct.unpack_from('<H', data, 0)[0])
        offset = 2
        output += struct.pack('<h', room_count)
        self.log(f'Number Rooms:{room_count}')
        for room_index in range(room_count):
            offset = self._write_room(output, room_index, offset)

        before = len(self.texture_table.records)
        for tile_index in range(len(self.room_textures.tiles)):
            self.texture_table.add_quad(tile_index, self.room_textures, True)
        unused = len(self.texture_table.records) - before
        self.log(f' Possible Unused Room Textures:{unused}')

        floor_words = struct.unpack_from('<i', data, offset)[0]
        output += data[offset:offset + 4 + floor_words * 2]
        return offset + 4 + floor_words * 2

    def _write_room(self, output, room_index, offset):
        """Convert one room and return the offset of the next.

        Most of the room is copied.  The mesh is converted, the editor's sector table is dropped,
        the room colour is reordered and the water scheme replaces a byte of the tail.
        """
        data = self.tom_rest
        scheme = WaterScheme()
        output += data[offset:offset + _ROOM_HEADER_BYTES]

        mesh_words = struct.unpack_from('<i', data, offset + 0x16)[0]
        mesh_start = offset + 0x1A
        words = list(struct.unpack_from(f'<{mesh_words}h', data, mesh_start))
        output += self._convert_room_mesh(words, room_index, scheme)
        offset = mesh_start + mesh_words * 2

        door_count = struct.unpack_from('<h', data, offset)[0]
        output += data[offset:offset + 2 + door_count * _DOOR_SIZE]
        offset += 2 + door_count * _DOOR_SIZE

        columns, rows = struct.unpack_from('<hh', data, offset)
        output += data[offset:offset + 4]
        offset += 4
        output += data[offset:offset + columns * rows * _SECTOR_SIZE]
        offset += columns * rows * _SECTOR_SIZE

        columns, rows = struct.unpack_from('<hh', data, offset)
        offset += 4 + columns * rows * _EDITOR_SECTOR_SIZE   # editor only: not written

        red, green, blue = data[offset], data[offset + 1], data[offset + 2]
        output += struct.pack('<I', blue | (green << 8) | (red << 16))
        light_count = struct.unpack_from('<h', data, offset + 6)[0]
        output += data[offset + 6:offset + 8]
        offset += 8
        # Light records are copied without looking at them, including fields the editor never
        # initialised (see docs/formats/TOM_FORMAT.md).
        output += data[offset:offset + light_count * _LIGHT_SIZE]
        offset += light_count * _LIGHT_SIZE

        static_count = struct.unpack_from('<h', data, offset)[0]
        output += data[offset:offset + 2]
        offset += 2
        output += data[offset:offset + static_count * _STATIC_MESH_SIZE]
        offset += static_count * _STATIC_MESH_SIZE

        output += data[offset:offset + 4]                                  # flip room and flags
        output += bytes([scheme.value & 0xFF, data[offset + 6], data[offset + 7]])
        return offset + 8

    def _convert_room_mesh(self, words, room_index, scheme):
        """Convert a mesh: colours and water effects on vertices, then the polygon lists.

        The polygons are written as all quads, then all triangles, then all sprites.
        """
        position = 0
        vertex_count = words[position]
        position += 1
        self.largest_vertex_count = max(self.largest_vertex_count, vertex_count)
        result = [vertex_count]
        for _ in range(max(vertex_count, 0)):
            x, y, z, colour = words[position:position + 4]
            colour = _rgb15_to_bgr15(colour)
            vertex = [x, y, z, colour, 0, colour]
            self.effects.apply(room_index, vertex, scheme)
            result += vertex
            position += 6

        polygon_count = words[position]
        position += 1
        polygons, position = self._read_polygons(words, position, polygon_count, room_index)

        table = self.texture_table
        textures = self.room_textures
        quads = [body for kind, body in polygons if kind in _CONVERTED_QUADS]
        result.append(len(quads))
        for quad in quads:
            result += quad[0:4]
            self.animations.register_use(quad[4], KIND_QUAD)
            result.append(to_int16(table.add_quad(quad[4], textures, True)))

        triangles = [body for kind, body in polygons if kind in _CONVERTED_TRIANGLES]
        result.append(len(triangles))
        for triangle in triangles:
            result += triangle[0:3]
            self.animations.register_use(triangle[3], KIND_TRIANGLE)
            result.append(to_int16(table.add_triangle(triangle[3], textures, True)))

        sprites = [body for kind, body in polygons if kind == _SPRITE]
        result.append(len(sprites))
        for sprite in sprites:
            result += sprite[0:2]
        packed = struct.pack(f'<{len(result)}h', *[to_int16(word) for word in result])
        return struct.pack('<i', len(result)) + packed

    def _read_polygons(self, words, position, polygon_count, room_index):
        """Split the polygon words of a mesh into (kind, words) pairs."""
        polygons = []
        for _ in range(max(polygon_count, 0)):
            kind = words[position]
            if kind in _TRIANGLES:
                polygons.append((kind, words[position + 1:position + 7]))
                position += 7
            elif kind in _QUADS:
                polygons.append((kind, words[position + 1:position + 8]))
                position += 8
            elif kind == _SPRITE:
                polygons.append((kind, words[position + 1:position + 3]))
                position += 3
            else:
                self.log(f'Unknown polygon type in room {room_index}: {kind}')
                polygons.append((kind, ()))
                position += 1
        return polygons, position
