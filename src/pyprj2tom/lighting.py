"""Calculating the vertex colours of a compiled room.

A vertex starts from the room's ambient colour.  For each polygon it belongs to, every light adds
to the colour.  A vertex shared by several polygons gets the running average of their results.
Before adding light, a ray from the light to the vertex is tested against the walls.
"""
import math

from pyprj2tom.project_model import (CLASS_LIGHT, TYPE_LIGHT_EFFECT, TYPE_LIGHT_POINT,
                                   TYPE_LIGHT_SHADOW,
                                   TYPE_LIGHT_SPOT, TYPE_LIGHT_SUN, object_class)
from pyprj2tom.vertex_math import (DEGREES_TO_RADIANS, NO_VERTEX, direction_from_angles, dot,
                                 normalise, to_float32)
from trle_common.c_arithmetic import c_div, to_int16

_WALL_BIT = 8


class VertexColours:
    """Accumulated colour of each point while a room is being lit."""

    def __init__(self):
        """Create an empty set of per-point colour sums."""
        self.shade = {}

    def reset(self, count):
        """Clear the sums and use counts for ``count`` points."""
        self.count = [0] * count
        self.red = [0] * count
        self.green = [0] * count
        self.blue = [0] * count

    def add(self, point, red, green, blue):
        """Blend a new value into a point's running average."""
        count = to_int16(self.count[point])
        divisor = count + 1
        self.red[point] = c_div(self.red[point] * count + red, divisor)
        self.green[point] = c_div(self.green[point] * count + green, divisor)
        self.blue[point] = c_div(self.blue[point] * count + blue, divisor)
        self.count[point] = count + 1


def _clamp_byte(value):
    """Limit a colour value to the range of a byte."""
    return 0xFF if value >= 0x100 else max(value, 0)


class RoomLighter:
    """Lights one room at a time."""

    def __init__(self, project):
        """Remember the project, which holds the lights and objects."""
        self.project = project

    def light_room(self, room, points, records, masks, point_count):
        """Compute every vertex colour; returns per-point packed 15-bit colours."""
        colours = VertexColours()
        colours.reset(point_count)
        if room.light_ids:
            for z in range(room.size_z):
                for x in range(room.size_x):
                    index = room.size_x * z + x
                    for polygon in range(14):
                        if masks[index] & (1 << polygon):
                            self._light_polygon(room, points, records[index][polygon], colours, x,
                                                z,
                                                polygon)
        else:
            colours.red = [room.ambient[0]] * point_count
            colours.green = [room.ambient[1]] * point_count
            colours.blue = [room.ambient[2]] * point_count
        packed = []
        for red, green, blue in zip(colours.red, colours.green, colours.blue):
            packed.append(((_clamp_byte(blue) >> 3) << 10) | ((_clamp_byte(green) >> 3) << 5)
                          | (_clamp_byte(red) >> 3))
        return packed

    def _light_polygon(self, room, points, record, colours, sector_x, sector_z, polygon):
        """Add the light falling on each corner of a polygon to the sums of its points.

        Each corner starts from the room's ambient colour.  A point's final colour is the average
        over all the polygons that use it.
        """
        project = self.project
        normal = normalise([to_float32(record.normal[0]), to_float32(record.normal[1]),
                            to_float32(record.normal[2])])
        vertex_count = 3 + (1 if record.vertices[3] != NO_VERTEX else 0)
        for vertex in record.vertices[:vertex_count]:
            x, y, z = points[vertex]
            red, green, blue = room.ambient[0], room.ambient[1], room.ambient[2]
            for light_id in room.light_ids:
                placed = project.objects[light_id]
                if object_class(placed.type) != CLASS_LIGHT:
                    continue
                light = project.lights[placed.link]
                amount = self._light_amount(placed, light, normal, x, y, z, sector_x, sector_z,
                                            polygon, room)
                if amount:
                    red += (light.red * amount) >> 13
                    green += (light.green * amount) >> 13
                    blue += (light.blue * amount) >> 13
            colours.add(vertex, red, green, blue)

    def _light_amount(self, placed, light, normal, x, y, z, sector_x, sector_z, polygon, room):
        """Return how strongly a light affects a vertex of a polygon; 0 or ``None`` for nothing."""
        dx, dy, dz = float(x - placed.world_x), float(y - placed.world_y), float(z - placed.world_z)
        ray = [to_float32(dx), to_float32(dy), to_float32(dz)]
        distance = to_float32(math.sqrt(float(z - placed.world_z) * ray[2] + ray[1] * ray[1]
                                        + ray[0] * ray[0]))
        direction_to_vertex = normalise(ray)
        if placed.type == TYPE_LIGHT_SPOT:
            return self._spot_amount(placed, light, normal, direction_to_vertex, distance, room, x,
                                     y, z)
        if placed.type == TYPE_LIGHT_SUN:
            return self._sun_amount(placed, light, normal, room, x, y, z)
        if placed.type == TYPE_LIGHT_EFFECT:
            if (placed.x == sector_x and placed.z == sector_z
                    and (placed.placement & 0xF) == polygon):
                return light.intensity
            return 0
        if placed.type in (TYPE_LIGHT_POINT, TYPE_LIGHT_SHADOW):
            return self._bulb_amount(placed, light, normal, distance, room, x, y, z)
        return 0

    def _spot_amount(self, placed, light, normal, to_vertex, distance, room, x, y, z):
        """Return the strength of a spot light at a point.

        "length" is the distance of full brightness and "cutoff" the maximum distance.  Between the
        inner and outer angle of the cone the light fades out.
        """
        # For spot lights "length" is the distance of full brightness and "cutoff" the maximum.
        if not distance < light.cutoff:
            return 0
        axis = direction_from_angles(light.y_angle, light.x_angle)
        cosine_to_axis = to_float32(-(axis[2] * to_vertex[2] + axis[1] * to_vertex[1]
                                      + axis[0] * to_vertex[0]))
        inner = to_float32(math.cos(light.inner * DEGREES_TO_RADIANS))
        outer = to_float32(math.cos(light.outer * DEGREES_TO_RADIANS))
        if cosine_to_axis <= outer:
            cone = 0.0
        elif not cosine_to_axis < inner:
            cone = 1.0
        else:
            cone = to_float32(1.0 - (cosine_to_axis - inner) / (outer - inner))
        if distance < light.length:
            attenuation = 1.0
        else:
            attenuation = to_float32(1.0 - (distance - light.length)
                                     / (light.cutoff - light.length))
        facing = to_float32(dot(axis, normal))
        if not facing > 0.0 or not self._ray_is_clear(room, placed, x, y, z):
            return 0
        return to_int16(int(attenuation * facing * cone * light.intensity))

    def _sun_amount(self, placed, light, normal, room, x, y, z):
        """Return the strength of a sun light at a point; it depends on the direction only."""
        axis = direction_from_angles(light.y_angle, light.x_angle)
        facing = to_float32(dot(axis, normal))
        if not facing > 0.0 or not self._ray_is_clear(room, placed, x, y, z):
            return 0
        amount = to_int16(int(float(light.intensity) * facing))
        return amount if amount >= 0 else 0

    def _bulb_amount(self, placed, light, normal, distance, room, x, y, z):
        """Return the strength of a point or shadow light at a point.

        The strength falls from the inner radius to the outer radius.
        """
        falloff = to_float32((light.outer - distance) / (light.outer - light.inner))
        if not falloff <= 1.0:
            falloff = 1.0
        elif not falloff > 0.0:
            return 0
        # The editor uses the polygon normal with itself here, so the result is about 1.
        facing = to_float32(dot(normal, normal))
        if not facing > 0.0 or not self._ray_is_clear(room, placed, x, y, z):
            return 0
        return to_int16(int(facing * falloff * light.intensity))

    # ---------------------------------------------------------------------- wall ray tests
    def _ray_is_clear(self, room, placed, x, y, z):
        """Tell whether no wall blocks the line from a light to a point."""
        start = (placed.world_x, placed.world_y, placed.world_z)
        return (_sector_of_light_is_open(room, x, y, z, start)
                and _ray_clear_along_x_grid(room, x, y, z, start)
                and _ray_clear_along_z_grid(room, x, y, z, start))


def _height(y):
    """Convert a world Y value to a height in clicks (rounding towards zero)."""
    return -c_div(y, 256)


def _edge_height(sector, first, second, floor):
    """Return the height of the middle of a sector edge, between two corners."""
    base = sector.floor if floor else sector.ceiling
    return ((sector.corners[first] + sector.corners[second]) >> 1) + base


def _sector_of_light_is_open(room, x, y, z, start):
    """Tell whether the sector beside the point on the side of the light is open at that height."""
    start_x, _, start_z = start
    row = c_div(x, 1024) - (1 if start_x < x else 0)
    column = c_div(z, 1024) - (1 if start_z < z else 0)
    index = row * room.size_x + column
    sector = room.sectors[index]
    height = _height(y)
    return sector.floor <= height <= sector.ceiling and not sector.kind & _WALL_BIT


def _ray_clear_along_x_grid(room, x, y, z, start):
    """March along the ray's Z coordinate, checking both sectors of each X row boundary."""
    start_x, start_y, start_z = start
    if start_z < z:
        run_x, run_y, run_z = x - start_x, y - start_y, z - start_z
        begin_x, begin_y, begin_z = start_x, start_y, start_z
        end_z = z
    else:
        run_x, run_y, run_z = start_x - x, start_y - y, start_z - z
        begin_x, begin_y, begin_z = x, y, z
        end_z = start_z
    if run_z == 0:
        return True
    divisor = run_z + 1
    to_boundary = (c_div(begin_z, 1024) + 1) * 1024 - begin_z
    current_x = begin_x + c_div(to_boundary * run_x, divisor)
    current_y = begin_y + c_div(to_boundary * run_y, divisor)
    previous_index = None
    position = begin_z + to_boundary
    while position <= end_z:
        grid_z = c_div(position, 1024)
        grid_x = c_div(current_x, 1024)
        if grid_x < 0 or room.size_z <= grid_x or room.size_x <= grid_z:
            if position == end_z:
                return True
        else:
            height = _height(current_y)
            this_index = previous_index
            if grid_z > 0:
                this_index = room.size_x * grid_x + grid_z - 1
                this_sector = room.sectors[this_index]
                if (height < _edge_height(this_sector, 1, 0, True)
                        or _edge_height(this_sector, 6, 7, False) < height
                        or this_sector.kind & _WALL_BIT):
                    return False
            if position == end_z:
                return True
            previous_index = this_index
            if grid_z >= 0:
                this_sector = room.sectors[this_index]
                if height < _edge_height(this_sector, 3, 2, True):
                    return False
                if _edge_height(this_sector, 5, 4, False) < height:
                    return False
                if this_sector.kind & _WALL_BIT:
                    return False
                next_sector = room.sectors[this_index + 1]
                previous_index = this_index + 1
                if height < _edge_height(next_sector, 1, 0, True):
                    return False
                if _edge_height(next_sector, 6, 7, False) < height:
                    return False
                if next_sector.kind & _WALL_BIT:
                    return False
        current_x += c_div(run_x << 10, divisor)
        current_y += c_div(run_y << 10, divisor)
        position += 0x400
    return True


def _ray_clear_along_z_grid(room, x, y, z, start):
    """March along the ray's X coordinate, checking both sectors of each Z row boundary."""
    start_x, start_y, start_z = start
    if start_x < x:
        run_y, run_z, run_x = y - start_y, z - start_z, x - start_x
        begin_x, begin_y, begin_z = start_x, start_y, start_z
        end_x = x
    else:
        run_y, run_z, run_x = start_y - y, start_z - z, start_x - x
        begin_x, begin_y, begin_z = x, y, z
        end_x = start_x
    if run_x == 0:
        return True
    divisor = run_x + 1
    to_boundary = (c_div(begin_x, 1024) + 1) * 1024 - begin_x
    current_y = begin_y + c_div(to_boundary * run_y, divisor)
    current_z = begin_z + c_div(to_boundary * run_z, divisor)
    position = begin_x + to_boundary
    this_index = None
    while position <= end_x:
        grid_z = c_div(current_z, 1024)
        grid_x = c_div(position, 1024)
        if grid_z < 0 or room.size_x <= grid_z or room.size_z <= grid_x:
            if position == end_x:
                return True
        else:
            height = _height(current_y)
            if grid_x > 0:
                this_index = (grid_x - 1) * room.size_x + grid_z
                this_sector = room.sectors[this_index]
                if (height < _edge_height(this_sector, 2, 1, True)
                        or _edge_height(this_sector, 6, 5, False) < height
                        or this_sector.kind & _WALL_BIT):
                    return False
            if position == end_x:
                return True
            if grid_x >= 0:
                this_sector = room.sectors[this_index]
                if height < _edge_height(this_sector, 3, 0, True):
                    return False
                if _edge_height(this_sector, 7, 4, False) < height:
                    return False
                if this_sector.kind & _WALL_BIT:
                    return False
                far_sector = room.sectors[this_index + room.size_x]
                if height < _edge_height(far_sector, 2, 1, True):
                    return False
                if _edge_height(far_sector, 6, 5, False) < height:
                    return False
                if far_sector.kind & _WALL_BIT:
                    return False
        current_y += c_div(run_y << 10, divisor)
        current_z += c_div(run_z << 10, divisor)
        position += 0x400
    return True
