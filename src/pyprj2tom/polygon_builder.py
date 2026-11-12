"""Building the polygons of a room's sectors.

Each sector owns 16 points (see :mod:`pyprj2tom.room_compiler`) and up to 14 polygons:

====== ================================== ======================================
index  polygon                            normal, when not computed from vertices
====== ================================== ======================================
0, 8   floor (quad, or two triangles)     computed
1, 9   ceiling (quad, or two triangles)   computed
2, 10  wall towards Z-1: lower step       along X
3, 11  wall towards Z-1: upper step       along X
4      wall towards Z-1: full height      along X
5, 12  wall towards X-1: lower step       along Z
6, 13  wall towards X-1: upper step       along Z
7      wall towards X-1: full height      along Z
====== ================================== ======================================

Polygon records persist between sectors and between rooms: only records that are rebuilt change.
This matters in rare cases, for instance when a wall-mounted object refers to a polygon of its
sector that was not built this time.  Polygons 10 to 13 never get a normal assigned at all.

The many comparisons below decide which of the two neighbouring sectors a wall step belongs to
and where the visible wall begins and ends.
"""
from pyprj2tom.project_model import KIND_WALKABLE
from pyprj2tom.vertex_math import NO_VERTEX, face_is_planar, face_normal

_NORMAL_X_NEGATIVE = (-1.0, 0.0, 0.0)
_NORMAL_X_POSITIVE = (1.0, 0.0, 0.0)
_NORMAL_Z_NEGATIVE = (0.0, 0.0, -1.0)
_NORMAL_Z_POSITIVE = (0.0, 0.0, 1.0)
_WALL_BIT = 8


class PolygonRecord:
    """The stored form of a polygon of a sector."""

    __slots__ = ('vertices', 'normal')

    def __init__(self):
        """Create an empty record: three vertices and no fourth one."""
        self.vertices = [0, 0, 0, NO_VERTEX]
        self.normal = (0.0, 0.0, 0.0)


class SectorPolygonBuilder:
    """Creates the polygons of one sector at a time.

    ``records`` maps a sector number to its list of 14 persistent :class:`PolygonRecord` objects.
    """

    def __init__(self, room, points, records, masks):
        """Keep the shared work buffers: points, polygon records and active-polygon masks."""
        self.room = room
        self.points = points
        self.records = records
        self.masks = masks

    # ------------------------------------------------------------------ small building blocks
    def _y(self, point):
        """Return the height of a point."""
        return self.points[point][1]

    def _positions_equal(self, first, second):
        """Tell whether two points are at the same position."""
        return self.points[first] == self.points[second]

    def _triangle_is_valid(self, a, b, c):
        """A triangle needs three different corners."""
        return not (self._positions_equal(a, b) or self._positions_equal(b, c)
                    or self._positions_equal(c, a))

    def _simplify_quad(self, vertices):
        """Turn a quad with a repeated corner into a triangle; return False if it is unusable."""
        a, b, c, d = vertices
        if self._positions_equal(a, b):
            vertices[0], vertices[3] = d, NO_VERTEX
            return True
        if self._positions_equal(b, c):
            vertices[2], vertices[3] = d, NO_VERTEX
            return True
        if self._positions_equal(c, d):
            vertices[3] = NO_VERTEX
            return True
        if self._positions_equal(d, a):
            vertices[3] = NO_VERTEX
            return True
        # A quad whose diagonal edges run the opposite way would be twisted.
        return (self._y(d) < self._y(a)) == (self._y(c) < self._y(b))

    def _make_polygon(self, polygon, a, b, c, d=NO_VERTEX):
        """Store the vertices of a polygon of the current sector; return whether it is valid."""
        record = self.current_records[polygon]
        record.vertices = [a, b, c, d]
        if d != NO_VERTEX and not self._simplify_quad(record.vertices):
            return False
        if record.vertices[3] == NO_VERTEX:
            return self._triangle_is_valid(*record.vertices[:3])
        return True

    def _activate(self, polygon):
        """Mark a polygon of the current sector as existing."""
        self.masks[self.index] |= 1 << polygon

    def _is_active(self, polygon):
        """Tell whether a polygon of the current sector exists."""
        return self.masks[self.index] & (1 << polygon) != 0

    def _rotate(self, polygon, steps):
        """Rotate the corners of a polygon by the rotation chosen in the editor for its texture."""
        record = self.current_records[polygon]
        vertices = record.vertices
        if vertices[3] == NO_VERTEX:
            for _ in range(steps):
                vertices[0], vertices[1], vertices[2] = vertices[1], vertices[2], vertices[0]
        else:
            for _ in range(steps):
                vertices[:] = vertices[1:] + vertices[:1]

    def _compute_normal(self, polygon):
        """Compute a polygon's normal from its first three corners."""
        record = self.current_records[polygon]
        a, b, c = (self.points[v] for v in record.vertices[:3])
        record.normal = face_normal(a, b, c)

    def _wall(self, polygon, normal, a, b, c, d=NO_VERTEX):
        """Create a wall polygon; on success activate it and give it its fixed normal."""
        if not self._make_polygon(polygon, a, b, c, d):
            return False
        self._activate(polygon)
        self.current_records[polygon].normal = normal
        return True

    def _set_normal(self, polygon, normal):
        """Set the normal of a polygon."""
        self.current_records[polygon].normal = normal

    # ------------------------------------------------------------------ one sector
    def build(self, x, z):
        """Create all polygons of the sector at (x, z)."""
        room = self.room
        self.index = room.size_x * z + x
        self.current_records = self.records[self.index]
        self.masks[self.index] = 0
        self.sector = room.sector_at(x, z)
        self.first_point = [self.index * 16 + k for k in range(16)]
        self.hidden_bits = (self.sector.flags & 0x0E) ^ (self.sector.kind & 0xFF)
        self._build_floor()
        self._build_ceiling()
        if z > 0:
            self._build_z_walls(room.sectors[self.index - room.size_x],
                                [(self.index - room.size_x) * 16 + k for k in range(16)])
        if x > 0:
            self._build_x_walls(room.sectors[self.index - 1],
                                [(self.index - 1) * 16 + k for k in range(16)])

    def _build_floor(self):
        """Create the floor: one quad if the corners are in a plane, otherwise two triangles.

        The heights decide along which diagonal to split; the sector's split flag can swap it.  If
        all corners agree pairwise no polygon is made.
        """
        points, sector, faces = self.first_point, self.sector, self.sector.faces
        hidden = self.hidden_bits & 2
        if face_is_planar(*(self.points[points[k]] for k in range(4))):
            if self._make_polygon(0, points[0], points[1], points[2], points[3]):
                self._compute_normal(0)
                self._rotate(0, faces[0].rotation)
                if not hidden:
                    self._activate(0)
                faces[8].kind = 0
                faces[8].texture = 0
            return
        y0, y1 = self._y(points[0]), self._y(points[1])
        y2, y3 = self._y(points[2]), self._y(points[3])
        if y0 == y2:
            first_diagonal = True
        elif y1 == y3:
            first_diagonal = False
        elif ((y1 <= y0 or y3 <= y0) and (y1 <= y2 or y3 <= y2)
              and (y0 <= y1 or y0 <= y3) and (y2 <= y1 or y2 <= y3)):
            if ((y0 <= y3 or y2 <= y3) and (y0 <= y1 or y2 <= y1)
                    and (y3 <= y0 or y3 <= y2) and (y1 <= y0 or y1 <= y2)):
                return                      # all four corners agree pairwise: no polygons at all
            first_diagonal = True
        else:
            first_diagonal = False
        for face in (faces[0], faces[8]):
            face.rotation %= 3
            face.extra = (face.extra & 0xFFFE) if first_diagonal else (face.extra | 1)
        if first_diagonal == bool(sector.split_flags & 1):
            pair = ((points[0], points[1], points[3]), (points[1], points[2], points[3]))
        else:
            pair = ((points[0], points[2], points[3]), (points[0], points[1], points[2]))
        if self._make_polygon(0, *pair[0]):
            self._compute_normal(0)
            self._rotate(0, faces[0].rotation)
            if not hidden:
                self._activate(0)
        if self._make_polygon(8, *pair[1]):
            self._compute_normal(8)
            self._rotate(8, faces[8].rotation)
            if not hidden:
                self._activate(8)

    def _build_ceiling(self):
        """Create the ceiling, in the same way as the floor but with its own diagonal rules."""
        points, faces = self.first_point, self.sector.faces
        hidden = self.hidden_bits & 4
        quad = (points[7], points[6], points[5], points[4])
        if face_is_planar(*(self.points[points[k]] for k in (4, 5, 6, 7))):
            if self._make_polygon(1, *quad):
                self._compute_normal(1)
                self._rotate(1, faces[1].rotation)
                if not hidden:
                    self._activate(1)
            return
        y5, y7 = self._y(points[5]), self._y(points[7])
        y4, y6 = self._y(points[4]), self._y(points[6])
        if y5 == y7:
            first_diagonal = True
        elif y4 == y6:
            first_diagonal = False
        elif ((y5 < y4 and y7 < y4) or (y5 < y6 and y7 < y6)
              or (y4 < y5 and y4 < y7) or (y6 < y5 and y6 < y7)):
            first_diagonal = True
        elif ((y4 < y7 and y6 < y7) or (y4 < y5 and y6 < y5)
              or (y7 < y4 and y7 < y6) or (y5 < y4 and y5 < y6)):
            first_diagonal = False
        else:
            return
        for face in (faces[1], faces[9]):
            face.rotation %= 3
            face.extra = (face.extra & 0xFFFE) if first_diagonal else (face.extra | 1)
        if first_diagonal:
            pair = ((points[7], points[5], points[4]), (points[7], points[6], points[5]))
        else:
            pair = ((points[7], points[6], points[4]), (points[6], points[5], points[4]))
        if self._make_polygon(1, *pair[0]):
            self._compute_normal(1)
            self._rotate(1, faces[1].rotation)
            if not hidden:
                self._activate(1)
        if self._make_polygon(9, *pair[1]):
            self._compute_normal(9)
            self._rotate(9, faces[9].rotation)
            if not hidden:
                self._activate(9)

    # ------------------------------------------------------------------ walls towards Z-1
    def _build_z_walls(self, other, neighbour):
        """Create the walls between this sector and its neighbour at Z - 1.

        A "step" is the wall that appears where the floors or ceilings of the two sectors differ. It
        belongs to the sector on whose side it is visible.  The full-height wall is for a neighbour
        that is solid.
        """
        sector, here, faces = self.sector, self.first_point, self.sector.faces
        corners, far = sector.corners, other.corners
        my_floor = corners[2] + sector.floor * 2 + corners[1]
        other_floor = far[3] + other.floor * 2 + far[0]
        my_ceiling = corners[6] + sector.ceiling * 2 + corners[5]
        other_ceiling = far[7] + other.ceiling * 2 + far[4]
        y = self._y

        if other_floor < my_floor and other.kind & KIND_WALKABLE:
            self._z_lower_step_on_other(neighbour, here, my_floor, other_ceiling)
        elif my_floor < other_floor and self.hidden_bits & KIND_WALKABLE:
            self._z_lower_step_on_this(neighbour, here, other_floor, my_ceiling)
        if self._is_active(2):
            self._rotate(2, faces[2].rotation)
        if self._is_active(10):
            self._rotate(10, faces[10].rotation)

        if my_ceiling < other_ceiling and other.kind & KIND_WALKABLE:
            self._z_upper_step_on_other(neighbour, here, my_ceiling, other_floor)
        elif other_ceiling < my_ceiling and self.hidden_bits & KIND_WALKABLE:
            self._z_upper_step_on_this(neighbour, here, other_ceiling, my_floor)
        if self._is_active(3):
            self._rotate(3, faces[3].rotation)
        if self._is_active(11):
            self._rotate(11, faces[11].rotation)

        self._z_full_height_wall(other, neighbour, here, my_floor, other_floor, my_ceiling,
                                 other_ceiling)
        if self._is_active(4):
            self._rotate(4, faces[4].rotation)

    def _z_lower_step_on_other(self, near, here, my_floor, other_ceiling):
        """The other side's floor is lower: the step is seen from the neighbour, facing -X."""
        y, n = self._y, _NORMAL_X_NEGATIVE
        near_first, near_second = near[0], near[3]
        top_left, top_right = near[4], near[7]
        low_a, low_b = here[9], here[10]
        if other_ceiling < my_floor:
            ya, yb = y(low_a), y(low_b)
            if ya < y(top_right) or y(near_first) < ya or yb < y(top_left) or y(near_second) < yb:
                self._wall(2, n, top_right, top_left, near_first, near_second)
                return
            if (ya != y(top_right) or yb != y(top_left)) and (ya != y(near_first)
                                                              or yb != y(near_second)):
                self._wall(2, n, top_right, top_left, low_a, low_b)
                self._z_step_tail(low_b, low_a, near_first, near_second, n)
                return
            if self._wall(2, n, top_right, top_left, near_first, near_second):
                self._set_normal(2, n)
            return
        ya, yb = y(low_a), y(low_b)
        if y(near_first) < ya or y(near_second) < yb or (ya == y(near_first)
                                                         and yb == y(near_second)):
            self._wall(2, n, here[2], here[1], near_first, near_second)
            return
        self._wall(2, n, here[2], here[1], low_a, low_b)
        self._z_step_tail(low_b, low_a, near_first, near_second, n)

    def _z_step_tail(self, a, b, c, d, normal):
        """Add the second part of a lower step split by the diagonal step line."""
        if self._make_polygon(10, a, b, c, d):
            self._activate(10)
            self._set_normal(2, normal)

    def _z_lower_step_on_this(self, near, here, other_floor, my_ceiling):
        """This side's floor is lower: the step belongs to the neighbour's sector, facing +X."""
        y, n = self._y, _NORMAL_X_POSITIVE
        row_a, row_b = near[8], near[11]
        ya, yb = y(row_a), y(row_b)
        p1, p2, p5, p6 = y(here[1]), y(here[2]), y(here[5]), y(here[6])
        if my_ceiling < other_floor:
            if (p5 <= ya and p6 <= yb and ya <= p1 and yb <= p2
                    and (ya != p5 or yb != p6) and (ya != p1 or yb != p2)):
                self._wall(2, n, here[5], here[6], row_b, row_a)
                self._z_tail_high(row_a, row_b, here[2], here[1], n)
                return
            self._wall(2, n, here[5], here[6], here[2], here[1])
            return
        if p1 < ya or p2 < yb or (ya == p1 and yb == p2):
            self._wall(2, n, near[0], near[3], here[2], here[1])
            return
        self._wall(2, n, near[0], near[3], row_b, row_a)
        self._z_tail_high(row_a, row_b, here[2], here[1], n)

    def _z_tail_high(self, a, b, c, d, normal):
        """Add the second part of a lower step that faces this sector."""
        if self._make_polygon(10, a, b, c, d):
            self._activate(10)
            self._set_normal(2, normal)

    def _z_upper_step_on_other(self, near, here, my_ceiling, other_floor):
        """This ceiling is lower: the step above it is seen from the neighbour, facing -X.

        Polygon 11 is the second part of the step, when the second ceiling layer divides it.
        """
        y, n = self._y, _NORMAL_X_NEGATIVE
        low_a, low_b = near[4], near[7]
        first, second = near[0], near[3]
        ya, yb = y(here[13]), y(here[14])
        if my_ceiling < other_floor:
            if (y(low_a) <= ya <= y(first) and y(low_b) <= yb <= y(second)
                    and (ya != y(low_a) or ya != y(first)) and (yb != y(low_b) or yb != y(second))):
                self._wall(3, n, low_b, low_a, here[13], here[14])
                if self._make_polygon(11, here[14], here[13], first, second):
                    self._activate(11)
                    self._set_normal(3, n)
                return
            self._wall(3, n, low_b, low_a, first, second)
            return
        if ya < y(low_a) or yb < y(low_b):
            self._wall(3, n, low_b, low_a, here[5], here[6])
            return
        if ya == y(low_a) and yb == y(low_b):
            self._wall(3, n, low_b, low_a, here[5], here[6])
            return
        self._wall(3, n, low_b, low_a, here[13], here[14])
        if self._make_polygon(11, here[14], here[13], here[5], here[6]):
            self._activate(11)
            self._set_normal(3, n)

    def _z_upper_step_on_this(self, near, here, other_ceiling, my_floor):
        """The neighbour's ceiling is lower: the step is seen from this sector, facing +X."""
        y, n = self._y, _NORMAL_X_POSITIVE
        row_a, row_b = near[12], near[15]
        ya, yb = y(row_a), y(row_b)
        p1, p2, p5, p6 = y(here[1]), y(here[2]), y(here[5]), y(here[6])
        if other_ceiling < my_floor:
            if ya < p5 or p1 < ya or yb < p6 or p2 < yb:
                self._wall(3, n, here[5], here[6], here[2], here[1])
                return
            if (ya != p5 or ya != p1) and (yb != p6 or yb != p2):
                self._wall(3, n, here[5], here[6], row_b, row_a)
                self._z_upper_tail(row_a, row_b, here[2], here[1], n)
                return
            self._wall(3, n, here[5], here[6], here[2], here[1])
            return
        if ya < p5 or yb < p6 or (ya == p5 and yb == p6):
            self._wall(3, n, here[5], here[6], near[7], near[4])
            return
        self._wall(3, n, here[5], here[6], row_b, row_a)
        self._z_upper_tail(row_a, row_b, near[7], near[4], n)

    def _z_upper_tail(self, a, b, c, d, normal):
        """Add the second part of an upper step."""
        if self._make_polygon(11, a, b, c, d):
            self._activate(11)
            self._set_normal(3, normal)

    def _z_full_height_wall(self, other, near, here, my_floor, other_floor, my_ceiling,
                            other_ceiling):
        """Create the wall between a walkable sector and a solid neighbour.

        The wall covers only the height both sectors have in common.
        """
        if not (other_floor < my_ceiling and my_floor < other_ceiling):
            return
        other_flags = other.flags & 0xFE
        wall_bits = (other_flags ^ other.kind) & _WALL_BIT
        if other.kind & KIND_WALKABLE:
            if not self.hidden_bits & _WALL_BIT:
                return
            first, last = (here[1], here[2]) if other_floor < my_floor else (near[0], near[3])
            top = (here[6], here[5]) if my_ceiling < other_ceiling else (near[7], near[4])
            self._wall(4, _NORMAL_X_NEGATIVE, top[0], top[1], first, last)
        elif self.hidden_bits & KIND_WALKABLE and wall_bits:
            first, last = (here[2], here[1]) if other_floor < my_floor else (near[3], near[0])
            if other_floor < my_floor:
                top = (here[5], here[6]) if my_ceiling < other_ceiling else (near[4], near[7])
            else:
                top = (near[4], near[7]) if other_ceiling <= my_ceiling else (here[5], here[6])
            self._wall(4, _NORMAL_X_POSITIVE, top[0], top[1], first, last)

    # ------------------------------------------------------------------ walls towards X-1
    def _build_x_walls(self, other, near):
        """Create the walls between this sector and its neighbour at X - 1."""
        sector, here, faces = self.sector, self.first_point, self.sector.faces
        corners, far = sector.corners, other.corners
        my_floor = corners[1] + sector.floor * 2 + corners[0]
        other_floor = far[3] + other.floor * 2 + far[2]
        my_ceiling = corners[7] + sector.ceiling * 2 + corners[6]
        other_ceiling = far[5] + other.ceiling * 2 + far[4]
        y = self._y

        if other_floor < my_floor and other.kind & KIND_WALKABLE:
            self._x_lower_step_on_other(near, here, my_floor, other_ceiling)
        elif my_floor < other_floor and self.hidden_bits & KIND_WALKABLE:
            self._x_lower_step_on_this(near, here, other_floor, my_ceiling)
        if self._is_active(5):
            self._rotate(5, faces[5].rotation)
        if self._is_active(12):
            self._rotate(12, faces[12].rotation)

        if my_ceiling < other_ceiling and other.kind & KIND_WALKABLE:
            self._x_upper_step_on_other(near, here, my_ceiling, other_floor)
        elif other_ceiling < my_ceiling and self.hidden_bits & KIND_WALKABLE:
            self._x_upper_step_on_this(near, here, other_ceiling, my_floor)
        if self._is_active(6):
            self._rotate(6, faces[6].rotation)
        if self._is_active(13):
            self._rotate(13, faces[13].rotation)

        self._x_full_height_wall(other, near, here, my_floor, other_floor, my_ceiling,
                                 other_ceiling)
        if self.masks[self.index] & 0x80:
            self._rotate(7, faces[7].rotation)

    def _x_lower_step_on_other(self, near, here, my_floor, other_ceiling):
        """The neighbour's floor is lower: the step is seen from the neighbour, facing -Z."""
        y, n = self._y, _NORMAL_Z_NEGATIVE
        a2, a3, a6, a7 = near[2], near[3], near[6], near[7]
        low0, low1 = here[8], here[9]
        ya, yb = y(low0), y(low1)
        if other_ceiling < my_floor:
            if (y(a7) <= ya <= y(a3) and y(a6) <= yb <= y(a2)
                    and (ya != y(a7) or yb != y(a6)) and (ya != y(a3) or yb != y(a2))):
                self._wall(5, n, a6, a7, low0, low1)
                self._x_lower_tail(low1, low0, a3, a2, n)
                return
            self._wall(5, n, a6, a7, a3, a2)
            return
        if y(a3) < ya or y(a2) < yb:
            self._wall(5, n, here[1], here[0], a3, a2)
            return
        if ya == y(a3) and yb == y(a2):
            self._wall(5, n, here[1], here[0], a3, a2)
            return
        self._wall(5, n, here[1], here[0], low0, low1)
        self._x_lower_tail(low1, low0, a3, a2, n)

    def _x_lower_tail(self, a, b, c, d, normal):
        """Add the second part of a lower step."""
        if self._make_polygon(12, a, b, c, d):
            self._activate(12)
            self._set_normal(5, normal)

    def _x_lower_step_on_this(self, near, here, other_floor, my_ceiling):
        """This floor is lower: the step is seen from this sector, facing +Z."""
        y, n = self._y, _NORMAL_Z_POSITIVE
        far_a, far_b = near[10], near[11]
        ya, yb = y(far_a), y(far_b)
        p0, p1, p4, p5 = y(here[0]), y(here[1]), y(here[4]), y(here[5])
        if my_ceiling < other_floor:
            if (ya < p5 or p1 < ya or yb < p4 or p0 < yb
                    or (ya == p5 and yb == p4) or (ya == p1 and yb == p0)):
                self._wall(5, n, here[4], here[5], here[1], here[0])
                return
            self._wall(5, n, here[4], here[5], far_a, far_b)
            self._x_lower_tail(far_b, far_a, here[1], here[0], n)
            return
        if p1 < ya or p0 < yb:
            self._wall(5, n, near[3], near[2], here[1], here[0])
            return
        if ya != p1 or yb != p0:
            self._wall(5, n, near[3], near[2], far_a, far_b)
            self._x_lower_tail(far_b, far_a, here[1], here[0], n)
            return
        self._wall(5, n, near[3], near[2], here[1], here[0])

    def _x_upper_step_on_other(self, near, here, my_ceiling, other_floor):
        """This ceiling is lower: the step is seen from the neighbour, facing -Z."""
        y, n = self._y, _NORMAL_Z_NEGATIVE
        a2, a3, a6, a7 = near[2], near[3], near[6], near[7]
        row0, row1 = here[12], here[13]
        ya, yb = y(row0), y(row1)
        if my_ceiling < other_floor:
            if ya < y(a7) or y(a3) < ya or yb < y(a6) or y(a2) < yb:
                self._wall(6, n, a6, a7, a3, a2)
                return
            if (ya != y(a7) or yb != y(a6)) and (ya != y(a3) or yb != y(a2)):
                self._wall(6, n, a6, a7, row0, row1)
                self._x_upper_tail(row1, row0, a3, a2, n)
                return
            self._wall(6, n, a6, a7, a3, a2)
            return
        if ya < y(a7) or yb < y(a6) or (ya == y(a7) and yb == y(a6)):
            self._wall(6, n, a6, a7, here[4], here[5])
            return
        self._wall(6, n, a6, a7, row0, row1)
        self._x_upper_tail(row1, row0, here[4], here[5], n)

    def _x_upper_tail(self, a, b, c, d, normal):
        """Add the second part of an upper step."""
        if self._make_polygon(13, a, b, c, d):
            self._activate(13)
            self._set_normal(6, normal)

    def _x_upper_step_on_this(self, near, here, other_ceiling, my_floor):
        """The neighbour's ceiling is lower: the step is seen from this sector, facing +Z."""
        y, n = self._y, _NORMAL_Z_POSITIVE
        far_a, far_b = near[14], near[15]
        ya, yb = y(far_b), y(far_a)
        p0, p1, p4, p5 = y(here[0]), y(here[1]), y(here[4]), y(here[5])
        if other_ceiling < my_floor:
            if ya < p4 or p0 < ya or yb < p5 or p1 < yb:
                self._wall(6, n, here[4], here[5], here[1], here[0])
                return
            if (ya != p4 or yb != p5) and (ya != p0 or yb != p1):
                self._wall(6, n, here[4], here[5], far_a, far_b)
                self._x_upper_tail(far_b, far_a, here[1], here[0], n)
                return
            self._wall(6, n, here[4], here[5], here[1], here[0])
            return
        if ya < p4 or yb < p5 or (ya == p4 and yb == p5):
            self._wall(6, n, here[4], here[5], near[6], near[7])
            return
        self._wall(6, n, here[4], here[5], far_a, far_b)
        self._x_upper_tail(far_b, far_a, near[6], near[7], n)

    def _x_full_height_wall(self, other, near, here, my_floor, other_floor, my_ceiling,
                            other_ceiling):
        """Create the wall between a walkable sector and a solid neighbour along X."""
        if my_ceiling <= other_floor or other_ceiling <= my_floor:
            return
        if self.hidden_bits & _WALL_BIT and other.kind & KIND_WALKABLE:
            first, last = (here[0], here[1]) if other_floor < my_floor else (near[3], near[2])
            top = (here[5], here[4]) if my_ceiling < other_ceiling else (near[6], near[7])
            self._wall(7, _NORMAL_Z_NEGATIVE, top[0], top[1], first, last)
        elif self.hidden_bits & KIND_WALKABLE and ((other.flags & 0xFE) ^ other.kind) & _WALL_BIT:
            first, last = (here[1], here[0]) if other_floor < my_floor else (near[2], near[3])
            top = (here[4], here[5]) if my_ceiling < other_ceiling else (near[7], near[6])
            self._wall(7, _NORMAL_Z_POSITIVE, top[0], top[1], first, last)
