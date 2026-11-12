# PyTrleBuild

Readable Python reimplementations of the two programs that build a level with the **Tomb Raider Level
Editor** (TRLE, Core Design, 2000), with documentation of every file format and processing step:

| program | reimplements | converts |
|---|---|---|
| **`pyprj2tom`** | the export of the room editor `winroomedit.exe` (menu "Output Wad") | project `.PRJ` → level file `.TOM` (+ `.TXT`, `.CD`) |
| **`pytom2pc`** | the converter `tom2pc.exe` | `.TOM` + `.WAD` `.SWD` `.SFX` `.SAM` → playable Tomb Raider 4 level `.TR4` |

Both produce the same bytes as the original tools on the test levels (see
[docs/VERIFICATION.md](docs/VERIFICATION.md)).

> ### For study only
> This repository exists to **understand and document** how the original tools work. The code is
> written to be read: each module does one job, and its comments explain what it does and why the
> output has to look the way it does, including the quirks of the original. It is **not** meant for
> building real levels; for that, use a maintained editor such as
> [Tomb Editor](https://github.com/MontyTRC89/Tomb-Editor).

## Documentation

Start with [docs/README.md](docs/README.md). In short:

* [Concepts](docs/CONCEPTS.md): the tool chain and the vocabulary of the Tomb Raider engine
* Formats: [PRJ](docs/formats/PRJ_FORMAT.md), [TOM](docs/formats/TOM_FORMAT.md), [object list](docs/formats/OBJECT_LIST.md)
* Programs: [pyprj2tom](docs/PYPRJ2TOM.md) (export), [pytom2pc](docs/PYTOM2PC.md) (conversion)
* [Verification](docs/VERIFICATION.md): how the results were compared with the original tools

## Layout

```
src/
  pyprj2tom/       project -> .TOM            (docs/PYPRJ2TOM.md, section 12, lists the modules)
    data/objects.json   the editor's object slot names
  pytom2pc/        .TOM -> .TR4               (docs/PYTOM2PC.md, section 9, lists the modules)
  trle_common/     shared: C integer arithmetic, the MSVC qsort, classic zlib, portable paths
  run_pyprj2tom.py, run_pytom2pc.py   run from a checkout without installing
tools/
  objects_h_to_json.py   convert an editor objects.h into objects.json
docs/              formats, programs, concepts, usage, verification
```

## Differences from the original tools

* **Input files.** The originals need the editor's folder layout and the absolute Windows paths stored
  in the project. The Python versions accept every file on the command line and use the folder layout
  only as a fallback. File names are matched without regard to letter case on every system.
* **Uninitialised light fields.** The editor writes leftover memory into the light fields a light
  type does not use; `pyprj2tom` writes zeros there
  (see [the TOM format](docs/formats/TOM_FORMAT.md#the-bytes-that-change-from-one-export-to-the-next)).
* **Project versions.** `pyprj2tom` reads only project version `0x31`, the version the editor writes
  and every known project uses; older files are refused with a clear message.
* **Paths with dots.** `tom2pc.exe` cuts paths at the first dot and fails on folder names containing
  one; `pytom2pc` does not.
* **Compression.** For a `.TR4` byte-identical to the original, Python must use the classic zlib (the
  normal case except on Python 3.14+ for Windows; see [docs/PYTOM2PC.md](docs/PYTOM2PC.md#6-the-tr4-container)).
  Otherwise the level is equally valid; only its compressed bytes differ.

Everything else, including the quirks, is reproduced.

## Legal

This is an independent study project. It is not affiliated with, endorsed by or connected to Core
Design, Eidos, Square Enix, Crystal Dynamics, Embracer Group or any other holder of rights in Tomb
Raider. "Tomb Raider" is a trademark of its respective owner. No code, executables or game assets of
the original tools are included. The object slot names in `objects.json` are a list of identifiers that is widely
published in level-editing resources.

The code and documentation are released under the [MIT License](LICENSE).

## Acknowledgements

Several field meanings of the project format were cross-checked against `PrjLoader.cs` of
[Tomb Editor](https://github.com/MontyTRC89/Tomb-Editor). The Tomb Raider level-building community
has documented these engines for more than two decades.