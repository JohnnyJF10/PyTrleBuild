"""Command line of ``pytom2pc``: convert a level's .TOM file (plus .WAD, .SWD, ...) into a .TR4 file.

Every input file can be given explicitly; files not given are looked for beside the ``.tom``,
which is the only layout ``tom2pc.exe`` knew::

    pytom2pc level.tom --wad objs.wad --swd objs.swd --sfx objs.sfx --sam objs.sam --samples samples/
    pytom2pc path/to/trle/graphics/wads/level.tom           # classic: everything beside the .tom
"""
import argparse
import os
import sys

from pytom2pc.converter import LevelConverter
from pytom2pc.level_files import ConversionError

_EPILOG = """\
how the input files are found:
  Each file given explicitly is used as it is. Every other file is looked for in the directory of
  the .tom, named after it (level.wad, level.swd, ...); font.pc and pcsky.raw are looked for there
  too. Letter case is ignored on every operating system.
  Required: .tom, .wad, .swd, .sfx. Optional: .sam (no sound samples without it), .raw (level sky;
  otherwise pcsky.raw), font.pc, .cin (cut-scene data).

This is a study implementation of the Tomb Raider Level Editor's tom2pc.exe, not a production tool.
"""


def build_parser():
    """Return the argument parser (also used to generate the usage text in the documentation)."""
    parser = argparse.ArgumentParser(
        prog='pytom2pc', description='Convert a level exported by the Tomb Raider Level Editor '
                                     '(.TOM and its companion files) into a Tomb Raider 4 '
                                     'level (.TR4).',
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('tom', help='the room file (.TOM) written by the editor or by pyprj2tom')
    group = parser.add_argument_group('input files (default: beside the .tom, same name)')
    group.add_argument('--wad', metavar='FILE', help='objects: meshes, animations, textures (.WAD)')
    group.add_argument('--swd', metavar='FILE', help='sprites and sprite sequences (.SWD)')
    group.add_argument('--sfx', metavar='FILE', help='sound effect table (.SFX)')
    group.add_argument('--sam', metavar='FILE', help='list of the sound sample files (.SAM)')
    group.add_argument('--sky', metavar='FILE',
                       help='the level\'s sky image, 256 x 256 RGB (.RAW); default: level.raw, '
                            'else pcsky.raw')
    group.add_argument('--default-sky', metavar='FILE',
                       help='the sky used when the level has none (default: pcsky.raw)')
    group.add_argument('--font', metavar='FILE', help='the font pages (default: font.pc)')
    group.add_argument('--cin', metavar='FILE', help='cut-scene data (.CIN), optional')
    group.add_argument('--samples', metavar='DIR',
                       default=os.path.join(os.getcwd(), 'sound', 'samples'),
                       help='directory of the .WAV files named in the .SAM '
                            '(default: ./sound/samples)')
    output = parser.add_argument_group('output')
    output.add_argument('--dest', metavar='DIR', default=os.path.join(os.getcwd(), 'data'),
                        help='output directory, the file is named <level>.tr4 (default: ./data)')
    output.add_argument('-o', '--output', metavar='FILE',
                        help='output file; replaces --dest and the default name')
    output.add_argument('-q', '--quiet', action='store_true', help='print only errors')
    return parser


def main(arguments=None):
    """Convert one level; returns the exit code."""
    options = build_parser().parse_args(arguments)
    log = (lambda *_: None) if options.quiet else print
    overrides = {
        'wad': options.wad, 'swd': options.swd, 'sfx': options.sfx, 'sam': options.sam,
        'raw': options.sky, 'cin': options.cin,
        'font.pc': options.font, 'pcsky.raw': options.default_sky,
    }
    converter = LevelConverter(options.dest, options.samples, log)
    try:
        output_path = converter.convert(options.tom, overrides, options.output)
    except ConversionError as error:
        print(error, file=sys.stderr)
        return 1
    if output_path is None:
        print('pytom2pc: conversion failed, a required input file is missing (see above)',
              file=sys.stderr)
        return 1
    print('Written', output_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
