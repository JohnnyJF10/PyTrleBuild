"""Writing the sector tables of a room and generating the level's floor data.

For each sector the exporter produces one record for the game and one for the editor.  Sloped floors
and ceilings, portals, triggers and special properties of the sector become a short list of
"floor data" function words, stored in one list for the entire level.
"""
import struct

from pyprj2tom.project_model import (CLASS_CAMERA, CLASS_ITEM, CLASS_SINK, FLAG_MONKEY, FLAG_TRIGGER,
                                   SECTOR_BORDER_WALL, SECTOR_DOOR, SECTOR_WALL, TYPE_CAMERA_FLYBY,
                                   TYPE_DOOR_CEILING, TYPE_DOOR_FLOOR, object_class)
from pyprj2tom.vertex_math import face_is_planar
from trle_common.c_arithmetic import to_int16

_NO_COLLISION_FLOOR = 0x06
_NO_COLLISION_CEILING = 0x18
_NO_ROOM_LINK = 0xFF
_FD_PORTAL, _FD_FLOOR_SLOPE, _FD_CEILING_SLOPE, _FD_TRIGGER, _FD_DEATH = 1, 2, 3, 4, 5
_FD_CLIMB = 6
_FD_MONKEY, _FD_MINECART_LEFT, _FD_MINECART_RIGHT = 0x13, 0x14, 0x15
_END_BIT = 0x8000
_NO_BLOCK_VALUE = -0x7F00
_ROOM_FLAG_WATER = 1
_TRIGGER_TYPE_CAMERA_LIKE = 8
_TRIGGER_NEEDS_TARGET = 6


class SectorTableWriter:
    """Writes the two sector tables of each room and accumulates the floor data."""

    def __init__(self, project, room_numbers, object_numbers, flyby_cameras, floor_data, cd_lines,
                 log):
        """Keep references to the numbering tables and the level-wide floor data list.

        ``_type_residue`` and ``_last_sector_is_blocked`` are values the original carries from one
        sector to the next, which end up in the output.
        """
        self.project = project
        self.room_numbers = room_numbers
        self.object_numbers = object_numbers
        self.flyby_cameras = flyby_cameras
        self.floor_data = floor_data
        self.cd_lines = cd_lines
        self.log = log
        self._type_residue = 0
        self._last_sector_is_blocked = False

    # ---------------------------------------------------------------------- common
    def _door_at(self, room_index, x, z):
        """Return the doors of a room whose rectangle covers a sector, last door first."""
        project = self.project
        room = project.rooms[room_index]
        found = []
        for object_id in reversed(room.door_ids):
            door = project.objects[object_id]
            if door.x <= x < door.x + door.size_x and door.z <= z < door.z + door.size_z:
                found.append((object_id, door))
        return found

    def _triggers_at(self, room, x, z):
        """Return trigger objects of a sector in the order the export visits them."""
        project = self.project
        found = []
        for object_id in reversed(room.object_ids):
            placed = project.objects[object_id]
            if object_class(placed.type) == 0x10 and placed.x <= x < placed.x + placed.size_x \
                    and placed.z <= z < placed.z + placed.size_z:
                found.append(placed)
        return found

    def _partner_room(self, door):
        """Return the number of the room on the other side of a door."""
        return self.project.objects[door.link].room

    def _height_addition(self, room, x, z):
        """Sum the height added to a sector by the items standing on it, such as bridges."""
        total = 0
        project = self.project
        for object_id in room.object_ids:
            placed = project.objects[object_id]
            if object_class(placed.type) == CLASS_ITEM and placed.x == x and placed.z == z:
                total += project.object_slots[placed.link].collision_heights[12]
        return total

    def _find_wall_portal_room(self, room_index, x, z):
        """Find the room connected through a floor/ceiling portal of a neighbouring wall sector.

        Returns 0xFF when there is none.
        """
        rooms = self.project.rooms
        room = rooms[room_index]
        original_room, original_x, original_z = room_index, x, z
        row = z - 1
        while row <= z + 1:
            if 0 <= row < room.size_z:
                column = x - 1
                while column <= x + 1:
                    if 0 <= column < room.size_x:
                        sector = room.sector_at(column, row)
                        if sector.kind & 5 == 5:
                            result = self._follow_portal_chain(room_index, room, column, row, x, z,
                                                               sector, original_room)
                            return result
                    column += 1
            row += 1
        return _NO_ROOM_LINK

    def _follow_portal_chain(self, room_index, room, column, row, x, z, sector, original_room):
        """Follow a stack of ceiling portals to the room that holds the real geometry.

        Returns that room's number, or ``_NO_ROOM_LINK`` when the chain ends or a closed opening
        between rooms of the same medium blocks it.
        """
        rooms = self.project.rooms
        current = room
        current_index = room_index
        while True:
            doors = self._door_at(current_index, column, row)
            door = doors[0][1]
            if door.type != TYPE_DOOR_CEILING:
                door = doors[1][1]
            next_index = self._partner_room(door)
            if (not sector.flags & 0x400 and sector.flags & 4
                    and not ((rooms[next_index].flags ^ current.flags) & _ROOM_FLAG_WATER)):
                return _NO_ROOM_LINK
            shift_x = current.position_x - rooms[next_index].position_x
            shift_z = current.position_z - rooms[next_index].position_z
            current_index = next_index
            current = rooms[next_index]
            x, column = x + shift_x, column + shift_x
            z, row = z + shift_z, row + shift_z
            here = current.sector_at(x, z)
            if here.kind == SECTOR_DOOR and not here.flags & 8:
                return self._partner_room(self._door_at(current_index, x, z)[0][1])
            if here.kind not in (SECTOR_WALL, SECTOR_BORDER_WALL) and not here.flags & 8:
                return current_index
            sector = current.sector_at(column, row)
            if sector.kind & 5 != 5:
                return _NO_ROOM_LINK

    # ---------------------------------------------------------------------- room table
    def write_room_tables(self, output, room_index):
        """Write the game sector table followed by the editor sector table of a room."""
        room = self.project.rooms[room_index]
        self.project.current_room = room_index
        output += struct.pack('<hh', room.size_x, room.size_z)
        for z in range(room.size_z):
            for x in range(room.size_x):
                output += self._game_sector(room_index, room, x, z)
        output += struct.pack('<hh', room.size_x, room.size_z)
        self._accumulated_type = self._type_residue
        for z in range(room.size_z):
            for x in range(room.size_x):
                output += self._editor_sector(room_index, room, x, z)

    # ---------------------------------------------------------------------- game sector
    def _game_sector(self, room_index, room, x, z):
        """Build the 8-byte game record of a sector and its floor data functions.

        The record holds the floor data index, material and box number, and floor and ceiling
        heights whose low bytes carry the numbers of the rooms below and above.
        """
        sector = room.sector_at(x, z)
        words = _FunctionWords()
        c = sector.corners
        highest_floor = max(c[0:4])
        lowest_ceiling = min(c[4:8])
        links = _RoomLinks()

        if highest_floor >= 1:
            self._write_floor_slope(room_index, room, x, z, sector, words, links)
        elif sector.no_collision_flags & _NO_COLLISION_FLOOR:
            sector.no_collision_flags &= 0xFFF9
        if lowest_ceiling < 0:
            self._write_ceiling_slope(room_index, room, x, z, sector, words, links)
        elif sector.no_collision_flags & _NO_COLLISION_CEILING:
            sector.no_collision_flags &= 0xFFE7

        added = self._height_addition(room, x, z)
        floor_value = to_int16(sector.floor + highest_floor + added) * -0x100
        ceiling_value = to_int16(sector.ceiling + lowest_ceiling) * -0x100
        self._write_portal_words(room_index, room, x, z, sector, words)
        self._last_sector_is_blocked = False
        if self._is_solid(sector):
            target = self._find_wall_portal_room(room_index, x, z)
            if target == _NO_ROOM_LINK:
                floor_value = _NO_BLOCK_VALUE
                ceiling_value = _NO_BLOCK_VALUE
                self._last_sector_is_blocked = True
            else:
                words.add(self.floor_data, _FD_PORTAL, self.room_numbers[target])
        self._write_simple_flags(sector, words)
        if sector.flags & FLAG_TRIGGER:
            self._write_triggers(room, x, z, sector, words)
        self._resolve_vertical_links(room, x, z, sector, links)

        low_byte_floor = (floor_value & ~0xFF) + links.below
        low_byte_ceiling = (ceiling_value & ~0xFF) + links.above
        self._remember_stack_residue(sector, highest_floor, lowest_ceiling, added)
        material = self._material_of(sector)
        record = struct.pack('<HHHH', words.first & 0xFFFF,
                             (material | (sector.box << 4)) & 0xFFFF,
                             low_byte_floor & 0xFFFF, low_byte_ceiling & 0xFFFF)
        if words.last:
            self.floor_data[words.last] |= _END_BIT
        return record

    def _is_solid(self, sector):
        """Tell whether a sector is a wall, or has its wall portal closed."""
        return sector.kind in (SECTOR_BORDER_WALL, SECTOR_WALL) or sector.flags & 8

    def _remember_stack_residue(self, sector, highest_floor, lowest_ceiling, added):
        """Record what the next sector table will find in the variable it does not initialise.

        The editor sector table starts from an uninitialised 16-bit word, which holds the upper
        half of the height value computed for the previous sector.  That is reproduced here so the
        first ``sector_type`` value of a room is the same as in the editor's files.
        """
        if self._is_solid(sector) and sector.kind != SECTOR_DOOR:
            if self._last_sector_is_blocked:
                self._type_residue = 0xFFFF
                return
        floor_part = (sector.floor + highest_floor + added) & 0xFFFF
        ceiling_part = (sector.ceiling + lowest_ceiling) & 0xFFFF
        negated = (-floor_part) & 0xFFFFFFFF
        high_half = (negated >> 8) & 0xFFFF
        product = (((high_half << 16) | ceiling_part) * -0x100) & 0xFFFFFFFF
        self._type_residue = product >> 16

    def _write_portal_words(self, room_index, room, x, z, sector, words):
        """Add the portal function for a sector inside a wall door."""
        if sector.kind == SECTOR_DOOR and (not sector.flags & 8 or sector.flags & 0x1000):
            door = self._door_at(room_index, x, z)[0][1]
            words.add(self.floor_data, _FD_PORTAL, self.room_numbers[self._partner_room(door)])

    def _write_simple_flags(self, sector, words):
        """Add the functions without parameters: death, climb, monkey swing and minecart turns."""
        if sector.flags & 0x10:
            words.add_word(self.floor_data, _FD_DEATH)
        if sector.flags & 0x3C0:
            words.add_word(self.floor_data, (sector.flags & 0x3C0) * 4 + _FD_CLIMB)
        if sector.flags & FLAG_MONKEY:
            words.add_word(self.floor_data, _FD_MONKEY)
        if sector.no_collision_flags & 0x20:
            words.add_word(self.floor_data, _FD_MINECART_LEFT)
        if sector.no_collision_flags & 0x40:
            words.add_word(self.floor_data, _FD_MINECART_RIGHT)

    def _material_of(self, sector):
        """Return the footstep material of the floor polygons of a sector, 6 if undetermined."""
        faces = sector.faces
        tiles = self.project.texture_tiles
        first = 0xFF
        if faces[0].kind == 7:
            first = self._cell_material(tiles[faces[0].texture & 0x3FF])
        second = 0xFF
        if faces[8].kind == 7:
            second = self._cell_material(tiles[faces[8].texture & 0x3FF])
        if first == 0xFF:
            return 6 if second == 0xFF else second
        if second == 0xFF or second <= first:
            return first
        return second

    def _cell_material(self, tile):
        """Return the footstep material of the texture cell a tile lies in."""
        row, column = tile[0] >> 8, tile[0] & 0xFF
        return self.project.footstep_table[(row >> 6) * 4 + (column >> 6)]

    def _write_floor_slope(self, room_index, room, x, z, sector, words, links):
        """Add the slope function of a floor whose corners differ.

        A flat tilted floor uses the simple slope function.  Otherwise the floor consists of two
        triangles, and a function holding the split direction and the corner heights is written.
        """
        c = sector.corners
        heights = [(0, c[0], 0), (10, c[1], 0), (10, c[2], 10), (0, c[3], 10)]
        if face_is_planar(*heights):
            words.note_start(self.floor_data)
            words.last = len(self.floor_data)
            self.floor_data.append(_FD_FLOOR_SLOPE)
            self.floor_data.append((c[0] - c[1] & 0xFF) + ((c[2] - c[1]) * 0x100) & 0xFFFF)
            if sector.no_collision_flags & _NO_COLLISION_FLOOR:
                sector.no_collision_flags &= 0xFFF9
            return
        kind, first_delta, second_delta = _floor_triangulation(c, sector.split_flags)
        words.note_start(self.floor_data)
        words.last = len(self.floor_data)
        if sector.no_collision_flags & _NO_COLLISION_FLOOR:
            kind = self._no_collision_kind(room_index, room, x, z, sector, kind, links, floor=True)
        self.floor_data.append((((first_delta & 0x1F) << 5 | (second_delta & 0x1F)) << 5
                                | kind) & 0xFFFF)
        self.floor_data.append((((c[2] & 0xF) | (c[3] << 4)) << 4 | (c[1] & 0xF)) << 4
                               | (c[0] & 0xF) & 0xFFFF)

    def _write_ceiling_slope(self, room_index, room, x, z, sector, words, links):
        """Add the slope function of a ceiling, in the same way as for a floor."""
        c = sector.corners
        heights = [(0, c[4], 0), (10, c[5], 0), (10, c[6], 10), (0, c[7], 10)]
        if face_is_planar(*heights):
            words.note_start(self.floor_data)
            words.last = len(self.floor_data)
            self.floor_data.append(_FD_CEILING_SLOPE)
            self.floor_data.append((c[4] - c[5] & 0xFF) + ((c[6] - c[5]) * 0x100) & 0xFFFF)
            if sector.no_collision_flags & _NO_COLLISION_CEILING:
                sector.no_collision_flags &= 0xFFE7
            return
        kind, first_delta, second_delta = _ceiling_triangulation(c)
        words.note_start(self.floor_data)
        words.last = len(self.floor_data)
        if sector.no_collision_flags & _NO_COLLISION_CEILING:
            kind = self._no_collision_kind(room_index, room, x, z, sector, kind, links, floor=False)
        self.floor_data.append((((first_delta & 0x1F) << 5 | (second_delta & 0x1F)) << 5
                                | kind) & 0xFFFF)
        self.floor_data.append(((abs(c[6]) & 0xF | abs(c[7]) << 4) << 4 | abs(c[5]) & 0xF) << 4
                               | abs(c[4]) & 0xF)

    def _no_collision_kind(self, room_index, room, x, z, sector, kind, links, floor):
        """Return the triangulation kind, changed if half of the sector is a portal.

        A sector with a no-collision triangle has a door on one half only; the room link is set
        here.
        """
        door_type = TYPE_DOOR_FLOOR if floor else TYPE_DOOR_CEILING
        wanted_bits = _NO_COLLISION_FLOOR if floor else _NO_COLLISION_CEILING
        for object_id, door in self._door_at(room_index, x, z):
            if door.type == door_type:
                if sector.no_collision_flags & wanted_bits != wanted_bits:
                    partner_room = self.project.objects[door.link].room
                    if floor:
                        links.below = self.room_numbers[partner_room]
                    else:
                        links.above = self.room_numbers[partner_room]
                    return self._diagonal_with_portal(sector, kind, floor)
                break
        return kind

    @staticmethod
    def _diagonal_with_portal(sector, kind, floor):
        """Translate a triangulation kind to its variant with a portal triangle."""
        if floor:
            return ((1 if sector.no_collision_flags & 2 else 0) + 0xB) if kind == 7 \
                else ((1 if sector.no_collision_flags & 2 else 0) + 0xD)
        return ((1 if sector.no_collision_flags & 8 else 0) + 0xF) if kind == 9 \
            else ((1 if sector.no_collision_flags & 8 else 0) + 0x11)

    def _write_triggers(self, room, x, z, sector, words):
        """Add the trigger function of a sector, followed by one action per trigger object.

        The header word comes from the first trigger that has a type, timer or flags.  The list of
        actions starts with that trigger and wraps around.
        """
        floor_data = self.floor_data
        triggers = self._triggers_at(room, x, z)
        if not triggers:
            sector.flags -= 1
            return
        project = self.project
        words.note_start(floor_data)
        header = len(floor_data)
        words.last = header
        floor_data.append(_FD_TRIGGER)
        floor_data.append(0x3E00)
        first_index = header
        # The list of actions starts with the trigger that supplies the setup word.
        action_start = 0
        for position, placed in enumerate(triggers):
            trigger = project.triggers[placed.link]
            target_is_camera = (trigger.target_kind == 0
                                and object_class(project.objects[trigger.target].type)
                                == CLASS_CAMERA)
            if target_is_camera:
                if trigger.trigger_type:
                    floor_data[first_index] = trigger.trigger_type * 0x100 + _FD_TRIGGER
                    action_start = position
                    break
            elif trigger.trigger_type or trigger.timer or trigger.flags:
                floor_data[first_index] = trigger.trigger_type * 0x100 + _FD_TRIGGER
                floor_data[first_index + 1] = ((trigger.flags ^ 0x3E) * 0x100
                                               + (trigger.timer & 0xFF))
                action_start = position
                break
        for placed in triggers[action_start:] + triggers[:action_start]:
            self._write_action_word(placed, room, x, z)
        floor_data[-1] |= _END_BIT

    def _write_action_word(self, placed, room, x, z):
        """Add the action word (and the extra word, if any) of a trigger.

        Cameras and flyby cameras take a second word with timer and flags.  A trigger of the CD
        track type also adds a line to the ``.CD`` file.
        """
        project = self.project
        floor_data = self.floor_data
        trigger = project.triggers[placed.link]
        kind = trigger.target_kind
        if kind == 0:
            target = trigger.target
            target_object = project.objects[target]
            if target_object.type == TYPE_CAMERA_FLYBY:
                index = next((i for i,
                              flyby in enumerate(self.flyby_cameras) if flyby.object_id == target),
                             len(self.flyby_cameras))
                floor_data.append(self.flyby_cameras[index].sequence | 0x3000)
                floor_data.append(trigger.flags * 0x100 + (trigger.timer & 0xFF))
            else:
                word = self.object_numbers[target]
                target_class = object_class(target_object.type)
                if target_class == CLASS_CAMERA:
                    floor_data.append((word + 0x400) & 0xFFFF)
                    floor_data.append(trigger.flags * 0x100 + (trigger.timer & 0xFF))
                elif target_class == CLASS_SINK:
                    floor_data.append((word + 0x800) & 0xFFFF)
                else:
                    floor_data.append(word & 0xFFFF)
        elif kind == _TRIGGER_NEEDS_TARGET:
            floor_data.append((self.object_numbers[trigger.target] + 0x1800) & 0xFFFF)
        else:
            if kind == _TRIGGER_TYPE_CAMERA_LIKE:
                self.cd_lines.append(f'Room:{project.current_room} X:{x} Y:{z} '
                                     f'Track:{trigger.target}\n')
            floor_data.append((trigger.target + kind * 0x400) & 0xFFFF)

    def _resolve_vertical_links(self, room, x, z, sector, links):
        """Find the rooms below and above a sector with vertical portals."""
        kind = sector.kind
        if kind == 3:
            if not sector.no_collision_flags & _NO_COLLISION_FLOOR:
                links.below = _NO_ROOM_LINK
                self._link_below(room, x, z, sector, links)
        elif kind == 5:
            if not sector.no_collision_flags & _NO_COLLISION_CEILING:
                links.above = _NO_ROOM_LINK
                self._link_above(room, x, z, sector, links)
        elif kind == 7:
            self._link_both(room, x, z, sector, links)

    def _vertical_door(self, room, x, z, wanted_type):
        """Return the door of the wanted type in this sector, or the first door if there is none."""
        doors = self._door_at(self.project.current_room, x, z)
        if doors[0][1].type != wanted_type and len(doors) > 1:
            return doors[1][1]
        return doors[0][1]

    def _link_below(self, room, x, z, sector, links):
        """Set the room below; a closed opening between rooms of the same medium gives no link."""
        door = self._vertical_door(room, x, z, TYPE_DOOR_FLOOR)
        target = self._partner_room(door)
        same_water = not ((self.project.rooms[target].flags ^ room.flags) & _ROOM_FLAG_WATER)
        if not sector.flags & 0x800 and sector.flags & 2 and same_water:
            links.below = _NO_ROOM_LINK
        else:
            links.below = self.room_numbers[target]

    def _link_above(self, room, x, z, sector, links):
        """Set the room above; a closed opening between rooms of the same medium gives no link."""
        door = self._vertical_door(room, x, z, TYPE_DOOR_CEILING)
        target = self._partner_room(door)
        same_water = not ((self.project.rooms[target].flags ^ room.flags) & _ROOM_FLAG_WATER)
        if not sector.flags & 0x400 and sector.flags & 4 and same_water:
            links.above = _NO_ROOM_LINK
        else:
            links.above = self.room_numbers[target]

    def _link_both(self, room, x, z, sector, links):
        """Set both vertical links of a sector with a floor and a ceiling portal."""
        doors = self._door_at(self.project.current_room, x, z)
        first, second = doors[0][1], doors[1][1]
        if second.type == TYPE_DOOR_CEILING:
            above_door, below_door = second, first
        else:
            below_door, above_door = second, first
        no_collision = sector.no_collision_flags
        if not no_collision & _NO_COLLISION_CEILING:
            links.above = self.project.objects[above_door.link].room
        if not no_collision & _NO_COLLISION_FLOOR:
            links.below = self.project.objects[below_door.link].room
        if not no_collision & _NO_COLLISION_FLOOR:
            links.below = self._translated_link(room, sector, links.below, sector.flags & 0x800,
                                                sector.flags & 2)
        if not no_collision & _NO_COLLISION_CEILING:
            links.above = self._translated_link(room, sector, links.above, sector.flags & 0x400,
                                                sector.flags & 4)

    def _translated_link(self, room, sector, target, traversable_flag, portal_flag):
        """Return the final number of a linked room, or no link if the opening is closed."""
        same_water = not ((self.project.rooms[target].flags ^ room.flags) & _ROOM_FLAG_WATER)
        if not traversable_flag and portal_flag and same_water:
            return _NO_ROOM_LINK
        return self.room_numbers[target]

    # ---------------------------------------------------------------------- editor sector
    def _editor_sector(self, room_index, room, x, z):
        """Build the 14-byte editor record of a sector.

        The third word is the OR of the sector kinds so far: the original never resets the variable
        it collects them in.
        """
        sector = room.sector_at(x, z)
        c = sector.corners
        output = bytearray()
        output += struct.pack('<H', ((c[2] - c[1]) * 0x100 | (c[0] - c[1]) & 0xFF) & 0xFFFF)
        ceiling_word = ((c[6] & 0xF | (c[7] << 4)) << 4 | c[5] & 0xF) << 4 | c[4] & 0xF
        output += struct.pack('<H', ceiling_word & 0xFFFF)
        highest_floor = max(c[0:4])
        lowest_ceiling = min(c[4:8])
        added = self._height_addition(room, x, z)
        floor_value = (sector.floor + highest_floor + added) * -0x100
        ceiling_value = (sector.ceiling + lowest_ceiling) * -0x100
        if self._is_solid(sector) and self._find_wall_portal_room(room_index, x,
                                                                  z) == _NO_ROOM_LINK:
            floor_value = ceiling_value = _NO_BLOCK_VALUE
        links = _RoomLinks(room_below=_NO_ROOM_LINK, room_above=_NO_ROOM_LINK)
        self._resolve_editor_links(room, x, z, sector, links)
        self._accumulated_type |= sector.kind
        output += struct.pack('<H', self._accumulated_type & 0xFFFF)
        output += struct.pack('<HHHH', floor_value & 0xFFFF, ceiling_value & 0xFFFF,
                              links.below & 0xFFFF, links.above & 0xFFFF)
        return bytes(output)

    def _resolve_editor_links(self, room, x, z, sector, links):
        """Find the vertical links as stored in the editor record."""
        kind = sector.kind
        if kind == 3:
            self._link_below_editor(room, x, z, sector, links)
        elif kind == 5:
            self._link_above_editor(room, x, z, sector, links)
        elif kind == 7:
            doors = self._door_at(self.project.current_room, x, z)
            first, second = doors[0][1], doors[1][1]
            if second.type == TYPE_DOOR_CEILING:
                above_door, below_door = second, first
            else:
                below_door, above_door = second, first
            links.below = self._editor_translate(room, sector, below_door, 2)
            links.above = self._editor_translate(room, sector, above_door, 4)

    def _link_below_editor(self, room, x, z, sector, links):
        """Set the room below in the editor record."""
        door = self._vertical_door(room, x, z, TYPE_DOOR_FLOOR)
        links.below = self._editor_translate(room, sector, door, 2)

    def _link_above_editor(self, room, x, z, sector, links):
        """Set the room above in the editor record."""
        door = self._vertical_door(room, x, z, TYPE_DOOR_CEILING)
        links.above = self._editor_translate(room, sector, door, 4)

    def _editor_translate(self, room, sector, door, flag):
        """Return the room number behind a door, or no link if the opening is closed."""
        target = self._partner_room(door)
        same_water = not ((self.project.rooms[target].flags ^ room.flags) & _ROOM_FLAG_WATER)
        if not sector.flags & flag or not same_water:
            return self.room_numbers[target]
        return _NO_ROOM_LINK


class _FunctionWords:
    """Tracks where a sector's function list starts and which word was added last."""

    def __init__(self):
        """Start with no functions: index 0 stands for "none"."""
        self.first = 0
        self.last = 0

    def note_start(self, floor_data):
        """Remember where this sector's list of functions begins."""
        if self.first == 0:
            self.first = len(floor_data)

    def add(self, floor_data, function, value):
        """Append a function with a parameter word."""
        self.note_start(floor_data)
        self.last = len(floor_data)
        floor_data.append(function)
        floor_data.append(value & 0xFFFF)

    def add_word(self, floor_data, value):
        """Append a function consisting of a single word."""
        self.note_start(floor_data)
        self.last = len(floor_data)
        floor_data.append(value & 0xFFFF)


class _RoomLinks:
    """The numbers of the rooms below and above a sector."""
    def __init__(self, room_below=_NO_ROOM_LINK, room_above=_NO_ROOM_LINK):
        """Create the links; by default there is no room in either direction."""
        self.below = room_below
        self.above = room_above


def _triangle_type_floor(c, split_toggled):
    """Choose the diagonal of a floor; 7 and 8 are the plain cases, 9 and 10 the steep ones."""
    c0, c1, c2, c3 = c[0], c[1], c[2], c[3]
    if c0 == c2:
        if not split_toggled:
            if c2 <= c1 or c0 <= c1:
                return 7 if (c0 <= c3 or c2 <= c3) else 9
            return 9
        if c0 < c1 and c0 < c3:
            return 10
        if c2 < c1 and c2 < c3:
            return 10
        return 8
    if c1 == c3:
        if split_toggled:
            return _steep_side(c)
        return _flat_side_when_not_split(c)
    if ((c0 < c1 and c0 < c3) or (c2 < c1 and c2 < c3)
            or (c1 < c0 and c3 < c0) or (c1 < c2 and c3 < c2)):
        if split_toggled:
            return _steep_side(c)
        return _flat_side_when_not_split(c)
    if split_toggled:
        return _flat_side_when_not_split(c)
    return _steep_side(c)


def _flat_side_when_not_split(c):
    """Choose the triangulation kind of a floor split along its other diagonal."""
    c0, c1, c2, c3 = c[0], c[1], c[2], c[3]
    if c1 <= c0 or c3 <= c0:
        if c2 < c1 and c2 < c3:
            return 10
        return 8
    return 10


def _steep_side(c):
    """Choose the triangulation kind from the corner that sticks out."""
    c0, c1, c2, c3 = c[0], c[1], c[2], c[3]
    if c2 <= c1 or c0 <= c1:
        return 7 if c0 <= c3 or c2 <= c3 else 9
    return 9


def _floor_triangulation(c, split_flags):
    """Return the floor data function and the two height differences of the triangles."""
    kind = _triangle_type_floor(c, bool(split_flags & 1))
    a, b, d, e = c[0], c[1], c[2], c[3]
    first, second = 0, 0
    top = max(a, b, d, e)
    if kind == 7:
        if a < e and b < e and d < e:
            first = e - max(a, b, d)
        if a < b and d < b and e < b:
            second = b - max(a, d, e)
    elif kind == 8:
        if b < a and d < a and e < a:
            first = a - max(b, d, e)
        if a < d and b < d and e < d:
            second = d - max(a, b, e)
    elif kind == 9:
        kind = 7
        if a != b and b != d:
            pivot = (d - b) + a
            if top < pivot or top == e:
                first = top - pivot
        if a != e and e != d:
            pivot = (d - e) + a
            if pivot > top or top == b:
                second = top - pivot
    elif kind == 10:
        kind = 8
        if b != d and d != e:
            pivot = (e - d) + b
            if top < pivot or top == a:
                first = top - pivot
        if a != b and a != e:
            pivot = (e - a) + b
            if pivot > top or top == d:
                second = top - pivot
    return kind, first, second


def _ceiling_triangulation(c):
    """Return the ceiling's function word and the height differences of its two triangles."""
    c4, c5, c6, c7 = c[4], c[5], c[6], c[7]
    if c5 == c7:
        kind = 9
    elif c4 == c6:
        kind = 10
    elif ((c4 <= c5 or c4 <= c7) and (c6 <= c5 or c6 <= c7)
          and (c5 <= c4 or c7 <= c4) and (c5 <= c6 or c7 <= c6)):
        kind = 9
    else:
        kind = 10
    a, b, d, e = -c4, -c5, -c6, -c7
    top = max(a, b, d, e)
    first, second = 0, 0
    if kind == 9:
        first = max(e + c6 + b, b, d, e) - top
        second = max(a, b, e + c4 + b, e) - top
    elif kind == 10:
        first = max(a, b, d, d + c5 + a) - top
        second = max(a, d + c7 + a, d, e) - top
    return kind, first, second
