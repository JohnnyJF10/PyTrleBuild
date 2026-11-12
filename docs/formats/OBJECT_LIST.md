# The object list (`objects.h` and `objects.json`)

Every object in a Tomb Raider 4 level belongs to a **slot**: a fixed number with a fixed meaning for
the game engine. Slot 0 is Lara, slot 1 her pistol animations, slot 41 `BADDY_1`, and so on. The game
executable has code for each slot; a `.WAD` file fills slots with meshes and animations.

The level editor gets the names of the slots from `objects.h`, a C header that Core Design also used
to compile the game. The export needs the list for two reasons:

1. **Matching slots by name.** A project does not store slot numbers for its objects alone; its slot
   table also stores each slot's *name* (see [PRJ format, section 6](PRJ_FORMAT.md#6-wad-reference-and-slots)).
   When a project is loaded, each stored name is looked up in the list (the longest list name the
   stored text starts with wins), and the placed objects are renumbered. An object whose name is not
   found is **removed from its room**. With a wrong list, objects disappear, and a trigger that still
   refers to one of them makes the export fail.
2. **Classifying slots.** Slots before the end of the moveable section are moveables; the rest are
   static meshes. Static mesh numbers in the `.WAD` are counted from the first static slot.

It also provides the names in the `.TXT` report.

## 1. `objects.h` as the editor reads it

```c
/* Version number MUST be increased whenever the object lists are changed */
#define OBJECT_VERSION 129

enum object_types {
	LARA, PISTOLS_ANIM, UZI_ANIM, SHOTGUN_ANIM, CROSSBOW_ANIM, GRENADE_GUN_ANIM, SIXSHOOTER_ANIM, FLARE_ANIM,
	...
	DEFAULT_SPRITES,
	MISC_SPRITES,

	NUMBER_OBJECTS
};

enum static_types {
	PLANT0, PLANT1, ..., SHATTER9,
	NUMBER_STATIC_OBJECTS
};
```

The editor does **not** parse C. Its reader is a line scanner:

1. Find the first line containing `OBJECT_VERSION` and take the first number on it (129 = `0x81`).
2. Find the line containing `object_types`. Split every following line at commas, spaces, tabs and line
   ends, collecting the words, until a line containing `};`.
3. Drop the last word: it is the count `NUMBER_OBJECTS`, not a slot.
4. Do the same after the line containing `static_types`. Here the count `NUMBER_STATIC_OBJECTS` is
   *not* dropped; it becomes a slot name nobody uses.
5. At most 1024 names are kept.

This is why the file needs no comments to be removed, but also why a comment *inside* a section would
become a list of slot names. [`read_objects_header`](../../src/pyprj2tom/object_loading.py)
implements exactly this procedure.

The **object version** must equal the version stored in the `.WAD` (first 32-bit value) and becomes the
low 16 bits of the `.TOM` version. Editors with extended object lists use other version numbers.

The original list has 465 moveable slots and 60 static slots.

## 2. `objects.json`

The same information as plain data. `pyprj2tom` always uses this file unless another list is given
with `--objects`; it never needs the editor's `objects.h`:

```json
{
 "format": "pyprj2tom-object-list",
 "source": "OBJECTS.H of the Tomb Raider Level Editor (Core Design, 2000), object version 129",
 "object_version": 129,
 "moveables": ["LARA", "PISTOLS_ANIM", "...", "MISC_SPRITES"],
 "statics": ["PLANT0", "...", "SHATTER9"]
}
```

| field | meaning |
|---|---|
| `format` | always `pyprj2tom-object-list`, so other JSON files are not taken for an object list |
| `source` | free text: where the list came from |
| `object_version` | the `OBJECT_VERSION` number |
| `moveables` | the moveable slot names in slot order, without `NUMBER_OBJECTS` |
| `statics` | the static slot names in slot order, without `NUMBER_STATIC_OBJECTS` |

The unused count name of the static section is left out of the JSON. It never matches a project slot
and is never written, so the output is the same either way (this was checked byte for byte on all
test levels).

The bundled file [`src/pyprj2tom/data/objects.json`](../../src/pyprj2tom/data/objects.json) was made
from the editor's `OBJECTS.H` with

```sh
python tools/objects_h_to_json.py path/to/OBJECTS.H src/pyprj2tom/data/objects.json
```

which uses the editor-faithful reader above. Use the same tool to convert the object list of another
editor variant, then pass it with `--objects`. `--objects` also accepts an `objects.h` directly.

## 3. Slot groups at a glance

| slots | group |
|---|---|
| 0-30 | Lara, her weapon animations, skins, speech heads, meshes for wet/dirty Lara, crowbar, torch, hair |
| 31-34 | vehicles (motorbike, jeep, enemy jeep) |
| 35-107 | enemies, animals and characters, most with a `_MIP` (lower-detail) slot after each |
| 108-464 | game pieces, traps, platforms, pushables, puzzle and key items, switches, doors, pick-ups, animating objects, effects, AI markers (`AI_GUARD` is 398), sprite slots (`DEFAULT_SPRITES` 463, `MISC_SPRITES` 464) |
| statics 0-59 | `PLANT0-9`, `FURNITURE0-9`, `ROCK0-9`, `ARCHITECTURE0-9`, `DEBRIS0-9`, `SHATTER0-9` (shatter statics can be destroyed) |

The exact numbers are the positions in `objects.json`.
