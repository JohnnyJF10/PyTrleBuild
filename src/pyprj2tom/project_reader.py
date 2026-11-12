"""Reading a .PRJ project file (see docs/formats/PRJ_FORMAT.md).

Only version 0x31, the version the editor writes and every known project uses, is supported.
Older files need many conversions which could not be tested and raise an error instead.
"""
from pyprj2tom.binary_reader import BinaryReader
from pyprj2tom.object_loading import load_object_slots
from pyprj2tom.project_model import (CLASS_LIGHT, CLASS_TRIGGER, FACES_PER_SECTOR, Face, LightData,
                                   PlacedObject, Project, Room, Sector, TriggerData,
                                   TYPE_CAMERA_FLYBY, object_class)
from pyprj2tom.texture_import import load_project_texture

SUPPORTED_VERSION = 0x31
_FREE_LIST_LIGHTS = 0x300
_FREE_LIST_TRIGGERS = 0x200
_ANIMATED_RANGE_ENTRIES = 40
_NO_FILE = ('NA', 'NA ')
_OPENING_ROTATION_MASK = 0xE000
_TILE_GRID_ENTRIES = 0x100


class ProjectFormatError(Exception):
    """The file is not a project this program can read."""


def read_project(path, inputs, log):
    """Return the :class:`Project` stored in ``path``.  Slots and texture are loaded too."""
    with open(path, 'rb') as file:
        reader = BinaryReader(file.read())
    if reader.take(8) != b'PROJFILE':
        raise ProjectFormatError(f'{path} is not a project file')
    project = Project(version=reader.i32())
    if project.version != SUPPORTED_VERSION:
        raise ProjectFormatError(f'project version {project.version:#x} is not supported '
                                 f'(only {SUPPORTED_VERSION:#x})')
    load_object_slots(project, inputs)

    room_count = reader.i32()
    project.rooms = [_read_room(reader, project) for _ in range(room_count)]
    _skip_free_lists(reader)
    _read_texture_reference(reader, project, inputs, log)
    _read_wad_reference(reader, project, inputs)
    _read_animated_ranges(reader, project)
    project.footstep_table = reader.take(0x100)
    project.bump_table = reader.take(0x100)
    return project


def _read_room(reader, project):
    """Read one room: its header, the lists of doors, objects and lights, its properties and
    sectors.
    """
    room = Room(is_used=reader.i16() == 0)
    if not room.is_used:
        room.name, room.flags = 'Empty', 0
        return room
    room.name = reader.take(80).split(b'\0', 1)[0].decode('latin-1')
    reader.skip(18)     # the world position is recalculated
    reader.skip(4)
    room.size_x, room.size_z = reader.read('hh')
    room.position_x, room.position_z = reader.read('hh')
    reader.skip(2)      # the room's id; a value of -1 only causes a warning

    room.door_ids = _read_object_list(reader, project)
    room.object_ids = _read_object_list(reader, project)
    room.ambient = reader.take(4)
    room.light_ids = _read_object_list(reader, project, read_light_data=True)
    room.flip_room, room.flags = reader.read('hH')
    (room.water_level, room.reflection_level, room.reverb, room.flip_group,
     room.extra_byte) = reader.read('5B')
    room.sectors = [_read_sector(reader) for _ in range(room.size_x * room.size_z)]
    _compute_room_height_range(room)
    return room


def _read_object_list(reader, project, read_light_data=False):
    """Read a counted list of object numbers followed by one record for each."""
    count = reader.i16()
    ids = list(reader.read(f'{count}h'))
    for object_id in ids:
        placed = _read_object_record(reader)
        project.objects[object_id] = placed
        kind = object_class(placed.type)
        if kind == CLASS_TRIGGER:
            project.triggers[placed.link] = _read_trigger(reader)
        if read_light_data and kind == CLASS_LIGHT:
            project.lights[placed.link] = _read_light(reader)
    return ids


def _read_object_record(reader):
    """Read the part of a placed object that all object types share.

    For everything but a flyby camera only the top bits of the rotation are meaningful.
    """
    placed = PlacedObject()
    placed.type, placed.x, placed.z, placed.size_x, placed.size_z = reader.read('5h')
    (placed.height_offset, placed.room, placed.link, placed.flags,
     placed.placement) = reader.read('5h')
    placed.world_x, placed.world_y, placed.world_z = reader.read('3i')
    placed.rotation_x, placed.rotation_y, placed.roll = reader.read('3h')
    placed.tint1, placed.tint2 = reader.read('hh')
    if placed.type != TYPE_CAMERA_FLYBY:
        placed.rotation_y &= _OPENING_ROTATION_MASK
    return placed


def _read_trigger(reader):
    """Read the data of a trigger."""
    return TriggerData(*reader.read('5h'))


def _read_light(reader):
    """Read the data of a light."""
    intensity = reader.i16()
    inner, outer, x_angle, y_angle, length = reader.read('5f')
    cutoff = reader.f32()
    red, green, blue = reader.read('3B')
    enabled = reader.u8()
    return LightData(intensity, inner, outer, x_angle, y_angle, length, cutoff, red, green, blue,
                     enabled)


def _read_sector(reader):
    """Read one sector: type, flags, heights, corner heights and its 14 textured faces."""
    sector = Sector(kind=reader.u16(), flags=reader.u16())
    sector.floor, sector.ceiling = reader.read('hh')
    sector.corners = list(reader.read('16b'))
    sector.faces = [Face(*reader.read('HHBBH')) for _ in range(FACES_PER_SECTOR)]
    sector.no_collision_flags = reader.u16()
    sector.split_flags = reader.u16()
    return sector


def _compute_room_height_range(room):
    """Find a room's lowest floor and highest ceiling, ignoring the border sectors."""
    room.floor_height, room.ceiling_height = height_range(room, 0, 0, room.size_x, room.size_z)


def height_range(room, first_x, first_z, count_x, count_z):
    """Return the lowest floor and highest ceiling of walkable sectors in a rectangle.

    The rectangle is first clipped so that it does not include the outer border of the room.  If
    it has no walkable sector, the values come from wall sectors instead.  As in the original,
    each wall sector replaces the ceiling result once its ceiling exceeds the walkable maximum.
    """
    if first_x < 1:
        count_x += first_x - 1
        first_x = 1
    if room.size_x <= first_x + count_x:
        count_x = room.size_x - first_x - 1
    if first_z < 1:
        count_z += first_z - 1
        first_z = 1
    if room.size_z <= first_z + count_z:
        count_z = room.size_z - first_z - 1
    lowest_floor = 32000
    wall_floor = 32000
    highest_ceiling = -32000
    wall_ceiling = -32000
    for z in range(first_z, first_z + count_z):
        for x in range(first_x, first_x + count_x):
            sector = room.sector_at(x, z)
            if not sector.kind & 8:
                lowest_floor = min(lowest_floor, sector.floor)
                highest_ceiling = max(highest_ceiling, sector.ceiling)
            else:
                wall_floor = min(wall_floor, sector.floor)
                if sector.ceiling > highest_ceiling:
                    wall_ceiling = sector.ceiling
    if lowest_floor != 32000:
        return lowest_floor, highest_ceiling
    return wall_floor, wall_ceiling


def _skip_free_lists(reader):
    """Skip the editor's lists of free object, light and trigger slots."""
    reader.skip(4)                                  # free objects
    capacity = reader.i32()
    reader.skip(capacity * 4)
    reader.skip(4 + _FREE_LIST_LIGHTS * 4)
    reader.skip(4 + _FREE_LIST_TRIGGERS * 4)


def _read_texture_reference(reader, project, inputs, log):
    """Read the texture file name and table, and load the texture image.

    Also derives from the texture's cells whether each tile is transparent.
    """
    project.texture_path = reader.space_terminated_text()
    if project.texture_path in _NO_FILE:
        project.texture_path = ''
        return
    load_project_texture(project, inputs, log)
    tile_count = reader.i32()
    tiles = [list(reader.read('iHH')) for _ in range(tile_count)]
    cell_flags = project.texture.cell_transparency
    transparency = []
    for index, tile in enumerate(tiles):
        row = tile[0] // 256
        column = tile[0] - row * 256
        cell = column // 64 + (row // 64) * 4
        if index < _TILE_GRID_ENTRIES:
            # The first 256 entries must always form the regular 64 x 64 grid, in row order.
            expected_column = index & 3
            expected_row = index >> 2
            if column // 64 != expected_column or row // 64 != expected_row:
                tile[0] = (expected_row * 0x100 + expected_column) * 0x40
            cell = index
        transparency.append(cell_flags[cell] if cell < len(cell_flags) else 0)
    project.texture_tiles = tiles
    project.tile_transparency = transparency


def _read_wad_reference(reader, project, inputs):
    """Read the name of the object file, load it, and map the placed items to its slots."""
    project.wad_path = reader.space_terminated_text()
    if project.wad_path in _NO_FILE:
        project.wad_path = ''
        return
    from pyprj2tom.object_loading import load_wad_objects
    load_wad_objects(project, inputs)
    slot_count = reader.i32()
    remap = _read_slot_table(reader, project, slot_count)
    _reassign_items(project, remap)


def _read_slot_table(reader, project, slot_count):
    """Read the slot records and map old slot numbers to the slots of the loaded WAD."""
    remap = {}
    names = [slot.name for slot in project.object_slots]
    for _ in range(slot_count):
        if reader.i16() == 0:
            continue
        name = reader.space_terminated_text()
        original_index = reader.i32()
        best, best_length = -1, -1
        for index, candidate in enumerate(names):
            if best_length < len(candidate) and name.startswith(candidate):
                best, best_length = index, len(candidate)
        slot = project.object_slots[best] if best != -1 else None
        if slot is None or slot.kind == 0:
            reader.skip(0x6C)
            continue
        slot.collision_rectangle = list(reader.read('4h'))
        slot.collision_heights = list(reader.read('25h'))
        slot.other_heights = list(reader.read('25h'))
        _clip_collision_tables(slot)
        remap[original_index] = best
    return remap


def _clip_collision_tables(slot):
    """Clear collision heights outside the item's collision rectangle."""
    left, top, width, height = slot.collision_rectangle
    for y in range(5):
        for x in range(5):
            if x < left or width - 1 + left < x or y < top or height - 1 + top < y:
                slot.collision_heights[y * 5 + x] = 0
                slot.other_heights[y * 5 + x] = 0


def _reassign_items(project, remap):
    """Point every item at its new slot; items whose slot no longer exists are removed."""
    for room in project.rooms:
        if not room.is_used:
            continue
        for object_id in reversed(list(room.object_ids)):
            placed = project.objects[object_id]
            if object_class(placed.type) != 8:
                continue
            new_slot = remap.get(placed.link)
            if new_slot is None:
                room.object_ids.remove(object_id)
                continue
            is_alternate = room.flags & 2 != 0
            is_static = project.object_slots[new_slot].kind & 0x100 != 0
            if not is_alternate or is_static:
                placed.link = new_slot


def _read_animated_ranges(reader, project):
    """Read the table of animated ranges; the editor rebuilds its list of used ranges from it."""
    reader.skip(4 + _ANIMATED_RANGE_ENTRIES * 4 + 0x100 * 4)
    project.animated_ranges = [reader.read('3i') for _ in range(_ANIMATED_RANGE_ENTRIES)]
