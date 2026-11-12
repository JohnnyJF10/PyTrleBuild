"""Reading the object data of the .WAD and converting object meshes to the game's form.

The .WAD holds moving and static objects.  Almost all of it is passed on unchanged.  Only the
meshes are rewritten: their polygons get texture-record numbers instead of tile numbers, and quads
and triangles are grouped into separate lists.
"""
import struct
from dataclasses import dataclass

from trle_common.c_arithmetic import c_div, to_int16

_POLYGON_TRIANGLE = 8
_POLYGON_QUAD = 9


@dataclass
class ObjectChunks:
    """The sections of the .WAD copied unchanged to the level: ``(record_count, bytes)``."""

    animations: tuple
    state_changes: tuple
    dispatches: tuple
    commands: tuple
    mesh_trees: tuple
    frames: tuple
    moveables: tuple
    statics: tuple

    def in_level_order(self):
        """Return the chunks in the order the level stores them."""
        return (self.animations, self.state_changes, self.dispatches, self.commands,
                self.mesh_trees, self.frames, self.moveables, self.statics)


class WadObjectData:
    """Mesh pointers, mesh words and the pass-through sections of a .WAD."""

    def __init__(self, data):
        """Parse the object data: mesh pointers, the mesh block, and the fixed-size record chunks.
        """
        self.data = data
        position = 0
        pointer_count = struct.unpack_from('<i', data, position)[0]
        position += 4
        self.mesh_pointers = list(struct.unpack_from(f'<{pointer_count}i', data, position))
        position += 4 * pointer_count
        mesh_words = struct.unpack_from('<i', data, position)[0]
        position += 4
        self.mesh_data_offset = position
        position += mesh_words * 2

        def read_chunk(record_size):
            """Read a chunk: a count followed by that many records."""
            nonlocal position
            count = struct.unpack_from('<i', data, position)[0]
            chunk = data[position + 4:position + 4 + count * record_size]
            position += 4 + count * record_size
            return count, chunk

        self.chunks = ObjectChunks(read_chunk(40), read_chunk(6), read_chunk(8), read_chunk(2),
                                   read_chunk(4), read_chunk(2), read_chunk(0x12),
                                   read_chunk(0x20))


class ObjectMeshConverter:
    """Produces the level's mesh block and its table of mesh pointers."""

    def __init__(self, wad, texture_table, object_textures, log):
        """Prepare the conversion of the meshes."""
        self.wad = wad
        self.texture_table = texture_table
        self.object_textures = object_textures
        self.log = log
        self.largest_vertex_count = 0
        self.mesh_bytes = b''
        self.mesh_pointers = []

    def convert_all(self):
        """Convert every mesh once; pointers to the same source mesh share one result."""
        output = bytearray()
        new_pointers = []
        converted = 0
        source_pointers = self.wad.mesh_pointers
        first_use = {}
        for index, pointer in enumerate(source_pointers):
            if pointer in first_use:
                new_pointers.append(new_pointers[first_use[pointer]])
                continue
            first_use[pointer] = index
            output += b'\0' * (((len(output) + 3) & ~3) - len(output))
            new_pointers.append(len(output))
            output += self._convert_mesh(self.wad.mesh_data_offset + c_div(pointer, 2) * 2)
            converted += 1
        output += b'\0' * (((len(output) + 3) & ~3) - len(output))
        self.mesh_bytes = bytes(output)
        self.mesh_pointers = new_pointers
        self.log(f'Object Meshes:{len(source_pointers)} '
                 f'Null:{len(source_pointers) - converted}')

    def _convert_mesh(self, offset):
        """Convert one mesh: header and vertices unchanged, polygons sorted into two lists."""
        data = self.wad.data

        def read_word():
            """Read the next word of the mesh."""
            nonlocal offset
            value = struct.unpack_from('<h', data, offset)[0]
            offset += 2
            return value

        words = [read_word() for _ in range(5)]        # bounding sphere and collision radius
        vertex_count = read_word()
        words.append(vertex_count)
        self.largest_vertex_count = max(self.largest_vertex_count, vertex_count)
        words += [read_word() for _ in range(max(vertex_count, 0) * 3)]

        # A positive count means normals (three words each); negative means vertex shades.
        normal_count = read_word()
        words.append(normal_count)
        if normal_count < 0:
            words += [read_word() for _ in range(-normal_count)]
        elif normal_count > 0:
            words += [read_word() for _ in range(normal_count * 3)]

        polygon_count = read_word()
        quads, triangles = self._read_polygons(offset, polygon_count)

        table = self.texture_table
        source = self.object_textures
        words.append(len(quads))
        for quad in quads:
            words += [*quad[:4], to_int16(table.add_quad(quad[4], source, False)), quad[5]]
        words.append(len(triangles))
        for triangle in triangles:
            words += [*triangle[:3], to_int16(table.add_triangle(triangle[3], source, False)),
                      triangle[4]]
        return struct.pack(f'<{len(words)}h', *[to_int16(word) for word in words])

    def _read_polygons(self, offset, polygon_count):
        """Read the mixed polygon list, keeping quads and triangles in their original order."""
        data = self.wad.data
        quads, triangles = [], []
        for _ in range(max(polygon_count, 0)):
            kind = struct.unpack_from('<h', data, offset)[0]
            if kind == _POLYGON_TRIANGLE:
                triangles.append(struct.unpack_from('<5h', data, offset + 2))
                offset += 12
            elif kind == _POLYGON_QUAD:
                quads.append(struct.unpack_from('<6h', data, offset + 2))
                offset += 14
            else:
                self.log(f'Unsupported POLY TYPE IN OBJECT MESH ->{kind}')
                offset += 2
        return quads, triangles
