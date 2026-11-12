"""Compiling rooms: from sectors to points, polygons, vertex colours, mesh and portals.

A sector owns 16 points.  Points 0-3 are floor corners, 4-7 ceiling corners, 8-11 the corners of a
second floor layer and 12-15 of a second ceiling layer.  Polygons refer to points by number.
Because polygons of neighbouring sectors share corners, a merge pass makes equal positions
use the same point.  After lighting, the used points are renumbered to give the final mesh.
"""
from collections import defaultdict
from dataclasses import dataclass
from typing import List

from pyprj2tom.lighting import RoomLighter
from pyprj2tom.polygon_builder import PolygonRecord, SectorPolygonBuilder
from pyprj2tom.project_model import (CLASS_DOOR, CLASS_TRIGGER, TYPE_DOOR_CEILING, TYPE_DOOR_FLOOR,
                                   TYPE_DOOR_X_NEGATIVE, TYPE_DOOR_X_POSITIVE, TYPE_DOOR_Z_POSITIVE,
                                   object_class)
from pyprj2tom.project_reader import height_range
from pyprj2tom.vertex_math import NO_VERTEX
from trle_common.c_arithmetic import to_int16

_CORNER_TEMPLATE = ((1024, 0), (0, 0), (0, 1024), (1024, 1024),
                    (1024, 0), (0, 0), (0, 1024), (1024, 1024),
                    (1024, 0), (0, 0), (0, 1024), (1024, 1024),
                    (1024, 0), (0, 0), (0, 1024), (1024, 1024))
_POINTS_PER_SECTOR = 16
_POINT_CAPACITY = 0x1900        # 400 sectors of 16 points; the point buffer is shared by all rooms
_POLYGONS_PER_SECTOR = 14
_MERGE_PASSES = 3
_WALL_BIT = 8

_FACE_TEXTURED = 7
_FACE_TRANSPARENT_COLOUR = 3
_POLYGON_FLAT_COLOUR = 3
_POLYGON_TEXTURED = 9
_POLYGON_TEXTURED_TRANSPARENT = 0x11
_DEFAULT_COLOUR = 6


@dataclass
class CompiledPolygon:
    """A polygon of the finished mesh."""

    kind: int
    vertices: List[int]
    texture: int
    sector_x: int
    sector_z: int

    @property
    def has_four_vertices(self):
        """Tell whether this polygon is a quad."""
        return len(self.vertices) == 4


@dataclass
class CompiledMesh:
    """The finished mesh of a room."""

    vertices: list
    shades: list
    polygons: list


@dataclass
class CompiledDoor:
    """A portal in the form it is written to the level."""

    adjoining_room: int
    normal: tuple
    corners: list


class RoomCompiler:
    """Compiles rooms of one project; state from earlier rooms stays where the editor keeps it."""

    def __init__(self, project):
        """Create the work buffers, which are shared by all rooms and keep their old contents."""
        self.project = project
        self.records = defaultdict(lambda: [PolygonRecord() for _ in range(_POLYGONS_PER_SECTOR)])
        self.masks = {}
        self.points = [[0, 0, 0] for _ in range(_POINT_CAPACITY)]
        self.lighter = RoomLighter(project)

    # ------------------------------------------------------------------ whole level
    def compile_all(self):
        """Build all rooms in both flip states and then match their shades across doors."""
        project = self.project
        for room in project.rooms:
            room.mesh = None
        for _ in range(2):
            for index, room in enumerate(project.rooms):
                if room.is_used and not room.flags & 2:
                    self.compile_room(index)
            self.toggle_flip_state()
        self.compile_room(project.current_room)
        for _ in range(2):
            for index, room in enumerate(project.rooms):
                if room.is_used and not room.flags & 2:
                    self.match_door_shades(index)
            self.toggle_flip_state()

    def toggle_flip_state(self):
        """Exchange the geometry of every flip pair; the two rooms keep their numbers."""
        project = self.project
        for room in project.rooms:
            if room.is_used and room.flip_room != -1:
                self._swap_flip_pair(room, project.rooms[room.flip_room])
        project.flipped = not project.flipped

    @staticmethod
    def _swap_flip_pair(base, alternate):
        """Exchange the geometry of a room and its flip room; the second one is marked as the hidden
        state.
        """
        for name in ('sectors', 'ambient', 'effect_word', 'floor_height', 'ceiling_height',
                     'mesh', 'doors', 'light_ids', 'object_ids', 'door_ids'):
            base_value = getattr(base, name)
            setattr(base, name, getattr(alternate, name))
            setattr(alternate, name, base_value)
        base_flags = base.flags
        base.flags = alternate.flags & ~2 & 0xFFFF
        alternate.flags = base_flags | 2

    # ------------------------------------------------------------------ one room
    def compile_room(self, index):
        """Run the whole compile for a room: geometry, mesh and portals."""
        project = self.project
        room = project.rooms[index]
        self.rebuild_geometry(index, with_lighting=True)
        self._create_mesh(room)
        self._create_doors(room)

    def rebuild_geometry(self, index, with_lighting):
        """Build the points and polygons of a room in the shared work buffers."""
        project = self.project
        room = project.rooms[index]
        room.world_x, room.world_y, room.world_z = room.position_z << 10, 0, room.position_x << 10
        self._fill_point_grid(room)
        builder = SectorPolygonBuilder(room, self.points, self.records, self.masks)
        for z in range(room.size_z):
            for x in range(room.size_x):
                self._set_heights(room, x, z)
                builder.build(x, z)
        for object_id in room.object_ids:
            self._place_object_on_polygon(room, object_id)
        for _ in range(_MERGE_PASSES):
            self._merge_vertices(room)
        for light_id in room.light_ids:
            self._place_object_on_polygon(room, light_id)
        if with_lighting:
            self.shade_of_point = self.lighter.light_room(room, self.points, self.records,
                                                          self.masks,
                                                          len(self.points))

    def _fill_point_grid(self, room):
        """Set the X and Z of the points of all sectors; other points keep their old values."""
        number = 0
        for z in range(room.size_z):
            for x in range(room.size_x):
                for corner_x, corner_z in _CORNER_TEMPLATE:
                    point = self.points[number]
                    point[0], point[1], point[2] = corner_x + (z << 10), 0, corner_z + (x << 10)
                    number += 1

    def _set_heights(self, room, x, z):
        """Fill in the heights of a sector's points; ceilings are at least one click above floors.
        """
        index = room.size_x * z + x
        sector = room.sectors[index]
        if sector.ceiling <= sector.floor:
            sector.ceiling = sector.floor + 1
        base = index * _POINTS_PER_SECTOR
        corners = sector.corners
        for k in range(4):
            self.points[base + k][1] = to_int16((corners[k] + sector.floor) * -0x100)
            self.points[base + 8 + k][1] = to_int16((corners[8 + k] + sector.floor) * -0x100)
            self.points[base + 4 + k][1] = to_int16((corners[7 - k] + sector.ceiling) * -0x100)
            self.points[base + 12 + k][1] = to_int16((corners[15 - k] + sector.ceiling) * -0x100)

    def _place_object_on_polygon(self, room, object_id):
        """Compute the world position of an object mounted on a polygon of its sector."""
        placed = self.project.objects[object_id]
        kind = object_class(placed.type)
        if kind in (CLASS_DOOR, CLASS_TRIGGER):
            return
        polygon = placed.placement & 0xF
        record = self.records[room.size_x * placed.z + placed.x][polygon]
        point = self.points
        v0, v1, v2, v3 = record.vertices
        p0, p1, p2 = point[v0], point[v1], point[v2]
        if v3 == NO_VERTEX:
            p3 = [p2[i] if p0[i] == p1[i] else (p1[i] if p0[i] == p2[i] else p0[i])
                  for i in range(3)]
        else:
            p3 = point[v3]
        side = placed.placement >> 4
        if side == 1:
            position = [(p1[i] + p0[i]) >> 1 for i in range(3)]
        elif side == 2:
            position = [(p2[i] + p1[i]) >> 1 for i in range(3)]
        elif side == 3:
            position = [(p3[i] + p2[i]) >> 1 for i in range(3)]
        elif side == 4:
            position = [(p3[i] + p0[i]) >> 1 for i in range(3)]
        else:
            position = [(p3[i] + p2[i] + p1[i] + p0[i]) >> 2 for i in range(3)]
        if kind in (8, 0x20):
            centre = [(p3[i] + p2[i] + p1[i] + p0[i]) >> 2 for i in range(3)]
            position = [position[i] + ((centre[i] - position[i]) >> 2) for i in range(3)]
        if polygon in (0, 8):
            normal = (0.0, -1.0, 0.0)
        elif polygon in (1, 9):
            normal = (0.0, 1.0, 0.0)
        else:
            normal = record.normal
        distance = float(placed.height_offset)
        placed.world_x = position[0] + to_int16(int(normal[0] * distance))
        placed.world_y = position[1] + to_int16(int(normal[1] * distance))
        placed.world_z = position[2] + to_int16(int(normal[2] * distance))

    # ------------------------------------------------------------------ merging points
    def _active_polygons(self, room, x, z):
        """Return the polygon records that exist in a sector."""
        index = room.size_x * z + x
        mask = self.masks[index]
        records = self.records[index]
        return [records[polygon] for polygon in range(_POLYGONS_PER_SECTOR)
                if mask & (1 << polygon)]

    def _neighbourhood(self, room, x, z):
        """Yield the sector and its neighbours in a 3 x 3 area, row by row."""
        start_z = z - 1 if z > 0 else z
        start_x = x - 1 if x > 0 else x
        for row in range(start_z, start_z + 3):
            if row >= room.size_z:
                return
            for column in range(start_x, start_x + 3):
                if column >= room.size_x:
                    break
                yield column, row

    def _merge_vertices(self, room):
        """Give all polygon corners with equal positions, near each other, the same point."""
        done = set()
        for z in range(room.size_z):
            for x in range(room.size_x):
                for record in self._active_polygons(room, x, z):
                    for slot in range(4):
                        vertex = record.vertices[slot]
                        if vertex != NO_VERTEX and vertex not in done:
                            self._merge_into(room, vertex, x, z, done)

    def _merge_into(self, room, vertex, x, z, done):
        """Replace every point at the same position as ``vertex``, near the sector, by ``vertex``.
        """
        while True:
            duplicate = self._find_duplicate_point(room, vertex, x, z)
            if duplicate is None:
                return
            for column, row in self._neighbourhood(room, x, z):
                for record in self._active_polygons(room, column, row):
                    vertices = record.vertices
                    for slot in range(4):
                        if vertices[slot] == duplicate:
                            vertices[slot] = vertex
            done.add(vertex)

    def _find_duplicate_point(self, room, vertex, x, z):
        """Return another point at the position of ``vertex``, near the sector, or None."""
        target = self.points[vertex]
        for column, row in self._neighbourhood(room, x, z):
            for record in self._active_polygons(room, column, row):
                for candidate in record.vertices:
                    if (candidate != NO_VERTEX and candidate != vertex
                            and self.points[candidate] == target):
                        return candidate
        return None

    # ------------------------------------------------------------------ mesh
    def _create_mesh(self, room):
        """Number the used points and produce the room's vertex and polygon lists."""
        room.flags &= 0xFFF7
        used = {}
        for z in range(room.size_z):
            for x in range(room.size_x):
                for record in self._active_polygons(room, x, z):
                    for slot in range(4):
                        vertex = record.vertices[slot]
                        if vertex != NO_VERTEX and vertex not in used:
                            used[vertex] = True
        new_numbers = {point: number for number, point in enumerate(sorted(used))}
        vertices = [tuple(self.points[point]) for point in sorted(used)]
        shades = [self.shade_of_point[point] for point in sorted(used)]
        polygons = []
        for z in range(room.size_z):
            for x in range(room.size_x):
                index = room.size_x * z + x
                mask = self.masks[index]
                records = self.records[index]
                sector = room.sectors[index]
                for polygon in range(_POLYGONS_PER_SECTOR):
                    if mask & (1 << polygon):
                        polygons.append(self._mesh_polygon(room, sector.faces[polygon],
                                                           records[polygon], new_numbers, x, z))
        room.mesh = CompiledMesh(vertices, shades, polygons)

    def _mesh_polygon(self, room, face, record, new_numbers, x, z):
        """Convert a polygon record to its final form.

        Decides between textured, transparent and flat, and adds the triangle corner to the texture
        word.
        """
        if face.kind == _FACE_TRANSPARENT_COLOUR and face.texture == 0:
            room.flags |= _WALL_BIT
        if face.kind == _FACE_TEXTURED:
            texture = face.texture
            transparent = self.project.tile_transparency[texture & 0x3FF]
            kind = _POLYGON_TEXTURED_TRANSPARENT if transparent else _POLYGON_TEXTURED
        else:
            kind = _POLYGON_FLAT_COLOUR
            texture = _DEFAULT_COLOUR if face.kind == 0 else face.texture
        v0, v1, v2, v3 = record.vertices
        if v3 == NO_VERTEX:
            if kind != _POLYGON_FLAT_COLOUR:
                texture = (face.triangle << 13) | face.texture
            kind -= 1
            numbers = [new_numbers[v0], new_numbers[v1], new_numbers[v2]]
        else:
            numbers = [new_numbers[v0], new_numbers[v1], new_numbers[v2], new_numbers[v3]]
        return CompiledPolygon(kind & 0xFFFF, numbers, texture & 0xFFFF, x, z)

    # ------------------------------------------------------------------ portals
    def _create_doors(self, room):
        """Build the portal records of a room."""
        project = self.project
        doors = []
        for object_id in room.door_ids:
            placed = project.objects[object_id]
            partner_room = project.objects[placed.link].room
            doors.append(self._door_record(room, placed, partner_room))
        room.doors = doors

    def _door_record(self, room, placed, adjoining):
        """Compute the normal and the four corners of a door; the opening fits the sectors on both
        sides.
        """
        door_type = placed.type
        low, high = 32000, -32000
        if door_type == TYPE_DOOR_X_POSITIVE:
            normal = (0, 0, 1)
            for row in range(placed.size_z):
                sector = room.sector_at(placed.x, placed.z + row)
                other = room.sector_at(placed.x + 1, placed.z + row)
                low, high = _widen_opening(sector, other, low, high)
            corners = self._wall_corners(room, placed, 2, 3, high, low)
        elif door_type == TYPE_DOOR_Z_POSITIVE:
            normal = (1, 0, 0)
            for column in range(placed.size_x):
                sector = room.sector_at(placed.x + column, placed.z)
                other = room.sector_at(placed.x + column, placed.z + 1)
                low, high = _widen_opening(sector, other, low, high)
            corners = self._wall_corners(room, placed, 0, 3, low, high)
        elif door_type == TYPE_DOOR_FLOOR:
            normal = (0, -1, 0)
            floor_y, _ = height_range(room, placed.x, placed.z, placed.size_x, placed.size_z)
            corners = self._horizontal_corners(room, placed, floor_y)
        elif door_type == TYPE_DOOR_CEILING:
            normal = (0, 1, 0)
            _, ceiling_y = height_range(room, placed.x, placed.z, placed.size_x, placed.size_z)
            corners = self._horizontal_corners(room, placed, ceiling_y)
        elif door_type == TYPE_DOOR_X_NEGATIVE:
            normal = (-1, 0, 0)
            for column in range(placed.size_x):
                sector = room.sector_at(placed.x + column, placed.z)
                other = room.sector_at(placed.x + column, placed.z - 1)
                low, high = _widen_opening(sector, other, low, high)
            corners = self._wall_corners(room, placed, 1, 2, high, low)
        else:
            normal = (0, 0, -1)
            for row in range(placed.size_z):
                sector = room.sector_at(placed.x, placed.z + row)
                other = room.sector_at(placed.x - 1, placed.z + row)
                low, high = _widen_opening(sector, other, low, high)
            corners = self._wall_corners(room, placed, 1, 0, low, high)
        return CompiledDoor(adjoining, normal, corners)

    def _point_position(self, room, x, z, corner):
        """Return the position of a corner of a sector."""
        vertex = self.records_index_point(room, x, z, corner)
        return self.points[vertex]

    @staticmethod
    def records_index_point(room, x, z, corner):
        """Return the number of a corner point of a sector."""
        return (room.size_x * z + x) * _POINTS_PER_SECTOR + corner

    def _wall_corners(self, room, placed, first_corner, second_corner, top_click, bottom_click):
        """Return the four corners of a wall door from its first and last sector."""
        size_x, size_z = placed.size_x, placed.size_z
        first = self._point_position(room, placed.x, placed.z, first_corner)
        last_x = placed.x + size_x - 1
        last_z = placed.z + size_z - 1
        second = self._point_position(room, last_x, last_z, second_corner)
        offset_x = -1 if placed.type == TYPE_DOOR_Z_POSITIVE else 0
        offset_z = -1 if placed.type == TYPE_DOOR_X_POSITIVE else 0
        top = to_int16(top_click * -0x100)
        bottom = to_int16(bottom_click * -0x100)
        corners = [(first[0] + offset_x, top, first[2] + offset_z),
                   (first[0] + offset_x, bottom, first[2] + offset_z)]
        return corners + [(second[0] + offset_x, bottom, second[2] + offset_z),
                          (second[0] + offset_x, top, second[2] + offset_z)]

    def _horizontal_corners(self, room, placed, height):
        """Return the four corners of a floor or ceiling door at a height."""
        y = to_int16(height * -0x100 - (1 if placed.type == TYPE_DOOR_CEILING else 0))
        x0, z0, sx, sz = placed.x, placed.z, placed.size_x, placed.size_z
        a = self.points[self.records_index_point(room, x0, z0, 1)]
        far_x = self.points[self.records_index_point(room, x0 + sx - 1, z0, 2)]
        far_z = self.points[self.records_index_point(room, x0, z0 + sz - 1, 0)]
        c = self.points[self.records_index_point(room, x0 + sx - 1, z0 + sz - 1, 3)]
        second, fourth = (far_x, far_z) if placed.type == TYPE_DOOR_FLOOR else (far_z, far_x)
        return [(a[0], y, a[2]), (second[0], y, second[2]), (c[0], y, c[2]), (fourth[0], y,
                                                                              fourth[2])]

    # ------------------------------------------------------------------ shades across doors
    def match_door_shades(self, index):
        """Average the shade of vertices that coincide with a vertex of a neighbouring room."""
        rooms = self.project.rooms
        room = rooms[index]
        if not room.door_ids or room.mesh is None or room.doors is None:
            return
        for object_id, door in zip(room.door_ids, room.doors):
            partner_room = self.project.objects[self.project.objects[object_id].link].room
            other = rooms[partner_room]
            if other.mesh is None or other.doors is None or ((other.flags ^ room.flags) & 1):
                continue
            bounds = _door_bounds(door)
            self._match_shades(room, other, bounds)

    def _match_shades(self, room, other, bounds):
        """Give vertices inside a door that coincide with a vertex of the other room the average of
        both.
        """
        (min_x, max_x), (min_y, max_y), (min_z, max_z) = bounds
        offset_x = to_int16(room.world_x - other.world_x)
        offset_y = to_int16(room.world_y - other.world_y)
        offset_z = to_int16(room.world_z - other.world_z)
        other_positions = {}
        for number, position in enumerate(other.mesh.vertices):
            other_positions.setdefault(tuple(position), number)
        mine = room.mesh
        theirs = other.mesh
        for number, (x, y, z) in enumerate(mine.vertices):
            if not (min_x <= x <= max_x and min_y <= y <= max_y and min_z <= z <= max_z):
                continue
            other_number = other_positions.get((to_int16(offset_x + x), to_int16(offset_y + y),
                                                to_int16(offset_z + z)))
            if other_number is None:
                continue
            blended = _average_shade(mine.shades[number], theirs.shades[other_number])
            theirs.shades[other_number] = blended
            mine.shades[number] = blended

    # ------------------------------------------------------------------ doors of the current room
    def refresh_current_room_doors(self):
        """Copy geometry through the doors of the current room to their partner sectors."""
        project = self.project
        for object_id in project.rooms[project.current_room].door_ids:
            placed = project.objects[object_id]
            if placed.type in (TYPE_DOOR_FLOOR, TYPE_DOOR_CEILING):
                self._refresh_horizontal_door(placed)
            else:
                self._refresh_wall_door(placed)
        self.compile_room(project.current_room)

    def _ordered_door_pair(self, placed):
        """Return a door and its partner, with the room that has the geometry first."""
        project = self.project
        partner = project.objects[placed.link]
        if (project.flipped and project.rooms[placed.room].flip_room != -1
                and project.rooms[partner.room].flip_room == -1):
            return partner, placed
        return placed, partner

    def _refresh_wall_door(self, placed):
        """Copy heights and sector types through a wall door, then rebuild the room on the other
        side.
        """
        door, partner = self._ordered_door_pair(placed)
        rooms = self.project.rooms
        room_a, room_b = rooms[door.room], rooms[partner.room]
        offset_x = room_a.position_x + door.x - room_b.position_x
        offset_z = room_a.position_z + door.z - room_b.position_z
        for row in range(door.size_z):
            for column in range(door.size_x):
                source = room_a.sector_at(door.x + column, door.z + row)
                target = room_b.sector_at(offset_x + column, offset_z + row)
                _copy_heights(target, source)
                if source.kind & _WALL_BIT:
                    target.kind = source.kind
                else:
                    source.kind = 6
                    if target.kind & _WALL_BIT:
                        target.kind = 1
        offset_x = room_b.position_x + partner.x - room_a.position_x
        offset_z = room_b.position_z + partner.z - room_a.position_z
        for row in range(door.size_z):
            for column in range(door.size_x):
                source = room_a.sector_at(offset_x + column, offset_z + row)
                target = room_b.sector_at(partner.x + column, partner.z + row)
                _copy_heights(target, source)
                target.kind = 6 if not source.kind & _WALL_BIT else source.kind
        room_b.floor_height, room_b.ceiling_height = height_range(room_b, 0, 0, room_b.size_x,
                                                                  room_b.size_z)
        self.compile_room(partner.room)

    def _refresh_horizontal_door(self, placed):
        """Join the sectors above and below a floor or ceiling door, then rebuild the other room."""
        door, partner = self._ordered_door_pair(placed)
        rooms = self.project.rooms
        room_a, room_b = rooms[door.room], rooms[partner.room]
        floor_a, ceiling_a = height_range(room_a, door.x, door.z, door.size_x, door.size_z)
        floor_b, ceiling_b = height_range(room_b, partner.x, partner.z, partner.size_x,
                                          partner.size_z)
        for row in range(door.size_z):
            for column in range(door.size_x):
                a = room_a.sector_at(door.x + column, door.z + row)
                b = room_b.sector_at(partner.x + column, partner.z + row)
                if door.type == TYPE_DOOR_CEILING:
                    _join_ceiling_opening(a, b, ceiling_a, floor_b, ceiling_b)
                else:
                    _join_floor_opening(a, b, floor_a, floor_b, ceiling_b)
        room_b.floor_height, room_b.ceiling_height = height_range(room_b, 0, 0, room_b.size_x,
                                                                  room_b.size_z)
        self.compile_room(partner.room)


def _widen_opening(sector, other, low, high):
    """Extend the door opening so that both sectors at the door fit into it."""
    if other.floor < sector.floor:
        low = min(low, sector.floor)
    else:
        low = min(low, other.floor)
    if sector.ceiling < other.ceiling:
        high = max(high, sector.ceiling)
    else:
        high = max(high, other.ceiling)
    return low, high


def _door_bounds(door):
    """Return the (low, high + 1) range of a door along each axis."""
    xs = [corner[0] for corner in door.corners]
    ys = [corner[1] for corner in door.corners]
    zs = [corner[2] for corner in door.corners]
    return ((min(xs), max(xs) + 1), (min(ys), max(ys) + 1), (min(zs), max(zs) + 1))


def _average_shade(a, b):
    """Average two 15-bit colours, channel by channel."""
    a &= 0xFFFF
    b &= 0xFFFF
    return ((((a >> 10) & 31) + ((b >> 10) & 31)) >> 1) << 10 \
        | (((((a >> 5) & 31) + ((b >> 5) & 31)) >> 1) << 5) \
        | (((a & 31) + (b & 31)) >> 1)


def _copy_heights(target, source):
    """Copy the floor, ceiling and corner heights of a sector."""
    target.floor = source.floor
    target.ceiling = source.ceiling
    target.corners[:16] = source.corners[:16]


def _join_ceiling_opening(a, b, ceiling_a, floor_b, ceiling_b):
    """Connect sector ``a`` to sector ``b`` above it; undo the connection if they do not fit."""
    a_fits = a.ceiling == ceiling_a and not a.kind & _WALL_BIT and not any(a.corners[4:8])
    b_fits = b.floor == floor_b and not b.kind & _WALL_BIT and not any(b.corners[:4])
    if a_fits and b_fits:
        a.kind |= 4
        b.kind |= 2
        b.floor = ceiling_a
        a.corners[4:8] = [0, 0, 0, 0]
        b.corners[:4] = [0, 0, 0, 0]
        return
    if a.kind & 4 and not a.kind & _WALL_BIT:
        a.kind -= 4
        a.ceiling = ceiling_a
    a.flags &= 0xFFFB
    if b.kind & 2 and not b.kind & _WALL_BIT:
        b.kind -= 2
        b.floor = ceiling_a
    b.flags &= 0xFFFD


def _join_floor_opening(a, b, floor_a, floor_b, ceiling_b):
    """Connect sector ``a`` to sector ``b`` below it; undo the connection if they do not fit."""
    a_fits = a.floor == floor_a and not a.kind & _WALL_BIT and not any(a.corners[:4])
    b_fits = b.ceiling == ceiling_b and not b.kind & _WALL_BIT and not any(b.corners[4:8])
    if a_fits and b_fits:
        a.kind |= 2
        b.kind |= 4
        b.ceiling = floor_a
        a.corners[:4] = [0, 0, 0, 0]
        b.corners[4:8] = [0, 0, 0, 0]
        return
    if a.kind & 2 and not a.kind & _WALL_BIT:
        a.kind -= 2
        a.floor = floor_a
    a.flags &= 0xFFFD
    if b.kind & 4 and not b.kind & _WALL_BIT:
        b.kind -= 4
        b.ceiling = floor_a
    b.flags &= 0xFFFB
