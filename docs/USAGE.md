# Using pyprj2tom and pytom2pc

> These programs exist to **study** how the Tomb Raider Level Editor builds a level. They reproduce
> the original tools byte for byte on the test levels, but they are not meant as a production tool
> for building levels. For real level building use a maintained editor such as Tomb Editor.

## 1. Installation

Python 3.10 or later and `numpy` are required. The code is pure Python and runs on Windows, Linux and
macOS.

**Install the commands** (recommended; gives `pyprj2tom` and `pytom2pc` on the `PATH`):

```sh
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install .                     # or: pip install -e .  to edit the code in place
```

**Or run from the source checkout** without installing:

```sh
pip install -r requirements.txt
python src/run_pyprj2tom.py --help
python src/run_pytom2pc.py --help
```

`python -m pyprj2tom` and `python -m pytom2pc` work as well when `src` is on `PYTHONPATH`.

## 2. You need the original data files

This repository contains **no level data**: no projects, textures, WADs, sounds or levels. Those
files belong to the Tomb Raider Level Editor and to the authors of the levels. To try the programs you
need a TRLE installation (it ships with the sample projects `tut1`, `catacomb`, `city`, `cleopal`,
`coastal`, `karnak`, `settomb` and `title`) or other `.PRJ` / `.TOM` files.

The one piece of editor data that *is* included is the **object list** (the slot names of
`objects.h`), as [`src/pyprj2tom/data/objects.json`](../src/pyprj2tom/data/objects.json). It is a
list of names; see [formats/OBJECT_LIST.md](formats/OBJECT_LIST.md).

## 3. `pyprj2tom`: project (`.PRJ`) to level file (`.TOM`)

```
pyprj2tom PRJ [--texture TGA] [--wad WAD] [--swd SWD] [--objects FILE] [--root DIR]
              [--out DIR] [--name NAME] [-q]
```

| option | meaning | default |
|---|---|---|
| `PRJ` | the project file | required |
| `--texture TGA` | the project's 24-bit texture | the path stored in the project, inside `--root` |
| `--wad WAD` | the object file | the path stored in the project (with `.wad`), inside `--root` |
| `--swd SWD` | the sprite file | beside the `--wad` if that was given, else from the stored path; optional |
| `--objects FILE` | the object list, as JSON or as an editor `objects.h` | the bundled `objects.json` |
| `--root DIR` | the TRLE folder used for every file not given explicitly | three folders above the project (`TRLE/maps/<level>/<level>.prj` → `TRLE`) |
| `--out DIR` | where `<name>.TOM`, `.TXT` and `.CD` are written | the current folder |
| `--name NAME` | base name of the output files | the name of the object file stored in the project, as the editor does |
| `-q` | print only errors | |

### How the input files are found

A project stores its texture and object file as absolute Windows paths of the computer it was made
on, for example `C:\PROGRA~1\COREDE~1\TRLE\MAPS\ANGKOR\angtext.TGA`. For each file `pyprj2tom` uses,
in this order:

1. the file given on the command line;
2. for the object list: the bundled `objects.json` (an `objects.h` in the TRLE folder is **not** read
   unless given with `--objects`);
3. for the texture and the object files: the stored path translated into `--root`: everything from
   the folder `MAPS\` or `GRAPHICS\` onward is attached to the root, so the example becomes
   `<root>/MAPS/ANGKOR/angtext.TGA`.

If a file cannot be found the program stops and names the stored path and the option to use.

**Letter case is ignored on every operating system.** The editor ran on Windows, whose file system
does not distinguish `MAPS` from `maps`; its files mix both freely. On Linux and macOS every path is
therefore looked up component by component without regard to case (see
[`trle_common/paths.py`](../src/trle_common/paths.py)).

### Examples

Everything given explicitly (works with any folder layout):

```sh
pyprj2tom levels/karnak/karnak.prj \
    --texture levels/karnak/karntext.tga \
    --wad wads/karnak.wad --swd wads/karnak.swd \
    --out build
```

Inside a TRLE installation (the classic layout; nothing else needed):

```sh
pyprj2tom "C:/Program Files (x86)/Core Design/trle/maps/karnak/karnak.prj" --out build
```

The same installation used from Linux through Wine:

```sh
pyprj2tom ~/.wine/drive_c/Program\ Files\ \(x86\)/Core\ Design/trle/maps/Title/title.prj --out build
```

A project from another layout, with the object file taken from a TRLE folder elsewhere:

```sh
pyprj2tom my/projects/test.prj --root /path/to/trle --texture my/projects/test.tga --out build
```

An export takes about 25 to 60 seconds per level, depending on the computer.

## 4. `pytom2pc`: level file (`.TOM`) to game level (`.TR4`)

```
pytom2pc TOM [--wad FILE] [--swd FILE] [--sfx FILE] [--sam FILE] [--sky FILE]
             [--default-sky FILE] [--font FILE] [--cin FILE] [--samples DIR]
             [--dest DIR | -o FILE] [-q]
```

| option | meaning | required? | default |
|---|---|---|---|
| `TOM` | the level file from the export | yes | |
| `--wad` | objects | yes | `<level>.wad` beside the `.tom` |
| `--swd` | sprites | yes | `<level>.swd` beside the `.tom` |
| `--sfx` | sound effect table | yes | `<level>.sfx` beside the `.tom` |
| `--sam` | list of sample files | no (no sounds without it) | `<level>.sam` beside the `.tom` |
| `--sky` | the level's sky (256 x 256 RGB) | no | `<level>.raw` beside the `.tom` |
| `--default-sky` | sky used when the level has none | no | `pcsky.raw` beside the `.tom` |
| `--font` | font pages | no | `font.pc` beside the `.tom` |
| `--cin` | cut-scene data | no | `<level>.cin` beside the `.tom` |
| `--samples DIR` | folder of the `.wav` files named in the `.sam` | | `./sound/samples` |
| `--dest DIR` | output folder; the file is `<level>.tr4` | | `./data` |
| `-o FILE` | output file; replaces `--dest` | | |
| `-q` | print only errors | | |

`<level>` is the name of the `.tom` without extension. A missing `.sam` sample is reported and the
level is still written, the way `tom2pc.exe` does it.

### Examples

Classic: run inside a TRLE folder, everything beside the `.tom`:

```sh
cd /path/to/trle
pytom2pc graphics/wads/karnak.tom          # writes data/karnak.tr4, samples from sound/samples
```

Everything explicit:

```sh
pytom2pc build/karnak.TOM --wad wads/karnak.wad --swd wads/karnak.swd \
    --sfx wads/karnak.sfx --sam wads/karnak.sam --samples sounds/ \
    --font trle/graphics/wads/font.pc --default-sky trle/graphics/wads/pcsky.raw \
    -o build/karnak.tr4
```

## 5. A complete build

```sh
pyprj2tom trle/maps/karnak/karnak.prj --out build
pytom2pc  build/karnak.TOM --wad trle/graphics/wads/karnak.wad --swd trle/graphics/wads/karnak.swd \
          --sfx trle/graphics/wads/karnak.sfx --sam trle/graphics/wads/karnak.sam \
          --font trle/graphics/wads/font.pc --default-sky trle/graphics/wads/pcsky.raw \
          --samples trle/sound/samples -o build/karnak.tr4
```

## 6. Use from Python

```python
from pyprj2tom.exporter import export_project
from pyprj2tom.input_files import ProjectInputs
from pytom2pc.converter import LevelConverter

inputs = ProjectInputs(texture='karntext.tga', wad='karnak.wad')      # root=... for the fallback
tom = export_project('karnak.prj', inputs, 'build')

converter = LevelConverter('build', 'sound/samples')
tr4 = converter.convert(tom, overrides={'wad': 'karnak.wad', 'swd': 'karnak.swd',
                                        'sfx': 'karnak.sfx', 'sam': 'karnak.sam'})
```

The modules are documented in [PYPRJ2TOM.md](PYPRJ2TOM.md#12-the-source-code) and
[PYTOM2PC.md](PYTOM2PC.md#9-the-source-code).

## 7. Troubleshooting

| message | cause and remedy |
|---|---|
| `The project refers to the texture "C:\...", which is not at ...` | The stored path does not exist inside `--root`. Give the file with `--texture` (or `--wad`), or point `--root` at the TRLE folder. |
| `... has object version 130, the object list has 129` | The `.WAD` was made for another object list (for example by an extended editor). Give a matching list with `--objects`; [tools/objects_h_to_json.py](../tools/objects_h_to_json.py) converts an `objects.h`. |
| `project version 0x30 is not supported` | Only version `0x31` projects are read, the version this editor writes. |
| `ERROR: Could not find ....tom` / `Cant Seem to Load ...` | A required `pytom2pc` input is missing. Give it with the matching option. |
| `*** File Not Found foot01.wav` | A sample named in the `.sam` is not in `--samples`. The level is written anyway, without that sample. |
| `.TR4` differs from `tom2pc.exe` only in its compressed bytes | Python is linked against zlib-ng (Python 3.14+ on Windows). See [PYTOM2PC.md](PYTOM2PC.md#6-the-tr4-container). |
| Export stops with a `KeyError` in `floor_data` | A trigger targets an object that is no longer in the project (for example because its slot is missing from the WAD or the object list). Use the object list the project was made with. |
