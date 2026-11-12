"""Checking a freshly loaded project and repairing what would break the export.

The check is done in three phases, which report to ``error.log`` in the editor:

1. *Doors*: every door must link to another door that links back, and lie inside its room.
2. *Sectors*: each sector type must fit its position in the room, and the heights must be sane.
3. *Objects*: every object may occur only once and must be in the room it claims to be in.

Sound projects are left unchanged.
"""
from pyprj2tom.project_model import (CLASS_DOOR, FLAG_CEILING_PORTAL, FLAG_FLOOR_PORTAL,
                                   FLAG_WALL_PORTAL, MAX_OBJECTS, SECTOR_BORDER_WALL, SECTOR_DOOR,
                                   SECTOR_FLOOR, SECTOR_NONE, SECTOR_WALL, TYPE_DOOR_CEILING,
                                   TYPE_DOOR_FLOOR, TYPE_TRIGGER, object_class)

_PORTAL_FLAGS = (FLAG_WALL_PORTAL, FLAG_CEILING_PORTAL, FLAG_FLOOR_PORTAL)
_FLOOR_KINDS = (1, 3, 5, 7)


def repair_project(project, log):
    """Run all three phases; return the number of problems that were repaired."""
    repairs = _repair_doors(project, log)
    repairs += _repair_sectors(project, log)
    repairs += _repair_objects(project, log)
    if repairs:
        log(f'WARNING: Detected and repaired {repairs} problems in the project')
    return repairs


# ------------------------------------------------------------------------------- doors
def _door_is_valid(project, room_index, object_id):
    """Tell whether a door object is usable: a door in this room whose partner links back."""
    if object_id < 0 or object_id >= MAX_OBJECTS:
        return False
    door = project.objects[object_id]
    if door is None or object_class(door.type) != CLASS_DOOR:
        return False
    partner = project.objects[door.link]
    if partner is None or object_class(partner.type) != CLASS_DOOR:
        return False
    if door.room != room_index and project.rooms[door.room].flip_room != room_index:
        return False
    room = project.rooms[room_index]
    if door.x < 0 or door.z < 0 or room.size_x <= door.x or room.size_z <= door.z:
        return False
    partner_room = project.rooms[partner.room]
    if (partner.x < 0 or partner.z < 0 or partner_room.size_x <= partner.x
            or partner_room.size_z <= partner.z):
        return False
    return partner.link == object_id


def _repair_doors(project, log):
    """Remove the invalid doors of all rooms and return the number of repairs."""
    repairs = 0
    for room_index, room in enumerate(project.rooms):
        if not room.is_used:
            continue
        kept = []
        for object_id in reversed(room.door_ids):
            if _door_is_valid(project, room_index, object_id):
                kept.append(object_id)
            else:
                repairs += 1
                log(f'Room {room_index}: invalid door {object_id} removed')
        kept.reverse()
        room.door_ids = kept
    return repairs


# ------------------------------------------------------------------------------- sectors
def _is_border(room, x, z):
    """Tell whether a sector lies on the border of its room."""
    return x == 0 or z == 0 or x == room.size_x - 1 or z == room.size_z - 1


def _door_at(project, room, x, z):
    """Return the first door covering a sector, or ``None``; later doors in the list win."""
    for object_id in reversed(room.door_ids):
        door = project.objects[object_id]
        if door.x <= x < door.x + door.size_x and door.z <= z < door.z + door.size_z:
            return door
    return None


def _repair_missing_floor(project, room, x, z, sector):
    """A sector without a type becomes a wall, taking its heights from the rooms behind doors."""
    sector.kind = SECTOR_WALL
    for object_id in reversed(room.door_ids):
        door = project.objects[object_id]
        if not (door.x <= x < door.x + door.size_x and door.z <= z < door.z + door.size_z):
            continue
        other = project.rooms[project.objects[door.link].room]
        other_x = room.position_x + x - other.position_x
        other_z = room.position_z + z - other.position_z
        other_sector = other.sector_at(other_x, other_z)
        if door.type == TYPE_DOOR_CEILING:
            sector.ceiling = other_sector.floor
        elif door.type == TYPE_DOOR_FLOOR:
            sector.floor = other_sector.ceiling


def _repair_sector_type(project, room, x, z, sector, log):
    """Check a sector's type against its position; return 1 if something was repaired."""
    kind = sector.kind
    if kind == SECTOR_NONE:
        log(f'No floor {x} {z} in room')
        _repair_missing_floor(project, room, x, z, sector)
        return 1
    if kind in _FLOOR_KINDS:
        if _is_border(room, x, z):
            sector.kind = SECTOR_BORDER_WALL
            return 1
        return 0
    if kind in (SECTOR_DOOR, SECTOR_BORDER_WALL):
        if not _is_border(room, x, z):
            sector.kind = SECTOR_FLOOR
            return 1
        if kind == SECTOR_DOOR and _door_at(project, room, x, z) is None:
            sector.kind = SECTOR_BORDER_WALL
            return 1
        return 0
    if kind == SECTOR_WALL:
        if _is_border(room, x, z) and _door_at(project, room, x, z) is None:
            sector.kind = SECTOR_BORDER_WALL
            return 1
        return 0
    sector.kind = SECTOR_FLOOR
    return 1


def _repair_sectors(project, log):
    """Correct the sector types, portal flags and heights; return the number of repairs."""
    repairs = 0
    for room in project.rooms:
        if not room.is_used:
            continue
        for z in range(room.size_z):
            for x in range(room.size_x):
                sector = room.sector_at(x, z)
                repairs += _repair_sector_type(project, room, x, z, sector, log)
                if sum(1 for flag in _PORTAL_FLAGS if sector.flags & flag) > 2:
                    sector.flags &= 0xFFF1
                    repairs += 1
                if sector.ceiling <= sector.floor:
                    sector.floor = sector.ceiling - 1
                    repairs += 1
    return repairs


# ------------------------------------------------------------------------------- objects
def _repair_objects(project, log):
    """Remove objects in the wrong room or used twice, and duplicate triggers; return the number of
    repairs.
    """
    repairs = 0
    seen_objects = set()
    seen_triggers = set()
    seen_lights = set()
    for room_index, room in enumerate(project.rooms):
        if not room.is_used:
            continue
        seen_objects.update(room.door_ids)
        kept = []
        for object_id in reversed(room.object_ids):
            placed = project.objects[object_id]
            in_this_room = (placed.room == room_index
                            or project.rooms[placed.room].flip_room == room_index)
            if not in_this_room or object_id in seen_objects:
                repairs += 1
                log(f'Object {object_id} in room {room_index} removed')
                continue
            if placed.type == TYPE_TRIGGER:
                if placed.link in seen_triggers:
                    repairs += 1
                    log(f'Trigger of object {object_id} in room {room_index} removed')
                    continue
                seen_triggers.add(placed.link)
            seen_objects.add(object_id)
            kept.append(object_id)
        kept.reverse()
        room.object_ids = kept
        for object_id in room.light_ids:
            if object_id not in seen_objects:
                seen_objects.add(object_id)
                placed = project.objects[object_id]
                if object_class(placed.type) == 2:
                    seen_lights.add(placed.link)
    return repairs
