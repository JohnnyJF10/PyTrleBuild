# pytom2pc: from level file (`.TOM`) to game level (`.TR4`)

`tom2pc.exe` is the last step of the Tomb Raider Level Editor's tool chain. It takes the `.TOM` written
by the editor's export ([PYPRJ2TOM.md](PYPRJ2TOM.md)) and the object, sprite and sound files of the
level, and builds the `.TR4` file that `tomb4.exe`, the PC version of the game, loads.

`pytom2pc` is a readable Python reimplementation of `tom2pc.exe`. It produces **byte-identical**
files on all test levels. This document explains what the converter does and points to the module
that does each step. The vocabulary is explained in [CONCEPTS.md](CONCEPTS.md); how to run it is in
[USAGE.md](USAGE.md#4-pytom2pc-level-file-tom-to-game-level-tr4).

> **Study code.** The goal is to understand and document the original behaviour, down to the byte.
> The program is not meant for building real levels.

Contents: [1 Inputs and outputs](#1-inputs-and-outputs) · [2 Steps](#2-the-steps) · [3 Rooms](#3-rooms) ·
[4 Texture records](#4-texture-records) · [5 Repacking](#5-repacking-textures-for-the-pc) ·
[6 Container](#6-the-tr4-container) · [7 Quirks](#7-behaviour-kept-on-purpose) ·
[8 Verification](#8-verification) · [9 Source code](#9-the-source-code)

## 1. Inputs and outputs

| file | role | required | option |
|---|---|---|---|
| `level.tom` | room data and room textures ([format](formats/TOM_FORMAT.md)) | yes | positional |
| `level.wad` | objects: meshes, animations, object textures | yes | `--wad` |
| `level.swd` | sprites and sprite sequences | yes | `--swd` |
| `level.sfx` | sound effect table, copied as it is | yes | `--sfx` |
| `level.sam` | text list of the sound sample files, one per line | no | `--sam` |
| `level.raw` | sky image (256 x 256 RGB); otherwise `pcsky.raw` | no | `--sky`, `--default-sky` |
| `font.pc` | font pages for the level | no | `--font` |
| `level.cin` | cut-scene data | no | `--cin` |
| `*.wav` | the sound samples named in the `.sam` | no | `--samples` (folder) |

`tom2pc.exe` read all of these from the directory of the `.tom`; it derived every name from the name
of the `.tom`. `pytom2pc` does the same for every file not given explicitly, and matches names
without regard to letter case on every system (the shipped files mix `Catacomb.TOM` with
`catacomb.sam`). The result is written to `--dest` as `level.tr4`, or to the file given with `-o`.

Missing `.wad`, `.swd`, `.tom` or `.sfx` content stops the conversion. A missing `.sam` still gives a
file, without samples; a missing sample file is reported and skipped.

The three texture-carrying files must belong together: the versions of the `.wad` and `.swd` must
equal the low 16 bits of the `.tom` version, i.e. the object version of the [object list](formats/OBJECT_LIST.md).

## 2. The steps

The order is in [`converter.py`](../src/pytom2pc/converter.py).

| step | module | what happens |
|---|---|---|
| 1 Read texture headers | `texture_sheets` | Tile lists and images of `.tom`, `.wad` and `.swd`, and the version cross-check. |
| 2 Stack the images | `texture_sheets` | All source images are put into one stack of 256 x 256 pages: rooms start at page 0, objects on the page after the last room tile, sprites after the objects. |
| 3 Find transparency | `texture_sheets` | A tile is transparent if it contains any pure magenta pixel. |
| 4 Place sprites | `sprite_atlas`, `rectangle_search` | Sprites are moved into free page space, largest first, using a first-fit search on an occupancy map. |
| 5 Convert object meshes | `object_data` | Each distinct mesh once; polygons are sorted into quads then triangles; tile numbers become texture records (section 4). |
| 6 Write pages | `level_writer` | Pages as 32-bit colour (magenta becomes transparent black), then font and sky. |
| 7 Water analysis | `room_overview`, `vertex_effects` | All rooms are scanned first, because the effects on a vertex depend on the neighbouring rooms. |
| 8 Convert rooms | `room_converter` | Section 3. |
| 9 Write the rest | `level_writer` | Mesh block, pass-through `.wad` sections (animations, skeletons, frames), sprites, cameras, boxes, animated ranges, texture records, items, AI objects, cut-scene, and finally the `.sfx`. |
| 10 Repack textures | `texture_repacker` | Section 5. |
| 11 Compress and append samples | `tr4_container` | Section 6. |

Most of the level is passed through: the `.TOM` already contains the floor data, boxes, zones, items
and cameras in the game's format, and the `.WAD` the animations. The real work of `tom2pc` is in
the geometry formats and, above all, in the textures.

## 3. Rooms

Compared with the `.TOM`, a room is changed in these ways ([`room_converter.py`](../src/pytom2pc/room_converter.py)):

* **Vertices** are widened to six words: `x, y, z, colour, attributes, colour`. Red and blue in the
  15-bit vertex colour are exchanged.
* **Polygons** are sorted into quads, triangles and sprites. Flat-colour polygons are dropped (only the
  editor displays them). Each texture word becomes the number of a texture record.
* **The second sector table** of the `.TOM` is dropped; it is used by the editor only.
* **Ambient colour** is reordered to a 32-bit BGR value.
* **Lights and static meshes** are copied as they are. This includes the uninitialised light fields
  explained in the [TOM format](formats/TOM_FORMAT.md#the-bytes-that-change-from-one-export-to-the-next).
* **The last bytes** of a room become `alternate room, flags, water scheme, reverb, alternate group`.

### Water effects on vertices

The game animates some vertices: *shimmer* (a flicker of the vertex colour, as light through water)
and *wave* (a vertical movement of the vertex, used for water surfaces). Which vertices move is
decided here, from the room flags and the neighbouring rooms ([`vertex_effects.py`](../src/pytom2pc/vertex_effects.py)):

| situation | effect |
|---|---|
| Vertex in a water room | shimmer (`0x4000`); water scheme 0 |
| Vertex not at the room's lowest floor level | unchanged |
| Room flag `0x100` and a floor opening without a vertex below it | shimmer; scheme `reflection + 1` |
| Vertex at an opening in the floor to a water room, with no matching vertex below it | wave (`0x2000`); the scheme comes from the strongest water neighbour: `reflection + (strength * 4) + 5`; if the room has flag `0x200`, shimmer too |
| As above, but the room below has flag `0x80` (quicksand in community documentation) | wave; scheme 0 |
| Vertex at a side opening to a water room that has the same vertex | scheme as above, shimmer only if flag `0x200` |

Only vertices used by textured polygons of the neighbouring room count as "matching".

## 4. Texture records

In the game, a polygon does not refer to an image directly but to a 38-byte **texture record** (often
called "object texture" or "texture info") with page, corner coordinates and flags
([`texture_info_table.py`](../src/pytom2pc/texture_info_table.py)). Records are created on demand in
the order polygons are met, first objects, then rooms.

* A new request reuses an existing record if tile, attribute and all four corners are equal.
* A triangle request also finds **quad** records with the same first three corners.
* The attribute is `2` (additive blending) when a room texture has its additive bit set. Otherwise it
  is the tile's transparency flag: 0 opaque, 1 with transparent pixels.
* Room records have flag `0x8000`, plus `0x0800` or `0x0400` for the two bump mapping levels.
* After the rooms, one record is requested for every room tile, whether used or not.

### Animated textures

A polygon whose tile is inside an animated range registers its sub-rectangle and orientation with
that range ([`animated_textures.py`](../src/pytom2pc/animated_textures.py)). Ranges are matched using
the 64-pixel grid cell of the tile. Each distinct use then creates one texture record per animation
frame. The output is a table of those record numbers, followed by the "UV ranges" byte, which is the
group count of the first range.

## 5. Repacking textures for the PC

Until this step, the texture pages contain the source images as they were: whole 64 x 64 cells of the
room texture, the object texture sheet of the WAD, the sprite sheet. Most of that space is unused. The
PC format wants only the used tiles, grouped into page sets for rooms, objects and bump-mapped
textures. This pass creates them ([`texture_repacker.py`](../src/pytom2pc/texture_repacker.py)):

1. One item per sprite and texture record, sprites first. Items are then sorted by area, largest
   first. The sort is the original's unstable `qsort`.
2. Each texture chooses its group: `bump` if it has a bump flag, otherwise `room` or `object`. Sprites
   always go on object pages.
3. A texture whose top-left region matches the pixels of a placed tile shares that tile.
4. Everything else goes to the first free position on its group's pages (first page, then lowest row,
   then lowest column).
5. Bump tiles get a second image: each pixel averaged with the inverted pixel 1 or 2 positions down
   and right, a simple emboss.
6. All records are updated to the new position.
7. Pages are written as 32-bit, then repeated as 16-bit ARGB1555. Room pages come first, then object
   pages, then both bump sets.

## 6. The `.TR4` container

```
'TR4\0', u16 room_pages, u16 object_pages, u16 bump_pages (both sets)
{ u32 size, u32 compressed_size, zlib data }  x4: 32-bit pages, 16-bit pages, font + sky, level
i32 sample_count
{ u32 size, u32 size, wave file }  per sample
```

The compression is zlib level 6 ([`tr4_container.py`](../src/pytom2pc/tr4_container.py)). The samples
are whole `.wav` files, appended uncompressed after the level.

**Byte-exact output needs a classic zlib.** The deflate format allows many encodings of the same data.
Python 3.14 and later on Windows link zlib-ng, whose output decompresses to the same data but whose
compressed bytes differ from the classic zlib that `tom2pc.exe` used.
[`classic_zlib.py`](../src/trle_common/classic_zlib.py) therefore looks for a classic zlib: Python's
own if it is classic (the normal case on Linux and macOS), the library named by `CLASSIC_ZLIB_DLL`,
the `zlib1.dll` of Git for Windows, or the system's `libz`. Without one the level is still valid;
only the compressed bytes differ.

## 7. Behaviour kept on purpose

The Python version reproduces these details of the original, because the files would otherwise differ.

| behaviour | consequence |
|---|---|
| The attribute table has only 256 entries | With more than 256 room tiles (`catacomb`, `coastal`), attributes 256-259 are the bytes of the `.wad` texture section's end offset. |
| Duplicate texture check tests only the top-left offset | A tile that occurs elsewhere inside a larger one is not shared. |
| Page count uses `% 0xFFFF` | A group can be reported one page larger than it is. |
| Sprites do not select a page counter | They update the counter of the texture placed before them. Before any texture is placed, this is the first texture record itself. |
| The level block reads 6 bytes more than it has | The level block ends with 6 zero bytes. |
| Sample without a file | Writes the previous sample's size and nothing else, and carries on. |
| `.SAM` line ends | Expected to be CR LF: two characters are skipped after each name. |
| Sprite search excludes the last row and column | A sprite as wide or tall as a whole page can never be placed. |
| Limit of 20 pages per group and 16 animated ranges | Larger levels fail, as they do with the original tool. |

Deliberate differences, which do not change the output of a successful conversion:

* The original cuts paths at the *first* dot, so it fails if a directory name contains a dot. The
  Python version handles those paths normally.
* Every input file can be given explicitly, and names are matched without regard to letter case.

## 8. Verification

Run on the eight levels of the TRLE installation (tut1, title, catacomb, city, cleopal, coastal,
karnak, settomb), `pytom2pc` produces `.tr4` files **byte-identical** to those written by
`tom2pc.exe` from the same input files (run under Wine for the comparison; the output was also
identical on Linux with Python 3.12).

Note that two of the `.tr4` files shipped in the installation's `data` folder, `catacomb` and
`settomb`, were built from older versions of their `.tom` files; running `tom2pc.exe` on the shipped
`.tom` files gives different files, and `pytom2pc` matches those. See [VERIFICATION.md](VERIFICATION.md).

Paths with no test data (a `.wad` of version 0x82, for instance) are untested.

## 9. The source code

The package is in [`src/pytom2pc/`](../src/pytom2pc/). A good reading order is `converter` →
`texture_sheets` → `sprite_atlas` → `texture_info_table` → `object_data` → `room_converter` →
`level_writer` → `texture_repacker` → `tr4_container`.

| module | job |
|---|---|
| `cli` | the command line |
| `converter` | the order of the steps |
| `level_files` | locating the input files: explicit paths, or beside the `.tom` |
| `texture_sheets` | reading the texture headers and images of `.tom`, `.wad`, `.swd`; page layout; transparency |
| `sprite_atlas`, `rectangle_search` | the shared texture pages and sprite placement |
| `texture_info_table` | texture records and their de-duplication |
| `object_data` | object data from the `.WAD` and mesh conversion |
| `room_overview`, `vertex_effects` | water effects on vertices |
| `animated_textures` | animated texture ranges |
| `room_converter` | converting the rooms |
| `level_writer` | the uncompressed level, in the game's order of sections |
| `texture_repacker` | the PC-specific page packing |
| `tr4_container` | compression and sound samples |
