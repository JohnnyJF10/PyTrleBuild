# Verification

The Python versions are only as good as the evidence that they behave like the originals. This page
describes what was compared, with what result, and how to repeat it.

## 1. Principle

An explanation of a file format or an algorithm can be wrong in ways that reading cannot reveal. Code
written from the explanation, whose output is compared **byte for byte** with the output of the
original program, can. Every statement in the program documents is backed by such a comparison,
except where a document says otherwise (for example for code paths no test file reaches).

Byte identity also forces the reimplementation to account for everything: a single rounding
difference in the lighting, a different order of equal elements after sorting, or a missing padding
byte shows up immediately.

## 2. Test data

| set | content |
|---|---|
| TRLE installation | the 8 sample projects and their shipped `.TOM`, `.WAD`, `.SWD`, `.SFX`, `.SAM` files: tut1, title, catacomb, city, cleopal, coastal, karnak, settomb; `tom2pc.exe` |
| additional levels | the projects angkor, guard and library with their files, and two further projects (`angtemp`, `VertTriggersAndBreakClimb`) |

None of this data is part of the repository.

## 3. `pyprj2tom`

**Against the original export code.** The editor's export was run on the 11 sample projects with a
copy of the editor patched to export without a window (the editor has no working command-line
mode). `pyprj2tom` reproduces every byte the editor assigns in the `.TOM`, and the `.TXT` and `.CD`
files exactly. The only differing bytes are the light fields the editor leaves uninitialised
(1,095 to 3,326 bytes per level), which `pyprj2tom` writes as zeros
([why](formats/TOM_FORMAT.md#the-bytes-that-change-from-one-export-to-the-next)).

**Against the `.TOM` files shipped with the editor.** Same size for every level; the differing bytes
are those light fields, 1 or 2 bytes of the light table, and in `karnak` and `settomb` some bytes from
edits made to the projects after the shipped files were exported:

| level | bytes differing from the shipped `.TOM` | `.CD` |
|---|---|---|
| tut1 | 1,005 | identical |
| title | 1,339 | – (none shipped) |
| coastal | 1,552 | identical |
| cleopal | 1,702 | identical |
| catacomb | 1,802 | identical |
| city | 2,379 | identical |
| settomb | 3,269 | identical |
| karnak | 6,416 (3,124 of them texture bytes) | identical |

## 4. `pytom2pc`

`pytom2pc` was run on the shipped `.TOM` files of all eight installation levels and compared with
`tom2pc.exe` run on the same files (under Wine, on a copy of the installation): **all eight `.tr4`
files are byte-identical**.

Six of them are also identical to the `.tr4` files in the installation's `data` folder. `catacomb`
and `settomb` are not: those two shipped levels were built from older versions of their `.tom` files.
Their texture pages, font, sky and samples are the same, and only the level block differs; running
`tom2pc.exe` on the shipped `.tom` gives the same file as `pytom2pc`.

**The whole chain.** `title.PRJ` → `pyprj2tom` → `pytom2pc` gives the same `.tr4` as
`title.PRJ` → `pyprj2tom` → `tom2pc.exe`.

## 5. Portability and the command line

The renaming of the packages, the command-line options for every input file, the JSON object list and
the case-insensitive path handling were all introduced after the comparisons above. They were checked
by producing all outputs (21 exports and 20 conversions, 83 files) with the previous code and with
the new code, and comparing them:

| run | how the inputs were given |
|---|---|
| the 8 installation projects | classic: only `--root` (the installation); stored Windows paths translated; mixed-case file names on Linux |
| the 13 projects of the additional set | explicit: `--texture`, `--wad`, `--swd`, an unusable `--root`, and the bundled `objects.json` |
| the 8 installation levels | classic: everything beside the `.tom` |
| the 12 levels of the additional set | explicit: `--font`, `--default-sky`, `-o` |

All 83 files were byte-identical between the old and the new code. Every run used the bundled
`objects.json`; an export from a copy of the installation with `OBJECTS.H` deleted gives the same file. The installed package
(`pip install .`, console scripts `pyprj2tom` and `pytom2pc`, default `--root`) was checked the same
way. The tests ran on Linux with Python 3.12 and numpy 2; earlier comparisons ran on Windows.

## 6. Repeating the comparison

With a TRLE installation at `TRLE`:

```sh
# pytom2pc: should print "files are byte-identical" for the levels whose .tom matches their .tr4
pytom2pc TRLE/graphics/wads/city.tom --samples TRLE/sound/samples -o out/city.tr4
python tools/compare_tr4.py out/city.tr4 TRLE/data/city.tr4

# pyprj2tom: compare with the shipped .TOM (expect only the differences described in section 3)
pyprj2tom TRLE/maps/city/city.prj --out out
cmp -l out/city.TOM TRLE/graphics/wads/city.TOM | wc -l
```

`tools/compare_tr4.py` decompresses the four blocks of two `.tr4` files and reports which differ, so a
difference that is only in the compression (another zlib) can be told apart from a different level.

To compare with `tom2pc.exe` on other inputs, run it on a **copy** of the installation (it writes to
`data\`): `tom2pc.exe graphics\wads\<level>.tom` from the installation folder, under Wine on Linux
and macOS.

## 7. Not verified

* `.PCX` textures, project versions below `0x31`, `.wad` files of other object versions;
* the repairs of the project check (no sample project needs one);
* levels that exceed the original limits (20 pages per texture group, 16 animated ranges) beyond the
  fact that both programs refuse them.
