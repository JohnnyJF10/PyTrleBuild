# Concepts: how a Tomb Raider Level Editor level is built

This page explains the ideas the other documents rely on: the tool chain of the Tomb Raider Level
Editor (TRLE), the files that travel through it, and the vocabulary of the Tomb Raider engine. Read it
first if the words *sector*, *click*, *portal*, *flip room* or *box* are new to you.

## 1. The tool chain

Core Design released the Tomb Raider Level Editor in 2000, together with *Tomb Raider Chronicles*. It
is the in-house tool chain that had been used to build *Tomb Raider: The Last Revelation* (TR4), and
levels built with it run in the TR4 engine (`tomb4.exe`). Building a playable level takes three steps:

```
  winroomedit.exe                    "Output Wad" command              tom2pc.exe
 ┌──────────────┐   saves    ┌───────────────┐   writes    ┌──────────────────────┐  writes  ┌────────────┐
 │ room editor  │ ─────────► │ level.PRJ     │ ──────────► │ level.TOM  (+ .TXT,  │ ───────► │ level.TR4  │
 │ (you design) │            │ (editable)    │             │ .CD) next to the     │          │ (playable) │
 └──────────────┘            └───────────────┘             │ .WAD .SWD .SFX .SAM  │          └────────────┘
                                   ▲                       └──────────────────────┘                ▲
                                   │ refers to                                                     │ loaded by
                           texture .TGA, object .WAD/.SWD, objects.h                          tomb4.exe
```

| step | original program | Python version in this repository | document |
|---|---|---|---|
| design rooms, place objects, save | `winroomedit.exe` | – (not reimplemented) | [PRJ format](formats/PRJ_FORMAT.md) |
| compile the project into a level file | `winroomedit.exe`, menu "Output Wad" | **`pyprj2tom`** | [PYPRJ2TOM.md](PYPRJ2TOM.md), [TOM format](formats/TOM_FORMAT.md) |
| combine level, objects, sounds into a game level | `tom2pc.exe` | **`pytom2pc`** | [PYTOM2PC.md](PYTOM2PC.md) |

The interesting point is the split of work. The `.PRJ` is an *editing* format: it stores what the
designer did (heights, wall textures, placed objects) and nothing derived. The export to `.TOM` does
almost all the computing: it builds polygons from the height grid, lights every vertex, finds portals,
computes enemy pathfinding and writes the collision and trigger data. `tom2pc` is mostly a packer: it
merges the `.TOM` with the objects, re-arranges all textures into the page layout the PC engine
expects, and compresses the result.

## 2. The files

| file | written by | content |
|---|---|---|
| `.PRJ` | room editor | the editable project: rooms, sectors, faces, objects, lights, triggers |
| `.TGA` | an image editor | the room texture: 24-bit image, 256 pixels wide, cut into 64 x 64 cells; pure magenta (255, 0, 255) is transparent |
| `.WAD` | the editor's WAD tools | all objects: meshes, skeletons, animations, object textures, static meshes |
| `.SWD` | the editor's WAD tools | sprites (flat images such as flames and pick-ups) and sprite sequences |
| `.SFX` | sound tools | the sound effect table, copied into the level unchanged |
| `.SAM` | sound tools | a text list of the `.WAV` sample files, one per line |
| `.WAS` | WAD tools | the list of objects of a WAD as text (not used by the build) |
| `objects.h` | Core Design | the names of all object slots, see [object list](formats/OBJECT_LIST.md) |
| `.TOM` | export | the compiled level without objects and sounds |
| `.TXT` | export | report: how often each object slot is used, which WAD objects are unused |
| `.CD` | export | the sectors that trigger CD (music) tracks |
| `.RAW` | an image editor | optional 256 x 256 RGB sky image for the level (`pcsky.raw` is the default) |
| `font.pc` | Core Design | the game font pages |
| `.TR4` | `tom2pc` | the playable level |

The editor expects a fixed folder layout, which is why its files contain absolute Windows paths:

```
TRLE\
  objects.h
  maps\<level>\<level>.prj, <texture>.tga         projects and their textures
  graphics\wads\<level>.wad .swd .sfx .sam .tom   objects, sounds, and the export output
  sound\samples\*.wav                             the sound samples
  data\<level>.tr4                                the finished levels
```

The Python versions can use this layout, but they do not need it: every file can be given on the
command line instead. See [USAGE.md](USAGE.md).

## 3. Units and coordinates

* **World units.** A level lives in a 3D integer coordinate system. X and Z are the horizontal axes;
  **Y grows downwards**, so a higher floor has a *smaller* Y.
* **Sector.** The ground plan of every room is a grid of square cells of 1024 x 1024 world units. A
  sector is the unit of everything in the editor: it has one floor and one ceiling height (each with
  four corner offsets for slopes), its wall textures, its flags, and possibly a trigger.
* **Click.** Heights are edited in steps of 256 world units, a quarter sector. The `.PRJ` stores heights
  in clicks; the `.TOM` in world units (`-256 * clicks`) or, in the game's sector table, as signed
  bytes in clicks.
* **Fixed point.** Texture coordinates and sizes use 8.8 fixed point; angles use 16-bit units where
  65536 is a full turn. The lighting maths uses 32-bit floats, computed the way the x87 FPU of the
  original computer did (see [PYPRJ2TOM.md](PYPRJ2TOM.md#7-vertex-lighting)).

## 4. Rooms, portals and flip rooms

* **Room.** A level is a set of rooms, each an axis-aligned box of sectors with its own position. The
  border of a room's grid is wall. Rooms do not have to be convex or connected in space; what connects
  them are portals.
* **Portal (door).** An opening through which one room can see into the next. In the editor a door is
  an object placed on a border or in a floor or ceiling. The export turns it into a *portal record*
  (adjoining room, normal, four corners) used for rendering, and into floor data entries that move
  Lara and other objects from one room to the next.
* **Flip room (alternate room).** A second version of a room that the game can swap in at run time, for
  example a flooded or collapsed version. Flip rooms come in pairs and are switched in *groups* by
  triggers. Both versions must be compatible for pathfinding, which is why boxes are built "in both
  flip states".
* **Room flags** describe the environment: water, quicksand, sky visible, outside, damage, mist,
  reflection, snow and rain (see [PRJ format](formats/PRJ_FORMAT.md#2-room-record)).

## 5. Geometry, textures and light

* **Mesh.** The export turns each room's height grid into vertices and polygons: per sector up to 14
  polygons (floor, ceiling, and wall pieces towards two neighbours, in up to three layers).
* **Texture tile.** Each polygon refers to a rectangle (or a triangle half) of the room texture. The
  64 x 64 pixel *cells* of the `.TGA` are the unit of storage; a tile can be smaller than a cell.
* **Animated textures.** Ranges of tiles that the game cycles through (water surfaces, lava).
* **Vertex lighting.** The engine does not compute lights per pixel. Every vertex stores a colour,
  computed at export time from the room's ambient colour and its lights (sun, point, spot, shadow,
  effect). Moving objects are lit at run time from the room's light list.
* **Texture pages.** The game wants all textures (rooms, objects, sprites) packed onto 256 x 256
  pixel pages. Packing them is the main job of `tom2pc`.

## 6. Collision and game logic: floor data and triggers

The game does not collide with polygons. It uses the sector grid: each sector of the game's sector
table has a floor and a ceiling height, the rooms above and below, and an index into the **floor
data**, a list of 16-bit words with everything else about the sector: slopes and diagonal splits,
portals, death, climbable walls, monkey-swing, and the **triggers**.

A **trigger** is a sector property: when Lara (or an object, for some types) is on that sector and
the condition holds (she stands there, pulls a switch, picks up a key ...), a list of actions runs:
activate objects, switch cameras, flip rooms, play music.

## 7. Objects

* **Slot.** Objects are identified by *slot numbers* whose names are listed in `objects.h`: `LARA`,
  `BADDY_1`, `PUSHABLE_OBJECT1`, ... The `.WAD` says which slots it actually contains. *Moveables*
  (animated objects, slots 0-464) and *static meshes* (scenery, the 60 statics after them) have
  separate numbering. *Sprites* come from the `.SWD`.
* **Item.** A placed moveable. Items are written into the level with their position, room, rotation
  and flags; their numbers are what triggers refer to.
* **AI objects.** Invisible markers placed for enemy behaviour (`AI_GUARD`, `AI_PATROL1`, ...). They are
  written into a separate table.
* **Cameras, sinks and sound sources** are placed like lights and written into their own tables.

## 8. Enemy pathfinding: boxes, overlaps and zones

Enemies do not walk on the sector grid directly. The export groups the walkable sectors of every room
into rectangular **boxes** of equal floor height. Two boxes **overlap** when an enemy can move from one
to the other. **Zones** number the connected groups of boxes for each kind of mover (small steps,
big steps or jumps, flying), so the game can quickly see whether a target is reachable at all. Details
are in [PYPRJ2TOM.md](PYPRJ2TOM.md#9-pathfinding-boxes-overlaps-zones).

## 9. Why "byte-identical" matters here

The Python code is written to study the original programs. The most reliable way to show that an
explanation is right is to write code from it and get **exactly the same bytes** as the original
tool. That is why the code imitates details that look like accidents: the order in which the
unstable `qsort` of the Microsoft C runtime leaves equal elements, the rounding of the x87 FPU, or
reading past the end of a table. Each of those details is explained where it occurs, and the
documents mark clearly which behaviour is a quirk of the original. See [VERIFICATION.md](VERIFICATION.md).
