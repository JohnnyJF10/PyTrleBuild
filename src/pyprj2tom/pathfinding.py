"""Enemy pathfinding data: boxes, overlaps and zones.

*Boxes* are rectangular walkable areas with one height.  Each sector stores the box it belongs to.
*Overlaps* list, for each box, the boxes that a creature can reach from it directly.
*Zones* are numbered groups of boxes connected for a particular kind of creature.

All coordinates here are global sector numbers: room position plus sector number in the room.
Boxes are built twice, once for the normal state of flip rooms and once for the flipped state.
"""
from dataclasses import dataclass

from trle_common.c_arithmetic import to_int16

from pyprj2tom.project_model import (FLAG_BLOCK, FLAG_MONKEY, FLAG_WALL_PORTAL, FLAG_WALL_TRAVERSABLE,
                                   NO_ROOM, SECTOR_BORDER_WALL, SECTOR_DOOR, SECTOR_WALL,
                                   TYPE_DOOR_CEILING, TYPE_DOOR_FLOOR)

NO_BOX = 0x7FF
NO_HEIGHT = 0x7FFF
MAX_BOXES = 0x7F8
MAX_OVERLAPS = 15000
_DIRECTION_NEGATIVE_X, _DIRECTION_POSITIVE_X = 1, 2
_DIRECTION_NEGATIVE_Z, _DIRECTION_POSITIVE_Z = 4, 8
_ALL_DIRECTIONS = 0xF
_FLAG_NORMAL_STATE, _FLAG_FLIPPED_STATE = 4, 2
_MONKEY_BIT = 0x2000
_BLOCKED_BIT = 0x8000
_ROOM_FLAG_WATER = 1
_OVERLAP_END_BIT = 0x80
_OVERLAP_JUMP_BIT = 8
_OVERLAP_MONKEY_BIT = 0x20
_FLAG_UNREACHABLE_KIND = 0x8000


@dataclass
class Box:
    """A rectangular walkable area; positions are in sectors until the boxes are finished."""
    x_min: int
    x_max: int
    z_min: int
    z_max: int
    height: int
    overlap_index: int
    flags: int
    room: int


class PathfindingBuilder:
    """Builds the boxes and overlaps of a project."""

    def __init__(self, project, compiler):
        """Start with no boxes.

        Several values describe the most recent height lookup, which walks through portals: the room
        it ended in, the blocked, water and monkey-swing state, and the jump and monkey result of
        the last overlap test.
        """
        self.project = project
        self.compiler = compiler
        self.boxes = []
        self.overlaps = []
        self.current_room = 0
        self.door_cursor = -1
        self.blocked_flag = 0
        self.water_check_pending = 1
        self.monkey_area = 0
        self.door_hop_happened = 0
        self.jump_flag = 0
        self.monkey_flag = 0

    def build(self):
        """Assign a box to every sector, in both flip states, then compute the overlaps."""
        project = self.project
        saved_room = project.current_room
        for _ in range(2):
            for room_index, room in enumerate(project.rooms):
                if room.is_used and not room.flags & 2:
                    self._assign_boxes(room_index, room)
            self.compiler.toggle_flip_state()
        self._compute_overlaps()
        self._finish_boxes()
        project.current_room = saved_room

    def _assign_boxes(self, room_index, room):
        """Build a box for each inner sector of a room; border sectors never get one."""
        for z in range(room.size_z):
            for x in range(room.size_x):
                if (x == 0 or z == 0 or x == room.size_x - 1 or z == room.size_z - 1):
                    box_index = NO_BOX
                else:
                    box = self._make_box(room_index, x, z)
                    box_index = self._store_box(box) if box is not None else NO_BOX
                room.sector_at(x, z).box = box_index

    def _store_box(self, candidate):
        """Add a box unless an identical one exists; return its number."""
        for number, box in enumerate(self.boxes):
            if (box.x_min == candidate.x_min and box.x_max == candidate.x_max
                    and box.z_min == candidate.z_min and box.z_max == candidate.z_max
                    and box.height == candidate.height
                    and not (box.flags ^ candidate.flags) & 1):
                if self.project.flipped:
                    box.flags |= _FLAG_FLIPPED_STATE
                return number
        if len(self.boxes) == MAX_BOXES:
            raise OverflowError('Too many boxes in the level')
        self.boxes.append(candidate)
        return len(self.boxes) - 1

    # ------------------------------------------------------------------ sector queries
    def _room(self):
        """Return the room the current lookup is in."""
        return self.project.rooms[self.current_room]

    def _sector_of(self, global_x, global_z):
        """Return a sector from a level position, with its room coordinates (None if outside)."""
        room = self._room()
        x, z = global_x - room.position_x, global_z - room.position_z
        if not (0 <= x < room.size_x and 0 <= z < room.size_z):
            return None, x, z
        return room.sector_at(x, z), x, z

    def _doors_at(self, room_index, x, z):
        """Return the door objects covering a sector, last door first."""
        project = self.project
        room = project.rooms[room_index]
        matches = []
        for object_id in reversed(room.door_ids):
            door = project.objects[object_id]
            if door.x <= x < door.x + door.size_x and door.z <= z < door.z + door.size_z:
                matches.append(door)
        return matches

    def _partner_room(self, door):
        """Return the number of the room on the other side of a door."""
        return self.project.objects[door.link].room

    def _door_of_type(self, room_index, x, z, door_type):
        """Return the first door at the sector; the second one if the first has another type."""
        doors = self._doors_at(room_index, x, z)
        if not doors:
            return None
        if doors[0].type != door_type and len(doors) > 1:
            return doors[1]
        return doors[0]

    def sector_height(self, global_x, global_z):
        """Return a sector's walking height, or NO_HEIGHT.  May move to the room behind a door."""
        sector, x, z = self._sector_of(global_x, global_z)
        if sector is None or sector.kind & 8:
            return NO_HEIGHT
        flags = sector.flags
        if flags & FLAG_WALL_PORTAL and not flags & FLAG_WALL_TRAVERSABLE:
            return NO_HEIGHT
        if sector.kind == SECTOR_DOOR and (not flags & FLAG_WALL_PORTAL
                                           or flags & FLAG_WALL_TRAVERSABLE):
            doors = self._doors_at(self.current_room, x, z)
            if not doors:
                return NO_HEIGHT
            self.current_room = self._partner_room(doors[0])
            self.door_hop_happened = 1
            sector, x, z = self._sector_of(global_x, global_z)
        origin_room = self.current_room
        sector, x, z = self._descend_through_floors(global_x, global_z, sector, x, z)
        if sector is None:
            return NO_HEIGHT
        return self._height_of_sector(origin_room, sector, x, z, global_x, global_z)

    def _descend_through_floors(self, global_x, global_z, sector, x, z):
        """Follow openings in the floor down to the sector creatures actually walk on."""
        while sector.kind & 3 == 3:
            door = self._door_of_type(self.current_room, x, z, TYPE_DOOR_FLOOR)
            if door is None:
                return None, x, z
            below = self._partner_room(door)
            room = self._room()
            if (not sector.flags & 0x800 and sector.flags & 2
                    and not ((self.project.rooms[below].flags ^ room.flags) & _ROOM_FLAG_WATER)):
                break
            self.current_room = below
            sector, x, z = self._sector_of(global_x, global_z)
        return sector, x, z

    def _height_of_sector(self, origin_room, sector, x, z, global_x, global_z):
        """Return the walking height of a sector, or ``NO_HEIGHT`` if creatures cannot stand on it.

        A sector is too steep if adjacent corners differ by more than two clicks in a pattern that a
        creature cannot walk on.  This also records whether the sector is blocked, under a water
        surface or under a monkey swing.
        """
        c = sector.corners
        room = self._room()
        steep_0 = abs(c[0] - c[1]) > 2
        steep_1 = abs(c[1] - c[2]) > 2
        steep_2 = abs(c[2] - c[3]) > 2
        steep_3 = abs(c[3] - c[0]) > 2
        if (c[0] & 0xFF) == (c[2] & 0xFF):
            other_diagonal = False
        elif (c[1] & 0xFF) == (c[3] & 0xFF):
            other_diagonal = True
        elif ((c[1] <= c[0] or c[3] <= c[0]) and (c[1] <= c[2] or c[3] <= c[2])
              and (c[0] <= c[1] or c[0] <= c[3]) and (c[2] <= c[1] or c[2] <= c[3])):
            other_diagonal = False
        else:
            other_diagonal = True
        height = sector.floor + ((c[2] + c[3] + c[0] + c[1]) >> 2)

        if (self.water_check_pending and room.flags & _ROOM_FLAG_WATER
                and sector.ceiling - height < 2 and sector.kind & 5 == 5):
            door = self._door_of_type(self.current_room, x, z, TYPE_DOOR_CEILING)
            if door is not None:
                above = self.project.rooms[self._partner_room(door)]
                if not above.flags & _ROOM_FLAG_WATER:
                    self.water_check_pending = 0

        self.current_room = origin_room
        steep_count = int(steep_0) + int(steep_1) + int(steep_2) + int(steep_3)
        too_steep = not (steep_count < 3 and not (steep_0 and steep_2) and not (steep_1
                                                                                and steep_3))
        if not too_steep:
            if other_diagonal:
                a, b, d = steep_1, steep_2, steep_3
            else:
                a, b, d = steep_3, steep_1, steep_2
            too_steep = (steep_0 and a) or (b and d)
        if too_steep and self.water_check_pending and not room.flags & _ROOM_FLAG_WATER:
            return NO_HEIGHT
        if sector.flags & FLAG_BLOCK:
            if self.blocked_flag:
                return NO_HEIGHT
            self.blocked_flag = 1
        self.monkey_area = (sector.flags & FLAG_MONKEY) >> 14
        return height

    def _follow_door_at_border(self, global_x, global_z):
        """Move through doors in room-border sectors; returns False when the way is blocked."""
        project = self.project
        while self._is_border(global_x, global_z):
            room = self._room()
            x = _clamp(global_x - room.position_x, 0, room.size_x - 1)
            z = _clamp(global_z - room.position_z, 0, room.size_z - 1)
            sector = room.sector_at(x, z)
            if sector.kind != SECTOR_DOOR:
                break
            doors = self._doors_at(self.current_room, x, z)
            self.current_room = project.objects[doors[0].link].room
            if sector.flags & FLAG_WALL_PORTAL and not sector.flags & FLAG_WALL_TRAVERSABLE:
                return False
        room = self._room()
        sector = self._sector_or_none(room, global_x, global_z)
        while sector is not None and sector.kind & 3 == 3:
            x, z = global_x - room.position_x, global_z - room.position_z
            door = self._door_of_type(self.current_room, x, z, TYPE_DOOR_FLOOR)
            below = project.objects[door.link].room
            if (not sector.flags & 0x800 and sector.flags & 2
                    and not ((project.rooms[below].flags ^ room.flags) & _ROOM_FLAG_WATER)):
                break
            self.current_room = below
            room = self._room()
            sector = self._sector_or_none(room, global_x, global_z)
        return True

    @staticmethod
    def _sector_or_none(room, global_x, global_z):
        """Return a sector by global position; positions outside the room have none."""
        index = (global_z - room.position_z) * room.size_x + global_x - room.position_x
        return room.sectors[index] if 0 <= index < len(room.sectors) else None

    def _is_border(self, global_x, global_z):
        """Tell whether room coordinates lie on the border of the current room."""
        room = self._room()
        x, z = global_x, global_z
        return not (0 < x < room.size_x - 1 and 0 < z < room.size_z - 1)

    # ------------------------------------------------------------------ growing a box
    def _make_box(self, room_index, x, z):
        """Create the largest box containing a sector, or None if it is not walkable.

        Blocked sectors always give a box of exactly one sector.  Others grow as far as possible.
        """
        room = self.project.rooms[room_index]
        sector = room.sector_at(x, z)
        if sector.kind in (SECTOR_WALL, SECTOR_BORDER_WALL):
            return None
        if sector.flags & FLAG_WALL_PORTAL and not sector.flags & FLAG_WALL_TRAVERSABLE:
            return None
        left = x + room.position_x
        far = z + room.position_z
        self.current_room = room_index
        self._follow_door_at_border(left, far)
        self.blocked_flag = 0
        self.water_check_pending = 1
        self.monkey_area = 0
        height = self.sector_height(left, far)
        if height == NO_HEIGHT:
            return None
        box_room = self.current_room
        state_flag = _FLAG_FLIPPED_STATE if self.project.flipped else _FLAG_NORMAL_STATE
        flags = self.project.rooms[box_room].flags | state_flag
        if self.monkey_area:
            flags |= _MONKEY_BIT
        monkey = bool(self.monkey_area)
        if not self.water_check_pending:
            flags -= 1
        if self.blocked_flag:
            return Box(left, left + 1, far, far + 1, height, 0, flags | _BLOCKED_BIT, box_room)
        self.blocked_flag = 1
        return self._grow(left, far, height, flags, box_room, room_index, monkey)

    def _grow(self, x_low, z_low, height, flags, box_room, room_index, monkey):
        """Grow the box one rows at a time in the order -Z, +X, +Z, -X until none can grow."""
        x_high, z_high = x_low, z_low
        open_directions = _ALL_DIRECTIONS
        rooms_at_edges = {_DIRECTION_NEGATIVE_Z: room_index, _DIRECTION_POSITIVE_X: room_index,
                          _DIRECTION_POSITIVE_Z: room_index, _DIRECTION_NEGATIVE_X: room_index}
        while True:
            if open_directions & _DIRECTION_NEGATIVE_Z:
                self.door_hop_happened = 0
                if self._scan_edge(_DIRECTION_NEGATIVE_Z, x_low, x_high, z_low, z_low - 1, height,
                                   monkey,
                                   rooms_at_edges):
                    z_low -= 1
                else:
                    open_directions -= _DIRECTION_NEGATIVE_Z
            if open_directions & _DIRECTION_POSITIVE_X:
                self.door_hop_happened = 0
                if self._scan_edge(_DIRECTION_POSITIVE_X, z_low, z_high, x_high, x_high + 1, height,
                                   monkey,
                                   rooms_at_edges):
                    x_high += 1
                else:
                    open_directions -= _DIRECTION_POSITIVE_X
            if open_directions & _DIRECTION_POSITIVE_Z:
                self.door_hop_happened = 0
                if self._scan_edge(_DIRECTION_POSITIVE_Z, x_high, x_low, z_high, z_high + 1, height,
                                   monkey,
                                   rooms_at_edges):
                    z_high += 1
                else:
                    open_directions -= _DIRECTION_POSITIVE_Z
            if open_directions & _DIRECTION_NEGATIVE_X:
                self.door_hop_happened = 0
                if self._scan_edge(_DIRECTION_NEGATIVE_X, z_high, z_low, x_low, x_low - 1, height,
                                   monkey,
                                   rooms_at_edges):
                    x_low -= 1
                else:
                    open_directions -= _DIRECTION_NEGATIVE_X
            if open_directions == 0:
                return Box(x_low, x_high + 1, z_low, z_high + 1, height, NO_BOX, flags, box_room)

    def _sample(self, a, b, direction):
        """Height of a sector, with ``a``/``b`` being its coordinates along and across the edge."""
        if direction in (_DIRECTION_NEGATIVE_Z, _DIRECTION_POSITIVE_Z):
            return self.sector_height(a, b)
        return self.sector_height(b, a)

    def _scan_edge(self, direction, start, end, base, target, height, monkey, rooms_at_edges):
        """Test one row beside the box; returns True if the box can be extended over it."""
        step = 1 if end >= start else -1
        project = self.project
        starting_room = rooms_at_edges[direction]
        self.current_room = starting_room
        along = start
        first = True
        while (step > 0 and along <= end) or (step < 0 and along >= end):
            if self._sample(along, base, direction) != height or \
                    self._sample(along, target, direction) != height or self.monkey_area != monkey:
                return False
            if first:
                rooms_at_edges[direction] = self.current_room
                first = False
            if self.door_hop_happened:
                room_changed = self.current_room != starting_room
                unconnected = (project.rooms[self.current_room].flip_room == NO_ROOM
                               and project.rooms[starting_room].flip_room == NO_ROOM)
                if room_changed and not unconnected:
                    return False
                self.current_room = starting_room
                if self._sample(along, target, direction) != height:
                    return False
                self.door_hop_happened = 0
            along += step
        return True

    # ------------------------------------------------------------------ overlaps
    def _compute_overlaps(self):
        """Find for every box the boxes that creatures can move to.

        Each box is tested in the flip states it exists in.  The last entry of a box's list is
        marked.
        """
        project = self.project
        boxes = self.boxes
        for number, box in enumerate(boxes):
            box.overlap_index = NO_BOX
            if box.flags & _FLAG_NORMAL_STATE:
                if project.flipped:
                    self.compiler.toggle_flip_state()
                for other_number, other in enumerate(boxes):
                    if other_number != number and other.flags & _FLAG_NORMAL_STATE:
                        self._add_overlap_if_passable(number, box, other_number, other)
            if box.flags & _FLAG_FLIPPED_STATE:
                if not project.flipped:
                    self.compiler.toggle_flip_state()
                for other_number, other in enumerate(boxes):
                    if other_number != number and other.flags & _FLAG_FLIPPED_STATE \
                            and not (other.flags & box.flags & _FLAG_NORMAL_STATE):
                        self._add_overlap_if_passable(number, box, other_number, other)
            if self.overlaps:
                self.overlaps[-1] |= _OVERLAP_END_BIT << 8
        if project.flipped:
            self.compiler.toggle_flip_state()

    def _add_overlap_if_passable(self, number, box, other_number, other):
        """Add ``other`` to the overlaps of ``box`` if the two are connected."""
        if not self._boxes_connect(box, other):
            return
        if len(self.overlaps) == MAX_OVERLAPS:
            raise OverflowError('Too many overlaps in the level')
        if box.overlap_index == NO_BOX:
            box.overlap_index = len(self.overlaps)
        value = other_number
        if self.jump_flag:
            value |= _OVERLAP_JUMP_BIT << 8
        if self.monkey_flag:
            value |= _OVERLAP_MONKEY_BIT << 8
        self.overlaps.append(value)

    def _boxes_connect(self, first, second):
        """Decide whether two boxes can be reached from each other; sets the jump and monkey marks.
        """
        self.jump_flag = 0
        self.monkey_flag = 0
        high, low = (second, first) if first.height < second.height else (first, second)
        if low.x_min < high.x_max and high.x_min < low.x_max:
            if low.z_min < high.z_max and high.z_min < low.z_max:
                if high.height == low.height:
                    return self._confirm_connection(high, low)
                return False
            if self._jump_along_z(low, high):
                self.jump_flag = 1
                return True
            if low.z_min <= high.z_max and high.z_min <= low.z_max:
                if high.z_max == low.z_min and not self._edge_at_high_z(high, low):
                    return False
                if high.z_min == low.z_max and not self._edge_at_low_z(high, low):
                    return False
                return self._confirm_connection(high, low)
            return False
        if low.z_min < high.z_max and high.z_min < low.z_max and self._jump_along_x(high, low):
            self.jump_flag = 1
            return True
        if (low.x_min <= high.x_max and high.x_min <= low.x_max
                and low.z_min < high.z_max and high.z_min < low.z_max):
            if high.x_max == low.x_min and not self._edge_at_high_x(high, low):
                return False
            if high.x_min == low.x_max and not self._edge_at_low_x(high, low):
                return False
            return self._confirm_connection(high, low)
        return False

    def _confirm_connection(self, high, low):
        """Accept a connection and note whether it is a monkey swing connection."""
        self.monkey_flag = int(bool(high.flags & low.flags & _MONKEY_BIT))
        return True

    def _edge_at_high_z(self, box, other):
        """Check every sector along the far Z edge of a box against the other box."""
        return self._edge_scan(box, other, box.x_min, box.x_max, other.x_min, other.x_max,
                               lambda along: (along, box.z_max - 1), lambda along: (along,
                                                                                    box.z_max))

    def _edge_at_low_z(self, box, other):
        """Check that the boxes join along the low Z edge of ``box``."""
        return self._edge_scan(box, other, box.x_min, box.x_max, other.x_min, other.x_max,
                               lambda along: (along, box.z_min), lambda along: (along,
                                                                                box.z_min - 1))

    def _edge_at_high_x(self, box, other):
        """Check that the boxes join along the high X edge of ``box``."""
        return self._edge_scan(box, other, box.z_min, box.z_max, other.z_min, other.z_max,
                               lambda along: (box.x_max - 1, along), lambda along: (box.x_max,
                                                                                    along))

    def _edge_at_low_x(self, box, other):
        """Check that the boxes join along the low X edge of ``box``."""
        return self._edge_scan(box, other, box.z_min, box.z_max, other.z_min, other.z_max,
                               lambda along: (box.x_min, along), lambda along: (box.x_min - 1,
                                                                                along))

    def _edge_scan(self, box, other, low_a, high_a, low_b, high_b, inside, beyond):
        """Walk along the shared part of an edge and compare the heights on both sides.

        ``inside`` and ``beyond`` turn a position along the edge into the sector on each side.
        """
        start = max(low_a, low_b)
        end = min(high_a, high_b)
        for along in range(start, end):
            self.current_room = box.room
            if not self._follow_door_at_border(*inside(along)):
                return False
            self.blocked_flag = 0
            if self.sector_height(*beyond(along)) != other.height:
                return False
        return True

    def _jump_along_z(self, first, second):
        """Test for a jump across a one or two sector gap between boxes in the Z direction."""
        if first.height != second.height:
            return False
        middle = (max(first.x_min, second.x_min) + min(first.x_max, second.x_max)) >> 1
        height = second.height
        at = second.z_max
        if at == first.z_min - 1:
            self.current_room = second.room
            return self._landing_clear(middle, at - 1, at, height, along_x=True)
        if at == first.z_min - 2:
            self.current_room = second.room
            return self._wide_gap_clear(middle, at, height, along_x=True)
        at = first.z_max
        if second.z_min == at + 1:
            self.current_room = first.room
            return self._single_gap_clear(middle, at, height, along_x=True)
        if second.z_min == at + 2:
            self.current_room = first.room
            return self._double_gap_clear(middle, at, height, along_x=True)
        return False

    def _jump_along_x(self, first, second):
        """Test for a jump across a one or two sector gap between boxes in the X direction."""
        if first.height != second.height:
            return False
        middle = (max(first.z_min, second.z_min) + min(first.z_max, second.z_max)) >> 1
        height = second.height
        at = second.x_max
        if at == first.x_min - 1:
            self.current_room = second.room
            return self._landing_clear(middle, at - 1, at, height, along_x=False)
        if at == first.x_min - 2:
            self.current_room = second.room
            return self._wide_gap_clear(middle, at, height, along_x=False)
        at = first.x_max
        if second.x_min == at + 1:
            self.current_room = first.room
            return self._single_gap_clear(middle, at, height, along_x=False)
        if second.x_min == at + 2:
            self.current_room = first.room
            return self._double_gap_clear(middle, at, height, along_x=False)
        return False

    @staticmethod
    def _position(middle, line, along_x):
        """Return the level position for a point given along or across a line."""
        return (middle, line) if along_x else (line, middle)

    def _reachable(self, middle, line, along_x):
        """Tell whether a position can be entered, following doors at the room border."""
        return self._follow_door_at_border(*self._position(middle, line, along_x))

    def _low_gap(self, middle, line, height, along_x):
        """True if the sector at ``line`` is low enough to jump over."""
        value = self.sector_height(*self._position(middle, line, along_x))
        return value <= height - 2 and value != NO_HEIGHT

    def _landing_clear(self, middle, before, line, height, along_x):
        """Check a landing point: the sector before it and the point itself must be open."""
        if not self._reachable(middle, before, along_x):
            return False
        if not self._reachable(middle, line, along_x):
            return False
        return self._low_gap(middle, line, height, along_x)

    def _wide_gap_clear(self, middle, line, height, along_x):
        """Check a gap two sectors wide, with its low ceilings or floors."""
        if not self._reachable(middle, line - 1, along_x) or not self._reachable(middle, line,
                                                                                 along_x):
            return False
        if not self._low_gap(middle, line, height, along_x):
            return False
        if not self._reachable(middle, line + 1, along_x):
            return False
        return self._low_gap(middle, line + 1, height, along_x)

    def _single_gap_clear(self, middle, line, height, along_x):
        """Check a gap of one sector."""
        if not self._reachable(middle, line - 1, along_x) or not self._reachable(middle, line,
                                                                                 along_x):
            return False
        return self._low_gap(middle, line, height, along_x)

    def _double_gap_clear(self, middle, line, height, along_x):
        """Check a gap of two sectors."""
        if not self._single_gap_clear(middle, line, height, along_x):
            return False
        if not self._reachable(middle, line + 1, along_x):
            return False
        return self._low_gap(middle, line + 1, height, along_x)

    def _finish_boxes(self):
        """Convert boxes to world units and mark overlaps of boxes that only some creatures can use.
        """
        for box in self.boxes:
            if box.overlap_index != NO_BOX and box.flags & _FLAG_UNREACHABLE_KIND:
                box.overlap_index |= 0x8000
            box.x_min <<= 10
            box.z_min <<= 10
            box.x_max = box.x_max * 0x400 - 1
            box.z_max = box.z_max * 0x400 - 1
            box.height = to_int16(box.height * -0x100)


def _clamp(value, low, high):
    """Limit a value to a range."""
    return max(low, min(value, high))




def compute_zones(builder, movement_class, state_flag):
    """Flood-fill the boxes reachable by a movement class; returns one zone word per box."""
    boxes = builder.boxes
    step_limit = 0x100 if movement_class in (0, 1,
                                             2) else (0x400 if movement_class == 3 else 0x5000)
    zones = [NO_BOX] * len(boxes)
    zone = 0
    next_in_queue = [NO_BOX] * len(boxes)
    for start, box in enumerate(boxes):
        if (zones[start] != NO_BOX or not (state_flag & box.flags)
                or (box.overlap_index & 0x3FFF) == NO_BOX):
            continue
        zone += 1
        zones[start] = zone
        current = start
        tail = start
        water_state = box.flags & 1
        while True:
            position = boxes[current].overlap_index & 0x3FFF
            while True:
                link = builder.overlaps[position]
                position += 1
                is_last = link & 0x8000 != 0
                other = link & 0x7FF
                if other > len(boxes):
                    break
                if zones[other] == NO_BOX:
                    state_matches = ((state_flag | 1) & boxes[other].flags) == ((water_state & 1)
                                                                                | state_flag)
                    jumps = link & 0x2000 != 0
                    if state_matches or movement_class in (2, 4) or (jumps and movement_class == 3):
                        difference = boxes[other].height - boxes[current].height
                        reachable = (-step_limit <= difference <= step_limit
                                     and (not link & 0x800 or movement_class in (0, 3)))
                        if not reachable and jumps and movement_class == 3:
                            reachable = True
                        if (not reachable and movement_class == 2
                                and (boxes[other].flags & boxes[current].flags & 1)):
                            reachable = True
                        if reachable:
                            zones[other] = zone
                            if next_in_queue[other] == NO_BOX:
                                next_in_queue[tail] = other
                                tail = other
                if is_last:
                    break
            following = next_in_queue[current]
            next_in_queue[current] = NO_BOX
            if following == NO_BOX:
                break
            current = following
    return zones
