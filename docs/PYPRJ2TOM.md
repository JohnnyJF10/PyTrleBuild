# pyprj2tom: the editor's export, from project (`.PRJ`) to level file (`.TOM`)

The Tomb Raider Level Editor (`winroomedit.exe`) has a built-in exporter, the menu command "Output
Wad". It turns an editable project ([PRJ format](formats/PRJ_FORMAT.md)) into a `.TOM` file
([TOM format](formats/TOM_FORMAT.md)), which `tom2pc.exe` then turns into a game level
([PYTOM2PC.md](PYTOM2PC.md)). There is no separate program for the export, and the editor cannot run
it from the command line (its command-line option is dead code: the main thread is always started
without arguments).

`pyprj2tom` is a readable Python reimplementation of this exporter. This document explains what the
exporter does, step by step, and points to the module that does each step. If the vocabulary is new,
read [CONCEPTS.md](CONCEPTS.md) first. How to run the program is in [USAGE.md](USAGE.md#3-pyprj2tom-project-prj-to-level-file-tom).

> **Study code.** The goal is to understand and document the original behaviour, down to the byte.
> The program is not meant for building real levels.

Contents: [1 Inputs and outputs](#1-inputs-and-outputs) · [2 Overview](#2-overview) ·
[3 Loading](#3-loading-a-project) · [4 Texture import](#4-texture-import) · [5 Objects](#5-the-objects) ·
[6 Compiling a room](#6-compiling-a-room) · [7 Lighting](#7-vertex-lighting) · [8 Export](#8-the-export) ·
[9 Pathfinding](#9-pathfinding-boxes-overlaps-zones) · [10 Quirks](#10-export-behaviour-worth-knowing) ·
[11 Verification](#11-verification) · [12 Source code](#12-the-source-code)

## 1. Inputs and outputs

| input | role | command-line option |
|---|---|---|
| `level.PRJ` | the project | positional argument |
| the texture named in the project | 24-bit TGA, magenta (255, 0, 255) for transparency | `--texture` |
| the `.WAD` named in the project | which object slots exist, their class, meshes and collision | `--wad` |
| the `.SWD` beside it | which slots are sprites | `--swd` (optional) |
| the object list | names of all object slots ([object list](formats/OBJECT_LIST.md)) | `--objects` (bundled by default) |

Files not given on the command line are found through the paths stored in the project, translated
into the TRLE folder `--root` ([USAGE.md](USAGE.md#how-the-input-files-are-found)). The editor itself
only knew that second way, which is why projects exported only on a computer with the same folder
layout.

| output | content |
|---|---|
| `level.TOM` | the level: textures, rooms, collision, triggers, pathfinding, items |
| `level.TXT` | how often each object slot is used in the map, and which slots of the WAD are unused |
| `level.CD` | one line for each sector that triggers a CD (music) track: room, X, Z, track |

The editor also writes the diagnostics files `tom.log` and `error.log`; the Python version prints its
messages instead.

## 2. Overview

The editor does the work in four phases, and the Python version follows the same order
([`exporter.py`](../src/pyprj2tom/exporter.py)):

1. **Start-up**: read the slot names from the object list (the bundled `objects.json`), prepare empty rooms and a 256-entry tile
   table.
2. **Load the project** (section 3): rooms, objects, the texture (section 4) and the WAD (section 5),
   then check and repair the project.
3. **Compile all rooms** a first time (section 6), in both flip states, and match shades between
   neighbouring rooms. The editor does this right after loading, so that the 3D view can show the
   rooms.
4. **Export** (section 8), which begins by compiling everything once more and then writes the file.

The reimplementation repeats the first compilation as well, although nothing is displayed, because it
follows the editor's sequence exactly rather than guessing which steps leave no trace in the output.

## 3. Loading a project

Rooms are read as described in the [PRJ format](formats/PRJ_FORMAT.md)
([`project_reader.py`](../src/pyprj2tom/project_reader.py)). Then:

1. The texture is imported (section 4). The first 256 tile entries are corrected so that they form the
   regular 64 x 64 grid: the editor assumes that tile *n* of the first 256 is cell *n* of the image.
2. The WAD is read, and each slot stored in the project is matched by **name** to the slots of the
   object list: the longest list name that the stored name starts with wins. Placed items are
   renumbered to the new slots. An item whose slot is not in the list, or not in the WAD, is removed
   from its room. This lets a designer swap the WAD of a project for a newer one: as long as the names
   match, items keep their objects.
3. Animated ranges and the two per-cell byte tables (footstep sounds and bump mapping) are read.
4. **The project is checked and repaired** ([`project_repair.py`](../src/pyprj2tom/project_repair.py)),
   in three phases that the editor writes to `error.log`:

| phase | check | repair |
|---|---|---|
| I, doors | door number valid, it is a door, the partner is a door that links back, its room matches, position is inside the room | the door is removed from the room |
| II, sectors | sector type is valid for its position; a door sector has a door; the flags are consistent; the floor is below the ceiling | the type is changed to wall or floor; for example `0` with a door partner takes its height from the partner |
| III, objects | each object occurs once, a trigger's data is unique, the object is in the room it claims | the entry is removed |

Finally the free lists for objects, lights and triggers are rebuilt. Any repair shows a warning. No
sample project needs one, so the repair code has only run without changing anything.

Only project version `0x31` is read, the version this editor writes and every known project uses. The
editor also reads older versions (see the [appendix of the PRJ format](formats/PRJ_FORMAT.md#8-appendix-older-versions));
the Python version stops with a clear message instead.

## 4. Texture import

The texture is a single image of 64 x 64 pixel *cells*, four per row
([`texture_import.py`](../src/pyprj2tom/texture_import.py)).

* A TGA must be uncompressed 24-bit. It may be stored top-down or bottom-up. Its height is rounded up to a multiple of 64
  (rows filled with zero). The width should be 256; other widths are rearranged into 64-pixel cells four across.
  The editor also accepts an 8-bit PCX; the Python version does not, as no test project uses one.
* **Palette.** Although the PC game uses true-colour textures, the editor still produces the 8-bit data
  of the older renderers. A NeuQuant neural-network quantiser ([`neuquant.py`](../src/pyprj2tom/neuquant.py))
  reduces all non-magenta pixels to 255 colours. Index 0 stays black for transparency. Fixed
  user-interface colours are then forced into the palette, swapping them into the position of their
  nearest match.
* **Colour cache.** A table with one entry per 5:5:5 colour. Each entry is filled with the palette
  index nearest to the *first pixel* of the image that falls into that cell. Remaining entries are
  filled in afterwards using the palette and cell centres.
* **Light table.** 32 tables, each giving for every palette colour its darkened version. Each is looked
  up through the colour cache, so which pixel came first in a cell can change the result. For bright
  colours the lookup index is beyond the end of the 32 KB cache (up to 61 instead of 31 per channel),
  so the exporter reads neighbouring editor memory: mostly zeros, but also palettes and settings. The
  Python version models that memory (the palettes and image size the editor keeps there), which
  reproduces the table of a freshly started editor.

The transparency flag of each tile is set if any of its pixels is index 0, and the footstep table is
reset to its default of 6.

None of the 8-bit data is used by `tomb4.exe`; it is still written to the `.TOM`, and `tom2pc` skips
it.

## 5. The objects

The object list provides the slot names, first the moveables, then the statics
([object list](formats/OBJECT_LIST.md)). The WAD provides which slots exist, and for each slot its
class, mesh or sprite number, and whether its meshes store normals or shades
([`object_loading.py`](../src/pyprj2tom/object_loading.py)). The `.SWD` adds the sprite slots. The
collision shape of each item (a rectangle and two 5 x 5 height tables) comes from the project's slot
table.

| class value | meaning |
|---|---|
| `0x10` | moveable |
| `0x110` | static mesh |
| `8` | sprite |
| `0x108` | sprite used as a static (exported as a room sprite) |

For each moveable, a radius is computed by walking through its animation frame's mesh tree. It is at
least 1024.

## 6. Compiling a room

"Compiling" is the editor's word for turning a room's height grid into renderable geometry
([`room_compiler.py`](../src/pyprj2tom/room_compiler.py), [`polygon_builder.py`](../src/pyprj2tom/polygon_builder.py)).
Rooms are built in this order, for one flip state at a time:

1. **Grid.** Every sector gets 16 points: 4 for the floor, 4 for the ceiling, 4 for the second floor
   layer and 4 for the second ceiling layer. Heights come from `height + corner offset`, multiplied by
   -256 (clicks to world units, Y downwards).
2. **Polygons** per sector (up to 14), kept in fixed positions:
   * floor (one quad if planar, otherwise two triangles along the sector's diagonal, chosen by height
     and the split flag),
   * ceiling (likewise),
   * the two walls towards the neighbouring sectors in Z-1 and X-1, in up to three layers: the main
     wall, and pieces above or below it where the neighbour's floor or ceiling differs.

   Only two of the four walls are built per sector, because the other two belong to the neighbours.
   Each face's normal is computed, and a face with duplicate vertices is discarded.
3. **Wall-attached objects** have their position derived from the polygon they sit on, the side, and
   a distance from it.
4. **Vertex merge** runs three times. It replaces points with the same position in the 3 x 3
   neighbourhood by one point, and reuses numbers as the sectors are scanned.
5. **Lighting** (section 7).
6. **The mesh block** is written with renumbered vertices. Only used points appear.
7. **Portals.** Each door becomes a record with the adjoining room, normal and four corners. The
   door's height spans the sectors along it.

Afterwards, **shades are matched across doors**: where two neighbouring rooms have a vertex in the same
world position and the same state of the water flag, both get the average of the two colours. This
hides the seams between rooms.

Flip pairs are handled by swapping the data of the two rooms, compiling, and swapping back. That is
also why the `.TOM` writer has to put each room into the right flip state before writing it.

## 7. Vertex lighting

Each vertex starts from the room's ambient colour. For each light the amount added depends on its type
([`lighting.py`](../src/pyprj2tom/lighting.py)):

| light | amount |
|---|---|
| sun | `intensity * max(n.l, 0)` |
| point, shadow | `intensity * t * (n.n)`, where `t` falls linearly from inner to outer range; `n.n` is the normal with itself, about 1 |
| spot | `intensity * attenuation * n.l * cone`; distance and cone fall-off are linear |
| effect | a fixed value on one chosen polygon |

Here `n` is the vertex normal and `l` the direction to the light. The result is
`(colour * amount) >> 13` per colour channel; shadow bulbs have negative intensity and darken. A vertex
used by several polygons receives the average of each of them. Rays are tested against walls with
three checks: one at the light itself, one along the sector rows, one along the columns. Colours are
finally clamped to 0-255 and stored as 15-bit colour.

The maths follows a 53-bit x87 FPU: values stay in double precision until they are written to a 32-bit
float. [`vertex_math.py`](../src/pyprj2tom/vertex_math.py) reproduces this, because a single
rounding difference changes a vertex colour by one step and the file no longer matches.

## 8. The export

The writer ([`tom_writer.py`](../src/pyprj2tom/tom_writer.py)) runs these steps:

1. Doors are checked for overlaps. An overlap stops the export with a message.
2. Everything is compiled again; the doors of the current room are refreshed first.
3. The boxes and overlaps are built (section 9).
4. Used textures are collected: every tile referenced by a polygon, then full ranges for animated
   textures that are used. Only these cells are written.
5. Room objects are counted and numbered: AI objects go into a separate table, items get their
   position numbers (which triggers refer to), cameras, sinks and sound sources get theirs.
6. Rooms are written with [the layout in the TOM format](formats/TOM_FORMAT.md#4-rooms). Each is written
   while in the proper flip state.
7. The remaining sections: floor data, cameras, sound sources, boxes, zones, animated ranges, items,
   light table, map lines, AI objects.

Sorting uses the original `qsort` of the Microsoft C runtime ([`msvc_sort.py`](../src/trle_common/msvc_sort.py)),
because equal items keep an order that depends on the algorithm, and that order is visible in the file.

### Floor data

For each sector the exporter writes a list of 16-bit function words to the floor data
([`floor_data.py`](../src/pyprj2tom/floor_data.py)), in this order:

1. floor slope or split,
2. ceiling slope or split,
3. portal link for door sectors,
4. portal to the neighbouring room where a wall sector connects (the "wall portal" search looks at the
   3 x 3 neighbourhood),
5. kill Lara, climbable walls, monkey-swing, and the two minecart flags,
6. trigger list.

The last word gets bit 15. The floor heights, room links above and below, material and box number are
stored in the sector record itself.

A slope with a height difference of up to 2 clicks in each direction is a simple slope (function 2 or
3). Anything else is a split into two triangles with a chosen diagonal (function 7 to 18).

**Trigger list.** The list starts with the type word in its high byte and timer and mask in the setup
word. Triggers on the same sector are then listed with their action words: object, camera, sink, flip
map, music and others. Object actions use the item numbers from step 5; a trigger whose target object
is no longer in any room cannot be written.

## 9. Pathfinding: boxes, overlaps, zones

Enemies navigate on a graph of rectangles, not on the sector grid
([`pathfinding.py`](../src/pyprj2tom/pathfinding.py)).

* **Boxes** are built in both flip states. Starting from a walkable sector, the box grows in the order
  Z-, X+, Z+, X- as long as all sectors at the new edge have the same height, have no steep slope, and
  do not cross a flagged blocker. Duplicates are merged.
* **Overlaps.** Two boxes overlap if they touch and the height difference is passable. The two
  neighbouring boxes must be compatible with both flip states. Special marks are kept for "jump" links
  across one or two sectors and for monkey-swing areas.
* **Zones** are computed by flood fill over the overlap lists. There are five movement classes, each
  for two states (normal and flipped):

| class | allowed step |
|---|---|
| 0, 1, 2 | up to 1 click (`0x100`); class 2 also ignores water restrictions |
| 3 | up to 4 clicks (`0x400`); may jump |
| 4 | any (flyers) |

## 10. Export behaviour worth knowing

These details look like mistakes and partly are. The Python version reproduces them, because they are
part of the files.

| behaviour | effect |
|---|---|
| Uninitialised light fields | The bytes that change between exports ([details](formats/TOM_FORMAT.md#the-bytes-that-change-from-one-export-to-the-next)). This is the one place where the Python version deliberately differs: it writes zeros. |
| The `sector_type` word of the editor sector table | Accumulates by OR over the sectors of a room, because the variable is never reset. |
| Point and shadow lights | Use `n.n` instead of `n.l`, so that a point light's brightness does not depend on the surface direction. |
| The last row of texture cells | If it is not full, its free positions show the pixels 64 rows below the previous row's cells, or zeros past the end of the image ([details](formats/TOM_FORMAT.md#3-texture-table-and-pages)). |
| The light table | Its bright entries come from memory beyond the end of the colour cache, so they depend on the state of the editor. TOM files from a real editor session differ from a freshly started editor by 1 or 2 bytes in this table; this was seen in all shipped levels. |

## 11. Verification

The original export code was run on the 11 sample projects (the 8 projects of the TRLE installation
plus `angkor`, `guard` and `library`), with a copy of the editor patched to run the export without a
window. Compared with those reference files:

| level | `.TOM` | `.TXT`, `.CD` |
|---|---|---|
| tut1, catacomb, city, cleopal, coastal, karnak, settomb, title, angkor, guard, library | identical in every byte the editor assigns | identical |

The bytes the editor does not assign are the uninitialised light fields; `pyprj2tom` writes zeros
there, so 1,095 to 3,326 bytes per level differ.

Compared with the `.TOM` files **shipped with the editor**, the output of the original code agrees
except for:

* 1-2 bytes of the light table in every level (section 10), and
* `karnak`: 3,124 texture bytes in three tiles of one page; `settomb`: two lights of one room. The
  project files were evidently saved after those TOM files were exported.

`pyprj2tom` against the shipped TOMs gives the same picture: same size, and only those bytes plus the
light fields differ (1,005 bytes for `tut1` to 6,416 for `karnak`). The renaming to `pyprj2tom`, the
command-line options and the portable path handling were checked by producing all outputs before and
after the change: they are byte-identical. See [VERIFICATION.md](VERIFICATION.md) for the method.

Not verified, because no test data exists: PCX textures, project versions below `0x31`, and actual
repairs by the project check.

## 12. The source code

The package is in [`src/pyprj2tom/`](../src/pyprj2tom/). Each module does one job, and its comments
explain why the output has to look the way it does. A good reading order is `exporter` → `project_model`
→ `project_reader` → `room_compiler` → `polygon_builder` → `lighting` → `tom_writer` → `floor_data` →
`pathfinding`.

| module | job |
|---|---|
| `cli` | the command line |
| `exporter` | the order of the steps |
| `input_files` | where the texture, WAD, SWD and object list come from: explicit paths, then the TRLE folder |
| `project_model` | rooms, sectors, faces, objects, lights, triggers as Python data classes |
| `binary_reader`, `project_reader` | reading the `.PRJ` file |
| `project_repair` | the three repair phases for damaged projects |
| `object_loading` | the object list (`objects.json` or `objects.h`), the `.WAD` and the `.SWD` |
| `texture_import`, `neuquant` | the texture, its palette, colour cache and light table |
| `vertex_math` | floats and vectors with the original's numeric behaviour |
| `room_compiler` | points, polygons, mesh, portals, shades across doors, flip rooms |
| `polygon_builder` | the polygons of a sector |
| `lighting` | vertex colours and wall ray tests |
| `pathfinding` | boxes, overlaps and zones |
| `floor_data` | the two sector tables and the floor data list |
| `tom_writer` | the `.TOM`, `.TXT` and `.CD` files |
| `data/objects.json` | the editor's object list |

Shared helpers are in [`src/trle_common/`](../src/trle_common/): `c_arithmetic` (32-bit C integer
rules), `msvc_sort` (the original `qsort`), `classic_zlib` and `paths` (case-insensitive lookup of
Windows paths).
