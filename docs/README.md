# Documentation

These documents describe how the Tomb Raider Level Editor (TRLE, Core Design, 2000) turns a project
into a playable Tomb Raider 4 level: the file formats involved, and every step of the two programs
that do the work, the editor's export and `tom2pc.exe`. The descriptions were written alongside the
Python reimplementations `pyprj2tom` and `pytom2pc` and checked against the original programs byte by
byte.

## Reading order

1. **[CONCEPTS.md](CONCEPTS.md)**: the tool chain, the files, and the vocabulary (sectors, clicks,
   rooms, portals, flip rooms, floor data, triggers, slots, boxes). Start here.
2. **[USAGE.md](USAGE.md)**: installing and running `pyprj2tom` and `pytom2pc`, all options, how input
   files are found, troubleshooting.
3. **The formats**, as reference while reading the programs:
   * [formats/PRJ_FORMAT.md](formats/PRJ_FORMAT.md): the editor's project file
   * [formats/TOM_FORMAT.md](formats/TOM_FORMAT.md): the exported level file
   * [formats/OBJECT_LIST.md](formats/OBJECT_LIST.md): `objects.h` / `objects.json`, the names of the object slots
4. **The programs**:
   * [PYPRJ2TOM.md](PYPRJ2TOM.md): the export, `.PRJ` → `.TOM`: texture quantisation, room
     compilation, vertex lighting, floor data, triggers, pathfinding
   * [PYTOM2PC.md](PYTOM2PC.md): the conversion, `.TOM` → `.TR4`: texture records, water effects,
     texture page packing, the `.TR4` container
5. **[VERIFICATION.md](VERIFICATION.md)**: how the reimplementations were compared with the
   originals, the results, and how to repeat the comparison.

## Conventions used throughout

* Values are little-endian. `i8`/`u8`, `i16`/`u16`, `i32`/`u32` are integers of that many bits,
  `f32` is an IEEE single-precision float.
* Hexadecimal numbers are written `0x...`.
* World Y grows downwards; one sector is 1024 units, one click 256 units.
* "The original" means the program shipped with the editor (`winroomedit.exe` or `tom2pc.exe`);
  "the Python version" means `pyprj2tom` or `pytom2pc`.
* Behaviour of the original that looks unintended is called a *quirk* and is listed in a table at the
  end of each program document. The Python versions reproduce quirks whenever they affect the output.

## Scope

This is study material. The documents describe the original tools as they are, including their
limits (20 texture pages per group, 16 animated ranges, project version `0x31` only in the Python
version). Extended editors and engines (Tomb Editor, TRNG, TEN, Tomb4Plus and others) change several
of these formats and limits and are not covered.
