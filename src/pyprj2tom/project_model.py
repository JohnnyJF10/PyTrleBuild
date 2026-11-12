"""The data model of an editor project: rooms, sectors, faces, objects, lights and triggers."""
from dataclasses import dataclass, field
from typing import List, Optional

MAX_OBJECTS = 2000
MAX_LIGHTS = 0x300
MAX_TRIGGERS = 0x200
FACES_PER_SECTOR = 14
NO_ROOM = -1

# ---------------------------------------------------------------------------- object types
TYPE_DOOR_X_POSITIVE = 1
TYPE_DOOR_Z_POSITIVE = 2
TYPE_DOOR_Z_NEGATIVE = -2
TYPE_DOOR_X_NEGATIVE = -3
TYPE_DOOR_FLOOR = 4
TYPE_DOOR_CEILING = -5
TYPE_ITEM = 8
TYPE_TRIGGER = 0x10
TYPE_LIGHT_POINT = 0x4000
TYPE_LIGHT_FOG = 0x4020
TYPE_LIGHT_SPOT = 0x4100
TYPE_LIGHT_SUN = 0x4200
TYPE_LIGHT_EFFECT = 0x5000
TYPE_LIGHT_SHADOW = 0x6000
TYPE_CAMERA_FLYBY = 0x4040
TYPE_CAMERA_FIXED = 0x4080
TYPE_CAMERA_NORMAL = 0x4800
TYPE_SINK = 0x4400
TYPE_SOUND_SOURCE = 0x4C00

CLASS_DOOR = 1
CLASS_LIGHT = 2
CLASS_ITEM = 8
CLASS_TRIGGER = 0x10
CLASS_CAMERA = 0x20
CLASS_SINK = 0x40
CLASS_SOUND_SOURCE = 0x80

_CLASS_OF_TYPE = {
    TYPE_DOOR_CEILING: CLASS_DOOR, TYPE_DOOR_X_NEGATIVE: CLASS_DOOR,
    TYPE_DOOR_Z_NEGATIVE: CLASS_DOOR,
    TYPE_DOOR_X_POSITIVE: CLASS_DOOR, TYPE_DOOR_Z_POSITIVE: CLASS_DOOR, TYPE_DOOR_FLOOR: CLASS_DOOR,
    TYPE_ITEM: CLASS_ITEM, TYPE_TRIGGER: CLASS_TRIGGER,
    TYPE_LIGHT_POINT: CLASS_LIGHT, TYPE_LIGHT_FOG: CLASS_LIGHT, TYPE_LIGHT_SPOT: CLASS_LIGHT,
    TYPE_LIGHT_SUN: CLASS_LIGHT, TYPE_LIGHT_EFFECT: CLASS_LIGHT, TYPE_LIGHT_SHADOW: CLASS_LIGHT,
    TYPE_CAMERA_FLYBY: CLASS_CAMERA, TYPE_CAMERA_FIXED: CLASS_CAMERA,
    TYPE_CAMERA_NORMAL: CLASS_CAMERA,
    TYPE_SINK: CLASS_SINK, TYPE_SOUND_SOURCE: CLASS_SOUND_SOURCE,
}


def object_class(object_type):
    """Return the class of an object type; the editor treats unknown types as triggers."""
    return _CLASS_OF_TYPE.get(object_type, CLASS_TRIGGER)


# ---------------------------------------------------------------------------- sector values
SECTOR_NONE = 0
SECTOR_FLOOR = 1
SECTOR_FLOOR_OPENING = 3     # bit 2: a portal in the floor
SECTOR_CEILING_OPENING = 5   # bit 4: a portal in the ceiling
SECTOR_BOTH_OPENINGS = 7
SECTOR_DOOR = 6
SECTOR_WALL = 0x0E
SECTOR_BORDER_WALL = 0x1E

KIND_WALKABLE = 1
KIND_WALL_BIT = 8

FLAG_TRIGGER = 0x0001
FLAG_FLOOR_PORTAL = 0x0002
FLAG_CEILING_PORTAL = 0x0004
FLAG_WALL_PORTAL = 0x0008
FLAG_DEATH = 0x0010
FLAG_BLOCK = 0x0020
FLAG_CLIMB_MASK = 0x03C0
FLAG_CEILING_TRAVERSABLE = 0x0400
FLAG_FLOOR_TRAVERSABLE = 0x0800
FLAG_WALL_TRAVERSABLE = 0x1000
FLAG_MONKEY = 0x4000

ROOM_FLAG_WATER = 0x0001
ROOM_FLAG_ALTERNATE = 0x0002
ROOM_FLAG_OUTSIDE_VISIBLE = 0x0008
ROOM_FLAG_NO_LENS_FLARE = 0x0080


@dataclass
class Face:
    """The texture settings of one of the 14 polygons of a sector."""

    kind: int = 0
    texture: int = 0
    rotation: int = 0
    triangle: int = 0
    extra: int = 0


@dataclass
class Sector:
    """One grid cell of a room."""

    kind: int = 0
    flags: int = 0
    no_collision_flags: int = 0
    floor: int = 0
    ceiling: int = 0
    # Sixteen signed offsets from the heights above.  The positions are grouped in fours:
    # floor, ceiling, second floor layer, second ceiling layer.
    corners: List[int] = field(default_factory=lambda: [0] * 16)
    faces: List[Face] = field(default_factory=list)
    split_flags: int = 0
    box: int = 0


@dataclass
class PlacedObject:
    """A record of the shared object table: door, item, trigger, light, camera..."""

    type: int = 0
    x: int = 0
    z: int = 0
    size_x: int = 0
    size_z: int = 0
    height_offset: int = 0
    room: int = 0
    link: int = 0
    flags: int = 0
    placement: int = 0
    world_x: int = 0
    world_y: int = 0
    world_z: int = 0
    rotation_x: int = 0
    rotation_y: int = 0
    roll: int = 0
    tint1: int = 0
    tint2: int = 0


@dataclass
class LightData:
    """The settings of a light object."""

    intensity: int = 0
    inner: float = 0.0
    outer: float = 0.0
    x_angle: float = 0.0
    y_angle: float = 0.0
    length: float = 0.0
    cutoff: float = 0.0
    red: int = 0
    green: int = 0
    blue: int = 0
    enabled: int = 0


@dataclass
class TriggerData:
    """The settings of a trigger object."""

    trigger_type: int = 0
    target: int = 0
    timer: int = 0
    flags: int = 0
    target_kind: int = 0


@dataclass
class Room:
    """A room of the project, with the fields compile and export need."""

    is_used: bool = False
    name: str = ''
    size_x: int = 0
    size_z: int = 0
    position_x: int = 0
    position_z: int = 0
    # World position, filled by the room compile.
    world_x: int = 0
    world_y: int = 0
    world_z: int = 0
    door_ids: List[int] = field(default_factory=list)
    object_ids: List[int] = field(default_factory=list)
    light_ids: List[int] = field(default_factory=list)
    ambient: bytes = b'\0\0\0\0'
    flip_room: int = NO_ROOM
    flags: int = 0
    effect_word: int = 0
    water_level: int = 0
    reflection_level: int = 0
    reverb: int = 0
    flip_group: int = 0
    extra_byte: int = 0
    sectors: List[Sector] = field(default_factory=list)
    floor_height: int = 0
    ceiling_height: int = 0
    # Results of the room compile.
    mesh: Optional[object] = None
    doors: Optional[object] = None
    has_colourless_texture: bool = False

    def sector_at(self, x, z):
        """Return the sector at the given room coordinates."""
        return self.sectors[self.size_x * z + x]


@dataclass
class ObjectSlot:
    """An entry of the object list in objects.h, completed from the .WAD and .SWD."""

    name: str
    kind: int = 0
    number: int = 0
    collision_rectangle: List[int] = field(default_factory=lambda: [2, 2, 1, 1])
    collision_heights: List[int] = field(default_factory=lambda: [0] * 25)
    other_heights: List[int] = field(default_factory=lambda: [0] * 25)
    mesh_id: int = 0


SLOT_MOVEABLE = 0x10
SLOT_STATIC = 0x110
SLOT_SPRITE = 8
SLOT_SPRITE_STATIC = 0x108


@dataclass
class Project:
    """The complete state of a loaded project."""

    version: int = 0
    rooms: List[Room] = field(default_factory=list)
    objects: List[Optional[PlacedObject]] = field(default_factory=lambda: [None] * MAX_OBJECTS)
    lights: List[LightData] = field(
        default_factory=lambda: [LightData() for _ in range(MAX_LIGHTS)])
    triggers: List[TriggerData] = field(
        default_factory=lambda: [TriggerData() for _ in range(MAX_TRIGGERS)])
    object_slots: List[ObjectSlot] = field(default_factory=list)
    moveable_slot_count: int = 0
    object_version: int = 0
    texture_path: str = ''
    wad_path: str = ''
    texture_tiles: list = field(default_factory=list)
    animated_ranges: List[tuple] = field(default_factory=list)
    footstep_table: bytes = b''
    bump_table: bytes = b''
    current_room: int = 0
    flipped: bool = False

    @property
    def room_count(self):
        """Return the number of room slots, used or not."""
        return len(self.rooms)
