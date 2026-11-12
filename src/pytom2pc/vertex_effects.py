"""Water surface effects on room vertices.

Rooms carry no information about the water around them, but vertices do: the game makes them shimmer
or move depending on two attribute bits.  The rules look at whether a floor opening leads into a
water room, and whether the neighbouring room really has a matching vertex.  While working, the room
also receives the single byte the game calls its water scheme.
"""
from dataclasses import dataclass

VERTEX_SHIMMER = 0x4000
VERTEX_WAVE = 0x2000

ROOM_WATER = 0x0001
ROOM_QUICKSAND = 0x0080
ROOM_SHIMMER_ONLY = 0x0100
ROOM_SHIMMER_AT_WATER = 0x0200
_ROOM_WATER_OR_QUICKSAND = ROOM_WATER | ROOM_QUICKSAND

# Indices inside the 16-word door record.
_DOOR_ROOM = 0
_DOOR_NORMAL_Y = 2
_DOOR_MIN_X = 4
_DOOR_MIN_Z = 6
_DOOR_MAX_Z = 9
_DOOR_MAX_X = 10

_NEIGHBOUR_IS_QUICKSAND = 2
_MODE_REPORT_OPENING = 0x100


@dataclass
class WaterScheme:
    """Scratch values for the room being converted."""

    value: int = 0
    strongest_neighbour: int = 0


class VertexEffects:
    """Applies the water rules to one vertex at a time."""

    def __init__(self, rooms):
        """Keep the overview of all rooms."""
        self.rooms = rooms

    def _vertex_exists_in(self, other_room_index, room_index, x, y, z):
        """Tell whether another room has a used vertex at the same world position."""
        room = self.rooms[room_index]
        position = (room.x + x, y, room.z + z)
        return position in self.rooms[other_room_index].used_vertex_positions()

    @staticmethod
    def _door_covers(door, x, z):
        """Tell whether a position lies within a door."""
        return door[_DOOR_MIN_X] <= x <= door[_DOOR_MAX_X] and \
            door[_DOOR_MIN_Z] <= z <= door[_DOOR_MAX_Z]

    def _examine_floor_openings(self, room_index, x, y, z, mode):
        """Inspect openings in the floor above this vertex.

        Returns 0 when the vertex has a counterpart below (so it is not on a surface edge), and
        otherwise a description of the room below: ``(water strength << 4) | 1`` for water,
        ``2`` for quicksand.  In the "report opening" mode any opening simply returns 1.
        """
        rooms = self.rooms
        found = 0
        for door in rooms[room_index].doors:
            neighbour = door[_DOOR_ROOM] & 0xFFFF
            if door[_DOOR_NORMAL_Y] == -1 and self._door_covers(door, x, z):
                flags = rooms[neighbour].flags
                if flags & ROOM_WATER:
                    found = ((rooms[neighbour].water_strength << 4) | 1) & 0xFFFF
                elif flags & ROOM_QUICKSAND:
                    found = _NEIGHBOUR_IS_QUICKSAND
                if self._vertex_exists_in(neighbour, room_index, x, y, z):
                    return 0
        if mode == _MODE_REPORT_OPENING:
            return 1
        if found & 0xF:
            for door in rooms[room_index].doors:
                for second_door in rooms[door[_DOOR_ROOM]].doors:
                    second_neighbour = second_door[_DOOR_ROOM] & 0xFFFF
                    if rooms[second_neighbour].flags & _ROOM_WATER_OR_QUICKSAND:
                        if self._vertex_exists_in(second_neighbour, room_index, x, y, z):
                            return 0
            return found
        return 0

    def _examine_side_openings(self, room_index, x, y, z):
        """Look for a non-horizontal opening to water or quicksand covering this vertex.

        Returns 1 if the neighbouring water room also has this vertex, 2 if a quicksand room
        does, and 0 otherwise.
        """
        rooms = self.rooms
        for door in rooms[room_index].doors:
            neighbour = door[_DOOR_ROOM] & 0xFFFF
            if (rooms[neighbour].flags & _ROOM_WATER_OR_QUICKSAND) and door[_DOOR_NORMAL_Y] != 0:
                if self._door_covers(door, x, z):
                    if self._vertex_exists_in(neighbour, room_index, x, y, z):
                        return 2 - (1 if rooms[neighbour].flags & ROOM_WATER else 0)
                    return 0
        return 0

    def apply(self, room_index, vertex, scheme):
        """Update a vertex ``[x, y, z, light, attributes, light2]`` and the room's water scheme."""
        room = self.rooms[room_index]
        x, y, z = vertex[0:3]
        flags = room.flags

        if flags & ROOM_WATER:
            vertex[4] |= VERTEX_SHIMMER
            scheme.value = 0
            return
        if room.y_bottom != y:
            return  # only vertices at floor level can touch a water surface
        if flags & ROOM_SHIMMER_ONLY:
            if self._examine_floor_openings(room_index, x, y, z, _MODE_REPORT_OPENING) & 0xFFFF:
                vertex[4] |= VERTEX_SHIMMER
                scheme.value = (room.reflection_level + 1) & 0xFF
            return

        result = self._examine_floor_openings(room_index, x, y, z, 0) & 0xFFFF
        if result:
            if result & _NEIGHBOUR_IS_QUICKSAND:
                scheme.value = 0
                vertex[4] |= VERTEX_WAVE
                return
            scheme.strongest_neighbour = max(scheme.strongest_neighbour, result >> 4)
            self._set_scheme_from_neighbour(room, scheme)
            if flags & ROOM_SHIMMER_AT_WATER:
                vertex[4] |= VERTEX_SHIMMER
            vertex[4] |= VERTEX_WAVE
            return

        result = self._examine_side_openings(room_index, x, y, z) & 0xFFFF
        if result:
            if result & 2:
                scheme.value = 0
                return
            self._set_scheme_from_neighbour(room, scheme)
            if flags & ROOM_SHIMMER_AT_WATER:
                vertex[4] |= VERTEX_SHIMMER

    @staticmethod
    def _set_scheme_from_neighbour(room, scheme):
        """Derive the water scheme of a room from its strongest neighbour."""
        strength = (scheme.strongest_neighbour << 2) & 0xFF
        scheme.value = (room.reflection_level + strength + 5) & 0xFF
