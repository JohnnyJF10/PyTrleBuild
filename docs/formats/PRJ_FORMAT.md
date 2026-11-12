# The .PRJ project file format

A `.PRJ` file is a saved project of the Tomb Raider Level Editor (`winroomedit.exe`). It holds the editable
level: rooms, sectors, walls and their textures, placed objects, lights and triggers. It is the input of the
PRJ-to-TOM export ([PYPRJ2TOM.md](../PYPRJ2TOM.md)).

A project stores what the designer *did*, not what the game needs: heights in clicks, wall textures
per sector face, placed objects with their editor properties. Nothing derived (polygons, vertex
colours, collision, pathfinding) is stored; the export computes all of it. The format is a straight
dump of the editor's in-memory records, which is why it contains fields that are recomputed on
loading (world positions), free lists of unused records, and fixed-size tables.

**Where to find it in the code.** [`pyprj2tom/project_reader.py`](../../src/pyprj2tom/project_reader.py)
reads the file into the data classes of [`project_model.py`](../../src/pyprj2tom/project_model.py), in
the order of the table in section 1.

This description comes from the editor's own load and save code and was cross-checked against Tomb Editor's
`PrjLoader.cs`, which supplied several field meanings. All 11 sample projects are version `0x31`, the version
this editor writes. The appendix lists what the loader does for older versions.

Conventions: little-endian. `i8`/`u8`, `i16`/`u16`, `i32` are integers, `f32` a float. Heights are in *clicks* of 256 world
units; sector grids are 1024 units per cell.

## 1. File layout

| # | content |
|---|---|
| 1 | `"PROJFILE"` (8 bytes), `i32 version` |
| 2 | `i32 room_count` |
| 3 | `room_count` room records |
| 4 | object free list: `i32 free_count`, `i32 capacity`, `capacity x i32` |
| 5 | light free list: `i32 free_count`, `0x300 x i32` |
| 6 | trigger free list: `i32 free_count`, `0x200 x i32` |
| 7 | texture path token |
| 8 | texture tile table |
| 9 | WAD path token and slot table |
| 10 | animated texture ranges |
| 11 | 256 bytes: footstep material per texture cell |
| 12 | 256 bytes: bump-map attribute per texture cell |

The free lists contain indices of unused records. The editor rebuilds them after loading, so they do not
affect the export.

**Path tokens** are text terminated by a *space*, not by a zero byte. The value `NA ` means "no file". The
paths are absolute (`C:\PROGRA~1\COREDE~1\TRLE\...`) in all sample projects: Windows paths with 8.3
short names, as the editor saw them on the computer that saved the project. A consequence of the
space terminator is that a path containing a space cannot be stored at all; the 8.3 short names
contain none. `pyprj2tom` translates these paths into a TRLE folder or takes the files from the
command line instead (see [USAGE.md](../USAGE.md#how-the-input-files-are-found)).

## 2. Room record

The record begins with `i16 unused`. A non-zero value means an empty slot: nothing else follows for this room.

| field | type | meaning |
|---|---|---|
| unused | i16 | 0 = room exists |
| name | 80 bytes | |
| world position | 18 bytes | recomputed by the editor from the grid position |
| reserved | i16 x2 | |
| size_x, size_z | i16 x2 | sector counts, including the border |
| position_x, position_z | i16 x2 | grid position of the room |
| id | i16 | -1 means a broken room, which the editor reports and repairs |
| door_count | i16 | |
| door object ids | `door_count x i16` | |
| door records | object records | one for each id |
| object_count | i16 | |
| object ids | `object_count x i16` | |
| object records | object records | see section 4, with triggers |
| ambient | 4 bytes | R, G, B, 0 |
| light_count | i16 | the list also holds cameras and sound sources |
| light ids | `light_count x i16` | |
| light records | object records | see section 4, with light data |
| flip room | i16 | index of the alternate room, or -1 |
| flags | u16 | see below |
| water level | u8 | also the strength of snow and rain |
| mist or reflection level | u8 | |
| reverb | u8 | |
| flip group | u8 | (low byte of a word in `PrjLoader.cs`) |
| extra | u8 | |
| sectors | `size_x * size_z` records | ordered row by row in Z, then X; see section 3 |

Room flags (from `PrjLoader.cs`, confirmed where the exporter uses them):

| bit | meaning |
|---|---|
| 0x0001 | water |
| 0x0002 | this room is the alternate copy in a flip pair |
| 0x0004 | quicksand |
| 0x0008 | horizon / sky visible |
| 0x0010 | damage |
| 0x0020 | outside |
| 0x0080 | no lens flare |
| 0x0100 | mist effect |
| 0x0200 | reflection effect |
| 0x0400 | snow |
| 0x0800 | rain |

## 3. Sector record

In memory each sector is 142 bytes. In the file, its parts come in this order:

| field | type | meaning |
|---|---|---|
| type | u16 | see below |
| flags1 | u16 | see below |
| floor | i16 | height in clicks |
| ceiling | i16 | height in clicks |
| floor corners | i8 x4 | offsets from `floor` for corners: X+Z-, X-Z-, X-Z+, X+Z+ |
| ceiling corners | i8 x4 | offsets from `ceiling` for corners: X+Z+, X-Z+, X-Z-, X+Z- |
| floor 2 corners | i8 x4 | second floor layer (walls), same corner order as floor |
| ceiling 2 corners | i8 x4 | second ceiling layer, same corner order as ceiling |
| faces | 14 x 8 bytes | see below |
| flags2 | u16 | see below |
| flags3 | u16 | bit 0: alternate diagonal for the floor split |

Sector types:

| value | meaning |
|---|---|
| 1 | floor |
| 3 | floor with an opening in the floor (bit 2) |
| 5 | floor with an opening in the ceiling (bit 4) |
| 7 | floor with both openings |
| 6 | door: a border position containing a portal |
| 0x0E | wall |
| 0x1E | border wall |

`flags1`:

| bit | meaning |
|---|---|
| 0x0001 | contains a trigger |
| 0x0002 | floor opening |
| 0x0004 | ceiling opening |
| 0x0008 | wall opening (portal in a wall sector) |
| 0x0010 | death |
| 0x0020 | blocks enemy paths ("box") |
| 0x0040-0x0200 | climbable walls: +Z, +X, -Z, -X |
| 0x0400 / 0x0800 / 0x1000 | ceiling / floor / wall opening is traversable although drawn opaque |
| 0x4000 | monkey-swing |

`flags2`: `0x06` no collision on the floor, `0x18` no collision on the ceiling, `0x20` trigger triggerer,
`0x40` beetle marker.

Each of the 14 **faces** has this layout:

| offset | type | field |
|---|---|---|
| 0 | u16 | kind: 7 = textured, other values are flat colours, 0 = none |
| 2 | u8 | texture number, low bits |
| 3 | u8 | texture flags: bits 0-1 texture number high bits; 0x04 double-sided; 0x08 additive blending; 0x80 mirrored |
| 4 | u8 | rotation |
| 5 | u8 | triangle selection (which half of a tile) |
| 6 | u16 | not used by the export |

The exporter handles the two 8-bit texture fields as one 16-bit word; see the mesh section of [TOM_FORMAT.md](TOM_FORMAT.md).

## 4. Object records

Doors, objects, lights, cameras, sound sources, sinks and triggers share a record format.

| offset (memory) | type | field |
|---|---|---|
| 0x00 | i16 | object type, see below |
| 0x02 | i16 | sector X in room |
| 0x04 | i16 | sector Z in room |
| 0x06 | i16 | size X in sectors |
| 0x08 | i16 | size Z in sectors |
| 0x0A | i16 | height offset for wall-mounted objects |
| 0x0C | i16 | room |
| 0x0E | i16 | linked record: door partner, slot, light data, trigger data |
| 0x10 | i16 | flags |
| 0x12 | i16 | placement: low 4 bits face, high bits side |
| 0x14 | i32 x3 | world position X, Y, Z |
| 0x20 | i16 | rotation X |
| 0x22 | i16 | rotation Y; for all types except flyby cameras only bits 13-15 are kept when loading |
| 0x24 | i16 | roll / field of view |
| 0x26 | u16 | tint or shade |
| 0x28 | i16 | second tint or flags |

Object types:

| type | class | meaning |
|---|---|---|
| 1, 2, -2, -3 | door | wall portals, four sides |
| 4 | door | floor portal |
| -5 | door | ceiling portal |
| 8 | item | moveable or static mesh |
| 0x10 | trigger | |
| 0x4000 | light | point light |
| 0x4020 | light | fog bulb |
| 0x4100 | light | spot light |
| 0x4200 | light | sun |
| 0x5000 | light | effect light |
| 0x6000 | light | shadow bulb |
| 0x4040 | camera | flyby camera |
| 0x4080, 0x4800 | camera | normal camera variants |
| 0x4400 | sink | |
| 0x4C00 | sound source | |

**Light data** (30 bytes) follows the record of any object of class light:

| offset | type | field |
|---|---|---|
| 0x00 | u16 | intensity |
| 0x02 | f32 x5 | inner, outer, X angle, Y angle, length |
| 0x16 | f32 | cutoff |
| 0x1A | u8 x3 | colour |
| 0x1D | u8 | on (0 = disabled, ignored by the export) |

**Trigger data** (10 bytes) follows every trigger record:

| offset | type | field |
|---|---|---|
| 0 | i16 | trigger type |
| 2 | i16 | target |
| 4 | i16 | timer |
| 6 | i16 | one shot and activation mask |
| 8 | i16 | target kind |

## 5. Texture table

```
i32 tile_count
tile_count x { i32 linear_offset; u16 width; u16 height }
```

`linear_offset` is `y * 256 + x` in the texture image. While loading, the first 256 entries are forced to be the
64 x 64 pixel grid in row order, so entries that disagree are corrected.

The texture file itself is a separate TGA (or PCX) named by the path token. TGA files must be uncompressed 24-bit images.
Their height is rounded up to a multiple of 64.

## 6. WAD reference and slots

```
token  wad_path
i32    slot_count
slot_count x {
    i16 used
    if used != 0:
        token name
        i32 original_slot_index
        108 bytes  (only if the name still matches a slot in the .WAD; otherwise they are skipped)
}
```

The slot name is matched against the names from `objects.h` ([object list](OBJECT_LIST.md)): the longest name that the stored text starts with
wins. Placed items in rooms are re-indexed with this mapping. An item whose slot no longer exists is removed.

The 108 bytes are an item's collision grid: four `i16` (rectangle offset and size) and two 5 x 5 tables of `i16`.

## 7. Animated ranges

```
i32 count_used
i32 list[40]
i32 table[256]
40 x { i32 used; i32 first_tile; i32 last_tile }
```

## 8. Appendix: older versions

The editor reads these older versions but no sample project uses them, so they are not verified, and
`pyprj2tom` stops with a clear message when it meets them. All values are the *version* at which something changes.

| version | difference |
|---|---|
| < 0x14 | no room count: always 100 rooms |
| < 0x06 | object and light free lists in each room instead of at the end |
| < 0x18 | object free-list capacity is implicit (2000; 1000 before 0x10) |
| < 0x26 | light free list has 500 entries |
| < 0x30 | trigger free list has 300 entries; < 0x0E none |
| < 0x07 | no texture or WAD path |
| < 0x08 | no WAD path |
| < 0x13 | no texture tile table |
| < 0x0D | no animated ranges |
| < 0x24 | no footstep table (every cell is 6) |
| < 0x28 | one brightness word instead of four ambient bytes; only 8 sector corners; only 10 faces per sector |
| < 0x20 | only 8 faces; texture flags in a 16-bit value |
| < 0x19 | sector type and flags are single bytes |
| < 0x22 | no `flags2` |
| < 0x2F | no `flags3` |
| < 0x21, 0x25, 0x2D, 0x31 | the last five room bytes are missing or derived |
| < 0x2B, 0x2C, 0x2E | light data is reduced |
| < 0x2C | no second tint |
| < 0x05 | no ambient, and door and object positions are absolute |
| < 0x03 | heights are stored in half clicks |
| < 0x02 | another face layout (6 faces) |
