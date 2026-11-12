# The .TOM file format

A `.TOM` file is what the Tomb Raider Level Editor writes when a project is exported ("Output Wad").
It contains the textures used by the rooms, all room geometry, lighting and the level logic, such as
collision, pathfinding and object placement. Objects, animations and sounds are not in it: those live
in the `.WAD`, `.SWD`, `.SFX` and `.SAM` files next to it. The converter `tom2pc` combines all of them
into a `.TR4` game level (see [PYTOM2PC.md](../PYTOM2PC.md)).

The description is based on the export code of `winroomedit.exe` and verified against 11 levels: a
section-by-section comparison of the files written by the original export and by `pyprj2tom` agrees
in every byte the editor assigns (see [VERIFICATION.md](../VERIFICATION.md)).

**Where to find it in the code.** The file is written by
[`pyprj2tom/tom_writer.py`](../../src/pyprj2tom/tom_writer.py) in the order of the table below (sector
tables and floor data in [`floor_data.py`](../../src/pyprj2tom/floor_data.py), boxes and zones in
[`pathfinding.py`](../../src/pyprj2tom/pathfinding.py)). It is read by `pytom2pc`:
[`texture_sheets.py`](../../src/pytom2pc/texture_sheets.py) reads sections 1-4,
[`room_overview.py`](../../src/pytom2pc/room_overview.py) and
[`room_converter.py`](../../src/pytom2pc/room_converter.py) the rooms, and
[`level_writer.py`](../../src/pytom2pc/level_writer.py) copies most of the rest into the `.TR4`.

**Why the format looks like this.** Most sections after the rooms are already in the layout the game
uses at run time, so that `tom2pc` can copy them. Some sections are not used by the PC game at all:
the editor sector table and the portal map serve the editor, and the palette and light table serve
8-bit renderers. `tom2pc` skips them.

Conventions: all values are little-endian. `i16`, `u16`, `i32`, `u32` are integers, `f32` a float.
A *sector* is a 1024 x 1024 world unit grid cell. One *click* is 256 world units. World Y grows **downwards**.

## 1. File layout

| # | section | size |
|---|---|---|
| 1 | Version | 4 |
| 2 | Texture table: tiles and attributes | 2 + 8n + n |
| 3 | Texture pages | 4 + data |
| 4 | Palette | 0x300 |
| 5 | Rooms | 2 + rooms |
| 6 | Floor data | 4 + 2n |
| 7 | Cameras, then flyby cameras | 4 + 16n, 4 + 40n |
| 8 | Sound sources | 4 + 16n |
| 9 | Boxes, then overlaps | 4 + 20n, 4 + 2n |
| 10 | Zones | 10 x 2 x number of boxes |
| 11 | Animated texture ranges | 4 + 8n |
| 12 | Items | 4 + 24n |
| 13 | Light table | 0x2000 |
| 14 | Portal map | 4 + n |
| 15 | AI objects | 4 + 24n |

## 2. Version

`u32`. The low 16 bits hold the object version from `objects.h` (0x81 in TRLE). The high 16 bits are
`0xFF18` when textures are 24-bit and `0xFF08` when they are 8-bit. `tom2pc` only accepts files where
bits of `0xFF180000` are set.

## 3. Texture table and pages

```
u16 tile_count
tile_count x { i32 offset; u16 width; u16 height }
tile_count x u8 attribute
i32 page_bytes
u8  pages[page_bytes]
u8  palette[0x300]
```

The project's texture image is cut into 64 x 64 pixel *cells*. Only cells used by a polygon, or lying in
a used animated range, are stored. Cells are renumbered in ascending order of their original number and
placed four per row, so that one row of cells is 256 x 64 pixels.

* `page_bytes` is `rows * 0xC000` for 24-bit RGB textures (`rows * 0x4000` for 8-bit), where
  `rows = (cell_count + 3) >> 2`. If the last row has fewer than four cells, its free positions are not
  blank: the writer keeps the source positions of the previous row, moved on by one row of cells, so
  they show whatever lies 64 pixels below the previous row's cells. If that is past the end of the image they
  are zero, because the editor's image buffer is twice the height of the image and the second half is
  never written (seen in `karnak`, whose last row has one cell).
* `offset` is a *linear offset* of the tile in this image: `row_y * 256 + x`, where each texture row is 256 pixels.
* `width` and `height` are 8.8 fixed point as used by texture coordinates: `(pixels - 1) * 256 + 255`.
* `attribute` is the project's bump-mapping setting for the cell (0 none, 1 and 2 are the bump levels).
* `palette` has 256 RGB entries in 6-bit VGA format (0-63). It is produced by colour quantisation and is
  not used by the PC game.

Only tiles that are used are written. The texture numbers in the mesh refer to this reduced list.

## 4. Rooms

```
u16 room_count
room_count x Room
```

Rooms are numbered in file order; a project's empty room slots do not occur. All room references in
the file use this new numbering.

### 4.1 Room layout

| field | type | meaning |
|---|---|---|
| x, z | i32 | world position (`grid * 1024`) |
| y_bottom, y_top | i32 | lowest floor and highest ceiling, in world Y (`-256 * clicks`) |
| size_z, size_x | u16 | sector grid size. X count first in the file, then Z |
| flags | u16 | room flags; 2 is added for an alternate room with the same position, size and floor as its base |
| mesh_words | i32 | size of the mesh in 16-bit words |
| Mesh | | see 4.2 |
| Doors | | see 4.3 |
| Sectors (game) | | see 4.4 |
| Sectors (editor) | | see 4.5 |
| Ambient light | 4 bytes | R, G, B, unused byte |
| Unknown | u16 | |
| light_count | u16 | |
| Lights | 46 bytes each | see 4.6 |
| static_count | u16 | |
| Static meshes | 20 bytes each | see 4.7 |
| Tail | 8 bytes | see 4.8 |

### 4.2 Mesh

```
u16 vertex_count (including one entry per sprite)
vertex_count x { i16 x, y, z; u16 shade1, shade2, attributes }
u16 polygon_count (including sprites)
polygons
```

Vertex positions are relative to the room. Sprites are appended as extra "vertices" whose fields are
`x, y, z, tint1, tint2, 0`.

Polygons start with a kind word. `v` are vertex numbers:

| kind | shape | content after the kind |
|---|---|---|
| 0x20 | textured triangle, image has transparent pixels | v0 v1 v2, texture, sector x, sector z |
| 0x21 | textured quad, image has transparent pixels | v0 v1 v2 v3, texture, sector x, sector z |
| 0x22 | flat-colour triangle | v0 v1 v2, colour, sector x, sector z |
| 0x23 | flat-colour quad | v0 v1 v2 v3, colour, sector x, sector z |
| 0x24 | textured triangle, opaque image | v0 v1 v2, texture, sector x, sector z |
| 0x25 | textured quad, opaque image | v0 v1 v2 v3, texture, sector x, sector z |
| 0x27 | sprite | vertex, sprite number |

Texture words: bits 0-9 tile number, bit 10 double-sided, bit 11 additive blending,
bits 13-15 orientation of triangles, and bit 15 "mirrored" for quads. `tom2pc` drops the flat-colour
polygons, which only the editor displays.

### 4.3 Doors (portals)

```
u16 count
count x { u16 adjoining_room; i16 normal_x, normal_y, normal_z; 4 x { i16 x, y, z } }
```

The room number is the renumbered one. Openings in floors have `normal_y = -1`, in ceilings `1`.

### 4.4 Sectors for the game

```
u16 x_count; u16 z_count
x_count * z_count x {
    u16 floor_data_index   (0 = none)
    u16 box_and_material   ((box number << 4) | material)
    u8  room_below         (0xFF = none)
    i8  floor              (clicks, world Y / 256)
    u8  room_above         (0xFF = none)
    i8  ceiling
}
```

Sectors are stored column by column. The floor of a *wall* sector is `-127`, the "no sector" value.
Sectors with a slope store their highest corner in `floor` and their lowest ceiling corner in `ceiling`,
and the shape in the floor data.

### 4.5 Sectors for the editor

```
u16 x_count; u16 z_count
x_count * z_count x {
    u16 floor_tilt      ((z_tilt << 8) | x_tilt)
    u16 ceiling_corners (four 4-bit values)
    u16 sector_type     (accumulated, see below)
    i16 floor_height    (world Y)
    i16 ceiling_height  (world Y)
    u16 room_below      (0xFF = none)
    u16 room_above
}
```

`tom2pc` skips this table. One quirk: `sector_type` is the OR of all sector types of the room up to this
sector, because the variable it is built in is not reset between sectors.

### 4.6 Lights

Record of 46 bytes (`0x2E`), the same layout the game uses:

| offset | type | field | used by types |
|---|---|---|---|
| 0x00 | i32 x3 | position | point, spot, shadow, fog |
| 0x0C | u8 x3 | colour R, G, B | all |
| 0x0F | u8 | type: 0 sun, 1 point, 2 spot, 3 shadow, 4 fog | all |
| 0x10 | i16 | intensity (negative for shadow bulbs) | all |
| 0x12 | f32 | inner range (spots: cosine of inner angle) | point, spot, shadow, fog |
| 0x16 | f32 | outer range (spots: cosine of outer angle) | point, spot, shadow, fog |
| 0x1A | f32 | spot length | spot |
| 0x1E | f32 | spot cutoff | spot |
| 0x22 | f32 x3 | direction | sun, spot |

#### The bytes that change from one export to the next

Exporting the same project twice gives files that differ in a few hundred bytes (190 in one `karnak`
test, up to 732 between the original tool and the Python port). **All of these are in the light records.**

They are the fields which a light's type does not use, in the "used by types" column above: a sun
has no position or range, and a point, shadow or fog light has no spot length, cutoff or direction.

* **What they are:** leftovers of uninitialised memory. The exporter fills one 46-byte structure on the
  stack, setting only the fields needed for the current light type, and writes all 46 bytes. It never
  clears the structure. Each record therefore keeps what the previous light left there, and the first
  record of a room shows whatever the preceding sector export left on the stack. Those are partly
  heap addresses, which is why they differ between runs of the same program.
* **Is it a bug?** Yes, a small one in the original C code: a missing `memset`. It is harmless because
  no reader depends on the values.
* **Does Tom2Pc use them?** No. It copies each light record to the `.TR4` without looking at it. In a test, these bytes in `tut1` were set to 00 and then
  to A5 before conversion: **only those 2,068 bytes changed** in the decompressed level. Textures, sounds and the
  rest of the level stayed identical, and only the zlib-compressed size varied a little (4,063,804 vs 4,063,833
  bytes).
* **Effect in the game:** none visible. `tomb4.exe` copies each record into its runtime light table,
  but what it does next depends on the type: suns skip position and range tests altogether, point
  and shadow lights use only position and range, spots use all their fields, which the editor sets, and fog
  bulbs use only position, red and outer range. The field at 0x1A is not read at all.

`pyprj2tom` writes zeros in these fields. Comparisons of its output with the original therefore skip exactly these fields and compare everything else strictly.

### 4.7 Static meshes

```
i32 x, y, z  (world position)
i16 rotation
u16 tint1, tint2
i16 static_number
```

### 4.8 Room tail

| field | type | meaning |
|---|---|---|
| alternate room | i16 | flip room's new number, or -1 |
| flags | u16 | room flags; 0x40 is added unless a neighbouring room has flag 8 set |
| water strength | u8 | `tom2pc` replaces it with the computed water scheme |
| reflection level | u8 | used for the water scheme, then dropped |
| reverb | u8 | |
| alternate group | u8 | |

## 5. Floor data

```
i32 word_count
u16 words[word_count]
```

The first word is a dummy so that index 0 can mean "no data". Each sector's list ends with bit 15 set in
its last function word. Function words:

| low bits | function | following data |
|---|---|---|
| 1 | portal to another room | u16 room |
| 2 | floor slope (one tilt direction) | u16 `(z_tilt << 8) \| x_tilt` |
| 3 | ceiling slope | u16, the same |
| 4 | trigger | u16 setup word, then action words; the list ends with bit 15 |
| 5 | kill Lara ("lava") | - |
| 6 | climbable walls | bits 8-11 are the climb directions |
| 7, 8 | split floor, the two diagonal directions | u16 corners |
| 9, 10 | split ceiling, the two diagonal directions | u16 corners |
| 11-14 | split floor with a collision-free or portal half | u16 corners |
| 15-18 | split ceiling with a collision-free or portal half | u16 corners |
| 19 | monkey-swing | - |
| 20 | minecart trigger left | - |
| 21 | minecart trigger right | - |

Triangulated sectors store in their function word two signed 5-bit "height differences" of the two
triangles, in bits 5-9 and 10-14.

## 6. Cameras

```
i32 count; count x { i32 x, y, z; i16 room_or_strength; u16 flags }      (normal cameras and sinks)
i32 count; count x { 40 byte flyby record, sorted by sequence and index }
```

## 7. Sound sources

```
i32 count
count x { i32 x, y, z; i16 sound; u16 flags }
```

The flag value contains the flip-room state: `0xC0`/`0x40`/`0x80` added for rooms without alternate, with alternate, and
for the alternate copy.

## 8. Boxes, overlaps and zones

These are the data for enemy pathfinding.

```
i32 box_count
box_count x { i32 x_min, x_max, z_min, z_max; i16 height; u16 overlap_index }
i32 overlap_word_count
u16 overlaps[overlap_word_count]
```

Zones follow without a count: **ten** tables with one `u16` for each box. There are two groups of
five tables for the normal state and for the state with flipmaps applied. The five tables of each group are
for the different enemy movement types (ground 1-4 and flyers).

## 9. Animated ranges and items

```
i32 range_count; range_count x { i32 first_tile, last_tile }
i32 item_count;  item_count x 24 byte item record, sorted by object number
```

## 10. Light table (`0x2000` bytes)

32 tables of 256 bytes: for each of 32 brightness levels, the palette index that best matches each
colour when darkened. `tom2pc` skips it. It is only used by 8-bit renderers.

The table is calculated from a 32 KB table of 5:5:5 colours, but the calculation can produce indices of up to 61
per colour channel for bright colours. In that case the exporter reads past the end of its colour table into
neighbouring memory: mostly zeros, but also palette data and editor settings. The bytes this produces depend on the state of the editor, and a few of them differ between exports.

## 11. Portal map

```
i32 byte_count
pairs of u16 values
```

A list of line segments (start and end, X and Z) drawn on the editor's 2D map. Each room's list is closed by
`0xFFFF, 0xFFFF`, and the end of the data by another such pair. `tom2pc` skips it.

## 12. AI objects

```
i32 count
count x 24 byte record, sorted (object number first)
```

Sorted with the Microsoft C runtime `qsort`, which is not stable, so the sort order of equal entries is
also part of the format.
