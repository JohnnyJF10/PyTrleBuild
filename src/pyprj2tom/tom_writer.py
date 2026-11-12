"""Writing the .TOM file, and the .TXT and .CD files beside it (see docs/formats/TOM_FORMAT.md)."""
import math
import struct
from dataclasses import dataclass

from trle_common.msvc_sort import msvc_qsort
from trle_common.c_arithmetic import to_int16, to_int32
from pyprj2tom.floor_data import SectorTableWriter
from pyprj2tom.pathfinding import PathfindingBuilder, compute_zones
from pyprj2tom.project_model import (CLASS_CAMERA, CLASS_ITEM, CLASS_LIGHT, CLASS_SINK,
                                   CLASS_SOUND_SOURCE, TYPE_CAMERA_FLYBY, TYPE_CAMERA_NORMAL,
                                   object_class)
from pyprj2tom.vertex_math import DEGREES_TO_RADIANS, direction_from_angles, to_float32

_AI_SLOT_FIRST = 0x18E
_AI_SLOT_LAST = 0x196
_EXTRA_ROOM_FLAG_NEIGHBOUR = 8
_DEFAULT_REACH_FLAG = 0x40
_MAX_FLYBY_CAMERAS = 0x500
_TEXTURED_POLYGON_KINDS = (6, 7, 8, 9, 0xE, 0xF, 0x10, 0x11)


@dataclass
class FlybyEntry:
    """A flyby camera of the level: its sequence, its position in the sequence and its object."""
    sequence: int
    index: int
    object_id: int


def _is_regular_item(project, placed):
    """Tell whether an object is a normal item, as opposed to an AI marker object."""
    return (object_class(placed.type) == CLASS_ITEM
            and not _AI_SLOT_FIRST <= placed.link <= _AI_SLOT_LAST)


class TomWriter:
    """Writes one level."""

    def __init__(self, project, compiler, output_base, log):
        """Prepare the writer.

        ``floor_data`` starts with one dummy word because index 0 means "this sector has no floor
        data".
        """
        self.project = project
        self.compiler = compiler
        self.output_base = output_base
        self.log = log
        self.floor_data = [0]
        self.cd_lines = []
        self.flyby_cameras = []
        self.ai_records = []
        self.slot_counts = []

    def write(self):
        """Run the export and write the three output files."""
        project = self.project
        output = bytearray()
        version = project.object_version | (0xFF180000 if project.texture else 0xFF080000)
        output += struct.pack('<I', version)
        pathfinding = PathfindingBuilder(project, self.compiler)
        pathfinding.build()
        self._write_textures(output)

        self.slot_counts = [0 if slot.kind else -1 for slot in project.object_slots]
        self._number_rooms()
        self._number_objects()
        self._sort_flyby_cameras()
        output += struct.pack('<H', self.used_room_count)
        self._write_rooms(output)
        output += struct.pack('<i', len(self.floor_data))
        output += struct.pack(f'<{len(self.floor_data)}H',
                              *[word & 0xFFFF for word in self.floor_data])
        self._write_cameras(output)
        self._write_sound_sources(output)
        self._write_boxes(output, pathfinding)
        self._write_animated_ranges(output)
        self._write_items(output)
        output += project.texture.light_table
        self._write_portal_map(output)
        self._write_ai_objects(output)

        with open(self.output_base + '.TOM', 'wb') as file:
            file.write(output)
        self._write_object_report()
        with open(self.output_base + '.CD', 'w', newline='') as file:
            for line in self.cd_lines:
                file.write(line.replace('\n', '\r\n'))

    # ---------------------------------------------------------------------- textures
    def _write_textures(self, output):
        """Write the texture table, the used texture pages and the palette.

        Only 64 x 64 cells used by some polygon are stored.  Cells are renumbered in order of their
        position in the image, tiles are given new consecutive numbers (``self.tile_map``), and the
        offset of each tile is recomputed for its cell's new place.  All tiles of an animated range
        are kept when one of them is used.
        """
        project = self.project
        rooms = [room for room in project.rooms if room.is_used]
        tiles = project.texture_tiles
        # -1 marks "not used", -2 "used, number not assigned yet".
        tile_number = [-1] * len(tiles)
        cell_number = [-1] * 0x100
        cell_of_tile = {}
        used_count = 0
        for room in rooms:
            for polygon in room.mesh.polygons:
                if polygon.kind not in _TEXTURED_POLYGON_KINDS:
                    continue
                index = polygon.texture & 0x3FF
                if tile_number[index] == -1:
                    used_count += 1
                    tile_number[index] = -2
                    offset = tiles[index][0]
                    row, column = offset >> 8, offset - (offset >> 8) * 256
                    cell = (column >> 6) + (row >> 6) * 4
                    cell_of_tile[index] = cell
                    if cell_number[cell] == -1:
                        cell_number[cell] = -2

        self.used_range_list = []
        for used, first, last in project.animated_ranges:
            if not used:
                continue
            entry_used = any(tile_number[i] == -2 for i in range(first, last + 1))
            if entry_used:
                self.used_range_list.append((first, last))
                for i in range(first, last + 1):
                    if tile_number[i] == -1:
                        tile_number[i] = -2
                        # The editor records the tile number as its cell here.
                        cell_of_tile[i] = i
                        cell_number[i] = -2
                        used_count += 1

        next_cell = 0
        for index, value in enumerate(cell_number):
            if value != -1:
                cell_number[index] = next_cell
                next_cell += 1
        self.page_rows = (next_cell + 3) >> 2

        output += struct.pack('<H', used_count)
        self.tile_map = {}
        attributes = bytearray()
        next_tile = 0
        for index in range(len(tiles)):
            if tile_number[index] == -1:
                continue
            offset, width, height = tiles[index]
            row, column = offset >> 8, offset - (offset >> 8) * 256
            cell_new = cell_number[cell_of_tile[index]]
            new_offset = (((cell_new & 3) + ((row & 0x3F) + (cell_new >> 2) * 0x40) * 4) * 0x40
                          + (column & 0x3F))
            output += struct.pack('<iHH', new_offset, width, height)
            attributes.append(project.bump_table[(row >> 6) * 4 + (column >> 6)])
            self.tile_map[index] = next_tile
            next_tile += 1
        output += attributes
        self._write_pages(output, cell_number, next_cell)
        output += project.texture.palette_6bit

    def _write_pages(self, output, cell_number, cell_count):
        """Write the used 64 x 64 cells, four to a row.

        If the last row is not full, its empty slots keep the source positions of the row before,
        moved on by one row of cells.  These can lie below the end of the image.  The editor's image
        buffer is twice the height of the image and its second half is never written, so there it
        reads zeros.
        """
        image = self.project.texture.rgb
        rows = (cell_count + 3) >> 2
        output += struct.pack('<i', rows * 0xC000)
        source = [0, 0, 0, 0]
        cell = 0
        written = 0
        while written < cell_count:
            slot = 0
            while True:
                while cell_number[cell] == -1:
                    cell += 1
                source[slot] = (cell >> 2) * 0x100 * 0x40 + (cell & 3) * 0x40
                cell += 1
                written += 1
                if written == cell_count:
                    break
                slot += 1
                if slot >= 4:
                    break
            for _ in range(0x40):
                for position in range(4):
                    start = source[position] * 3
                    output += image[start:start + 0xC0].ljust(0xC0, b'\0')
                    source[position] += 0x100

    # ---------------------------------------------------------------------- numbering
    def _number_rooms(self):
        """Give the used rooms consecutive numbers and note which room each flip room replaces."""
        project = self.project
        self.room_numbers = {}
        next_number = 0
        self.alternate_of = {}
        self.base_of = {}
        for index, room in enumerate(project.rooms):
            if room.is_used:
                self.room_numbers[index] = next_number
                next_number += 1
                if room.flip_room != -1:
                    self.base_of[room.flip_room] = index
        self.used_room_count = next_number

    def _number_objects(self):
        """Number the objects that other parts of the level refer to.

        Items, cameras together with sinks, and sound sources each have a numbering of their own. AI
        markers are collected for their own table and flyby cameras for sorting.  The per-slot use
        counts are what the ``.TXT`` report prints.
        """
        project = self.project
        self.object_numbers = {}
        self.item_count = self.camera_count = self.sound_count = 0
        for index, room in enumerate(project.rooms):
            if not room.is_used:
                continue
            for object_id in room.object_ids:
                placed = project.objects[object_id]
                if object_class(placed.type) != CLASS_ITEM:
                    continue
                slot = placed.link
                if _AI_SLOT_FIRST <= slot <= _AI_SLOT_LAST:
                    self._add_ai_object(index, room, placed)
                    continue
                self.slot_counts[slot] += 1
                if project.object_slots[slot].kind in (0x10, 8):
                    self.object_numbers[object_id] = self.item_count
                    self.item_count += 1
            for object_id in room.light_ids:
                placed = project.objects[object_id]
                kind = object_class(placed.type)
                if kind == CLASS_CAMERA and placed.type != TYPE_CAMERA_FLYBY or kind == CLASS_SINK:
                    if object_id not in self.object_numbers:
                        self.object_numbers[object_id] = self.camera_count
                        self.camera_count += 1
                elif kind == CLASS_CAMERA:
                    self.flyby_cameras.append(FlybyEntry((placed.link >> 13) & 7,
                                                         placed.link >> 8 & 0x1F, object_id))
                elif kind == CLASS_SOUND_SOURCE and object_id not in self.object_numbers:
                    self.object_numbers[object_id] = self.sound_count
                    self.sound_count += 1

    def _add_ai_object(self, room_index, room, placed):
        """Store the 24-byte record of an AI marker object; the table is sorted later."""
        record = struct.pack('<HHiiiHHHH', placed.link, self.room_numbers[room_index],
                             to_int32(room.world_x + placed.world_x), placed.world_y,
                             to_int32(room.world_z + placed.world_z), placed.tint2 & 0xFFFF,
                             placed.flags & 0xFFFF, placed.rotation_y & 0xFFFF, 0)
        self.ai_records.append(record)

    def _sort_flyby_cameras(self):
        """Sort the flyby cameras by sequence and position, with the qsort of the original."""
        msvc_qsort(self.flyby_cameras, _compare_flyby_entries)

    # ---------------------------------------------------------------------- rooms
    def _write_rooms(self, output):
        """Write all rooms; a flip room is written using the geometry of its alternate state.

        The rooms' flip state is toggled as needed, because the geometry of a flip pair is exchanged
        in memory.  A flip room that sits exactly on top of its base room gets ``extra_flag`` 2.
        """
        project = self.project
        table_writer = SectorTableWriter(project, self.room_numbers, self.object_numbers,
                                         self.flyby_cameras,
                                         self.floor_data, self.cd_lines, self.log)
        for index, room in enumerate(project.rooms):
            if not room.is_used:
                continue
            if room.flags & 2:
                if not project.flipped:
                    self.compiler.toggle_flip_state()
                source_room = project.rooms[self.base_of[index]]
                extra_flag = 0
                if (room.position_x == source_room.position_x
                    and room.position_z == source_room.position_z
                        and room.floor_height == source_room.floor_height):
                    extra_flag = 2
                source_index = self.base_of[index]
            else:
                if project.flipped:
                    self.compiler.toggle_flip_state()
                source_room = room
                extra_flag = 0
                source_index = index
            self._write_room(output, index, room, source_room, source_index, extra_flag,
                             table_writer)
        if project.flipped:
            self.compiler.toggle_flip_state()

    def _write_room(self, output, index, room, source, source_index, extra_flag, table_writer):
        """Write one room: header, mesh, doors, sector tables, lights, static meshes and tail.

        ``room`` supplies the identity of the room (its flip partner), ``source`` the geometry.  A
        room with a door to a room with flag 8 loses the default "reach" flag ``0x40``.
        """
        self.project.current_room = source_index
        output += struct.pack('<ii', source.world_x, source.world_z)
        output += struct.pack('<ii', source.floor_height * -0x100, source.ceiling_height * -0x100)
        output += struct.pack('<HH', source.size_x, source.size_z)
        output += struct.pack('<H', extra_flag | source.flags)
        self._write_mesh(output, source)
        reach_flag = _DEFAULT_REACH_FLAG
        output += struct.pack('<H', len(source.door_ids))
        for door, record in zip(source.door_ids, source.doors):
            target = self.project.rooms[record.adjoining_room]
            if target.flags & _EXTRA_ROOM_FLAG_NEIGHBOUR:
                reach_flag = 0
            output += struct.pack('<H', self.room_numbers[record.adjoining_room])
            output += struct.pack('<3h', *record.normal)
            for corner in record.corners:
                output += struct.pack('<3h', *corner)
        table_writer.write_room_tables(output, source_index)
        self._write_lights(output, source)
        self._write_static_meshes(output, source)
        flip = room.flip_room
        output += struct.pack('<h', self.room_numbers[flip] if flip != -1 else -1)
        output += struct.pack('<H', (source.flags | reach_flag) & 0xFFFF)
        source.flags = (source.flags | reach_flag) & 0xFFFF
        output += struct.pack('<4B', source.water_level, source.reflection_level, source.reverb,
                              source.flip_group)

    def _write_mesh(self, output, room):
        """Write the vertex and polygon lists of a room.

        Sprites placed in the room count as an extra vertex and an extra polygon each.  The size
        stored first is in 16-bit words: a vertex is 6 words, a quad 8, a triangle 7 and a sprite 9
        in total.
        """
        project = self.project
        mesh = room.mesh
        sprites = self._room_sprites(room)
        vertex_count = len(mesh.vertices)
        attributes = self._vertex_attributes(room)
        words = len(sprites) * 9 + len(mesh.polygons) * 8 + 2 + vertex_count * 6
        words -= sum(1 for polygon in mesh.polygons if len(polygon.vertices) == 3)
        output += struct.pack('<i', words)
        output += struct.pack('<H', vertex_count + len(sprites))
        for (x, y, z), shade, attribute in zip(mesh.vertices, mesh.shades, attributes):
            output += struct.pack('<6h', x, y, z, to_int16(shade), 0, attribute)
        for placed in sprites:
            output += struct.pack('<6H', placed.world_x & 0xFFFF, placed.world_y & 0xFFFF,
                                  placed.world_z & 0xFFFF,
                                  placed.tint1 & 0xFFFF, placed.tint2 & 0xFFFF, 0)
        output += struct.pack('<H', len(mesh.polygons) + len(sprites))
        tile_map = self.tile_map
        for polygon in mesh.polygons:
            code = {2: 0x22, 4: 0x22, 3: 0x23, 5: 0x23, 6: 0x24, 8: 0x24, 7: 0x25, 9: 0x25,
                    0xE: 0x20, 0x10: 0x20, 0xF: 0x21, 0x11: 0x21}[polygon.kind]
            output += struct.pack('<H', code)
            output += struct.pack(f'<{len(polygon.vertices)}H', *polygon.vertices)
            texture = polygon.texture
            if polygon.kind not in (2, 3, 4, 5):
                texture = tile_map[texture & 0x3FF] | (texture & 0xFC00)
            output += struct.pack('<HHH', texture & 0xFFFF, polygon.sector_x, polygon.sector_z)
        for index, placed in enumerate(sprites):
            output += struct.pack('<HHH', 0x27, vertex_count + index,
                                  project.object_slots[placed.link].number)

    def _room_sprites(self, room):
        """Return the objects of a room that are sprites (slot kind ``0x108``)."""
        project = self.project
        result = []
        for object_id in room.object_ids:
            placed = project.objects[object_id]
            if (object_class(placed.type) == CLASS_ITEM
                    and project.object_slots[placed.link].kind == 0x108):
                result.append(placed)
        return result

    def _vertex_attributes(self, room):
        """Mark vertices at openings between water and non-water rooms."""
        project = self.project
        attributes = [0] * len(room.mesh.vertices)
        for object_id, record in zip(room.door_ids, room.doors):
            partner = project.objects[project.objects[object_id].link].room
            if not ((project.rooms[partner].flags ^ room.flags) & 1):
                continue
            (min_x, max_x), (min_y, max_y), (min_z, max_z) = _door_box(record)
            for number, (x, y, z) in enumerate(room.mesh.vertices):
                if min_x <= x <= max_x and min_y <= y <= max_y and min_z <= z <= max_z:
                    attributes[number] = 1
        return attributes

    def _write_lights(self, output, room):
        """Write the room colour and the light records, sun lights first."""
        project = self.project
        output += room.ambient
        output += struct.pack('<H', room.effect_word)
        records = []
        for object_id in room.light_ids:
            placed = project.objects[object_id]
            if placed.type != 0x4200:
                continue
            light = project.lights[placed.link]
            if light.enabled:
                records.append(self._sun_record(light))
        for object_id in room.light_ids:
            placed = project.objects[object_id]
            if object_class(placed.type) != CLASS_LIGHT:
                continue
            light = project.lights[placed.link]
            if not light.enabled:
                continue
            record = self._light_record(room, placed, light)
            if record is not None:
                records.append(record)
        output += struct.pack('<H', len(records))
        for record in records:
            output += record

    @staticmethod
    def _sun_record(light):
        """Build the record of a sun light, which uses only its colour and direction.

        The bytes up to the direction are zero here; the editor leaves them uninitialised.
        """
        direction = direction_from_angles(light.y_angle, light.x_angle)
        return struct.pack('<3i3BBh8x12x12x', 0, 0, 0, light.red, light.green, light.blue, 0,
                           light.intensity)[:0x22] + \
            struct.pack('<3f', *direction)

    def _light_record(self, room, placed, light):
        """Build the record of a point, spot, shadow or fog light, or None for another type.

        Shadow lights store a negative intensity.  Spot lights store the cosines of their cone
        angles and their direction.  Fields a type does not use are zero here; the editor leaves
        them uninitialised.
        """
        position = struct.pack('<3i', to_int32(room.world_x + placed.world_x), placed.world_y,
                               to_int32(room.world_z + placed.world_z))
        type_code = {0x4000: 1, 0x4100: 2, 0x6000: 3, 0x4020: 4}.get(placed.type)
        if type_code is None:
            return None
        intensity = -light.intensity if placed.type == 0x6000 else light.intensity
        record = position + struct.pack('<3Bb', light.red, light.green, light.blue, type_code)
        record += struct.pack('<h', to_int16(intensity))
        if placed.type == 0x4100:
            axis = direction_from_angles(light.y_angle, light.x_angle)
            record += struct.pack('<4f', to_float32(_cos_degrees(light.inner)),
                                  to_float32(_cos_degrees(light.outer)),
                                  light.length, light.cutoff)
            record += struct.pack('<3f', *axis)
        else:
            record += struct.pack('<2f', light.inner, light.outer)
            record += b'\0' * (0x2E - len(record))
        return record

    def _write_static_meshes(self, output, room):
        """Write the static meshes placed in a room; their slot numbers start after the moveables.
        """
        project = self.project
        written = bytearray()
        count = 0
        for object_id in room.object_ids:
            placed = project.objects[object_id]
            if not _is_regular_item(project, placed):
                continue
            if project.object_slots[placed.link].kind != 0x110:
                continue
            written += struct.pack('<iii', to_int32(room.world_x + placed.world_x), placed.world_y,
                                   to_int32(room.world_z + placed.world_z))
            written += struct.pack('<HHHH', placed.rotation_y & 0xFFFF, placed.tint1 & 0xFFFF,
                                   placed.tint2 & 0xFFFF,
                                   (placed.link - project.moveable_slot_count) & 0xFFFF)
            count += 1
        output += struct.pack('<H', count)
        output += written

    # ---------------------------------------------------------------------- tail sections
    def _camera_room_of(self, index, room):
        """Alternate rooms share their cameras with the base room: return that room and number."""
        if room.flags & 2:
            base = self.base_of[index]
            return self.project.rooms[base], self.room_numbers[base]
        return room, self.room_numbers[index]

    def _write_cameras(self, output):
        """Write the cameras and sinks, followed by the sorted flyby camera records.

        For a sink the two fields after the position hold its strength and the box it stands in.
        """
        project = self.project
        block = bytearray()
        for index, room in enumerate(project.rooms):
            if not room.is_used:
                continue
            source_room, room_number = self._camera_room_of(index, room)
            for object_id in room.light_ids:
                placed = project.objects[object_id]
                kind = object_class(placed.type)
                if kind not in (CLASS_CAMERA, CLASS_SINK) or placed.type == TYPE_CAMERA_FLYBY:
                    continue
                block += struct.pack('<3i', to_int32(source_room.world_x + placed.world_x),
                                     placed.world_y,
                                     to_int32(source_room.world_z + placed.world_z))
                if kind == CLASS_CAMERA:
                    first, second = room_number, 1 if placed.type != TYPE_CAMERA_NORMAL else 0
                else:
                    first = (placed.flags >> 1) & 0x1F
                    second = room.sector_at(placed.x, placed.z).box
                block += struct.pack('<hH', first, second & 0xFFFF)
        output += struct.pack('<i', self.camera_count)
        output += block

        records = self._flyby_records()
        msvc_qsort(records, _compare_flyby_records)
        output += struct.pack('<i', len(records))
        for record in records:
            output += record

    def _flyby_records(self):
        """Build the 40-byte record of each flyby camera, in room order."""
        project = self.project
        records = []
        for index, room in enumerate(project.rooms):
            if not room.is_used:
                continue
            source_room, room_number = self._camera_room_of(index, room)
            for object_id in room.light_ids:
                placed = project.objects[object_id]
                if placed.type != TYPE_CAMERA_FLYBY:
                    continue
                x = to_int32(source_room.world_x + placed.world_x)
                y = placed.world_y
                z = to_int32(source_room.world_z + placed.world_z)
                direction = direction_from_angles(float(placed.rotation_y),
                                                  float(placed.rotation_x))
                targets = [to_int32(int(float(position) - axis * 1024.0))
                           for position, axis in zip((x, y, z), direction)]
                sequence, frame = (placed.link >> 13) & 7, (placed.link >> 8) & 0x1F
                records.append(struct.pack('<6iBB6H2x', x, y, z, *targets, sequence, frame,
                                           (placed.link & 0xFF) * 0xB6 & 0xFFFF,
                                           placed.roll * 0xB6 & 0xFFFF, placed.flags & 0xFFFF,
                                           placed.tint1 & 0xFFFF, placed.tint2 & 0xFFFF,
                                           room_number & 0xFFFF))
        return records

    def _write_sound_sources(self, output):
        """Write the sound sources; the flag bits record in which flip state each one plays."""
        project = self.project
        block = bytearray()
        for room in project.rooms:
            if not room.is_used:
                continue
            for object_id in room.light_ids:
                placed = project.objects[object_id]
                if object_class(placed.type) != CLASS_SOUND_SOURCE:
                    continue
                block += struct.pack('<3i', to_int32(room.world_x + placed.world_x), placed.world_y,
                                     to_int32(room.world_z + placed.world_z))
                flip_flags = 0xC0 if room.flip_room == -1 and not room.flags & 2 else 0x40
                if room.flip_room != -1:
                    flip_flags = 0x80
                block += struct.pack('<HH', placed.link & 0xFFFF,
                                     (placed.flags + flip_flags) & 0xFFFF)
        output += struct.pack('<i', self.sound_count)
        output += block

    def _write_boxes(self, output, pathfinding):
        """Write the boxes and overlaps, then the zones of the five movement classes in both flip
        states.
        """
        output += struct.pack('<i', len(pathfinding.boxes))
        for box in pathfinding.boxes:
            output += struct.pack('<4ihH', box.x_min, box.x_max, box.z_min, box.z_max, box.height,
                                  box.overlap_index & 0xFFFF)
        output += struct.pack('<i', len(pathfinding.overlaps))
        output += struct.pack(f'<{len(pathfinding.overlaps)}H', *pathfinding.overlaps)
        for state in (4, 2):
            for movement_class in range(5):
                zones = compute_zones(pathfinding, movement_class, state)
                output += struct.pack(f'<{len(zones)}H', *zones)

    def _write_animated_ranges(self, output):
        """Write the first and last new tile number of each used animated range."""
        output += struct.pack('<i', len(self.used_range_list))
        for first, last in self.used_range_list:
            output += struct.pack('<ii', self.tile_map[first], self.tile_map[last])

    def _write_items(self, output):
        """Write the moveable items of all rooms; items whose mesh cannot be tinted get the shade
        ``0xFFFF``.
        """
        project = self.project
        output += struct.pack('<i', self.item_count)
        room_number = 0
        for room in project.rooms:
            if not room.is_used:
                continue
            for object_id in room.object_ids:
                placed = project.objects[object_id]
                if not _is_regular_item(project, placed):
                    continue
                if project.object_slots[placed.link].kind not in (0x10, 8):
                    continue
                shade = placed.tint1 if self._mesh_can_tint(placed) else 0xFFFF
                output += struct.pack('<HH3iHHHH', placed.link & 0xFFFF, room_number,
                                      to_int32(room.world_x + placed.world_x), placed.world_y,
                                      to_int32(room.world_z + placed.world_z),
                                      placed.rotation_y & 0xFFFF,
                                      shade & 0xFFFF, placed.tint2 & 0xFFFF,
                                      (placed.flags & 0xFF) << 8)
            room_number += 1

    def _mesh_can_tint(self, placed):
        """Tell whether the item in this slot takes a shade: sprites do, meshes depending on their
        normals.
        """
        project = self.project
        slot = project.object_slots[placed.link]
        if slot.kind == 8:
            return True
        return project.mesh_has_negative_normal_count[slot.number]

    def _write_ai_objects(self, output):
        """Write the AI marker objects, sorted with the qsort of the original."""
        msvc_qsort(self.ai_records, _compare_ai_records)
        output += struct.pack('<i', len(self.ai_records))
        for record in self.ai_records:
            output += record

    def _write_portal_map(self, output):
        """Write the list of edges between walls and walkable sectors, for each room.

        An edge is written as two corner positions; each room's list and the whole list end with -1.
        """
        project = self.project
        data = bytearray()
        for room in project.rooms:
            if not room.is_used:
                continue
            for z in range(1, room.size_z):
                for x in range(1, room.size_x):
                    sector = room.sector_at(x, z)
                    previous_row = room.sector_at(x, z - 1)
                    previous_col = room.sector_at(x - 1, z)
                    if sector.kind & 8:
                        if previous_row.kind & 1:
                            data += struct.pack('<HHHH', x * 0x400, z * 0x400, x * 0x400 + 0x400,
                                                z * 0x400)
                        if previous_col.kind & 1:
                            data += struct.pack('<HHHH', x * 0x400, z * 0x400, x * 0x400,
                                                z * 0x400 + 0x400)
                    elif sector.kind & 1:
                        if previous_row.kind & 8:
                            data += struct.pack('<HHHH', x * 0x400, z * 0x400, x * 0x400 + 0x400,
                                                z * 0x400)
                        if previous_col.kind & 8:
                            data += struct.pack('<HHHH', x * 0x400, z * 0x400, x * 0x400,
                                                z * 0x400 + 0x400)
            data += b'\xFF\xFF\xFF\xFF'
        data += b'\xFF\xFF\xFF\xFF'
        output += struct.pack('<i', len(data))
        output += data

    # ---------------------------------------------------------------------- reports
    def _write_object_report(self):
        """Write the ``.TXT`` file listing how often each object slot is used, and the unused slots.
        """
        project = self.project
        with open(self.output_base + '.TXT', 'w', newline='') as file:
            file.write('NUMBER OF OBJECTS IN MAP\r\n')
            for slot, count in zip(project.object_slots, self.slot_counts):
                if count > 0:
                    file.write(f'{count:3d} x {slot.name}\r\n')
            file.write('\r\nUNUSED OBJECTS IN WAD\r\n')
            for slot, count in zip(project.object_slots, self.slot_counts):
                if count == 0:
                    file.write(f'      {slot.name}\r\n')


def _compare_flyby_entries(first, second):
    """Order of the flyby entries used by triggers: sequence, then frame."""
    return (first.sequence - second.sequence) or (first.index - second.index)


def _compare_flyby_records(first, second):
    """Order of the written flyby records, using the sequence and frame bytes of each record."""
    return (first[0x18] - second[0x18]) or (first[0x19] - second[0x19])


def _compare_ai_records(first, second):
    """Order of AI objects: slot number descending, then the 16-bit field at 0x10 ascending."""
    slot_first = struct.unpack_from('<h', first, 0)[0]
    slot_second = struct.unpack_from('<h', second, 0)[0]
    if slot_second != slot_first:
        return slot_second - slot_first
    return struct.unpack_from('<h', first, 0x10)[0] - struct.unpack_from('<h', second, 0x10)[0]


def _door_box(record):
    """Return the (low, high + 1) range of a door along each axis."""
    xs = [corner[0] for corner in record.corners]
    ys = [corner[1] for corner in record.corners]
    zs = [corner[2] for corner in record.corners]
    return (min(xs), max(xs) + 1), (min(ys), max(ys) + 1), (min(zs), max(zs) + 1)


def _cos_degrees(value):
    """Return the cosine of an angle given in degrees."""
    return math.cos(value * DEGREES_TO_RADIANS)
