"""Command line of ``pyprj2tom``: export a .PRJ project as a .TOM level (plus .TXT and .CD).

Every input file can be given explicitly; the editor's folder layout is only the fallback::

    pyprj2tom level.prj --texture level.tga --wad level.wad [--swd level.swd] [--objects objects.json]
    pyprj2tom path/to/trle/maps/level/level.prj            # classic: files found inside the TRLE folder
"""
import argparse
import os
import sys

from pyprj2tom.exporter import export_project
from pyprj2tom.input_files import InputFileError, ProjectInputs
from pyprj2tom.object_loading import ObjectDataError
from pyprj2tom.project_reader import ProjectFormatError
from trle_common.paths import find_existing

_EPILOG = """\
how the input files are found:
  Each file given explicitly is used as it is. For the files not given, the paths stored in the
  project (e.g. C:\\...\\TRLE\\MAPS\\LEVEL\\level.TGA) are translated into the editor folder --root:
  everything from MAPS\\ or GRAPHICS\\ onward is attached to it. Without --objects, the object list
  bundled with this package (objects.json) is used. Letter case is ignored on every operating system.

This is a study implementation of the Tomb Raider Level Editor's export, not a production tool.
"""


def build_parser():
    """Return the argument parser (also used to generate the usage text in the documentation)."""
    parser = argparse.ArgumentParser(
        prog='pyprj2tom', description='Export a Tomb Raider Level Editor project (.PRJ) as a .TOM '
                                      'level, together with its .TXT object report and .CD list.',
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('prj', help='the project file (.PRJ)')
    group = parser.add_argument_group('input files (each optional; see below)')
    group.add_argument('--texture', metavar='TGA', help='the 24-bit .TGA texture of the project')
    group.add_argument('--wad', metavar='WAD', help='the object file (.WAD)')
    group.add_argument('--swd', metavar='SWD',
                       help='the sprite file (.SWD); default: beside the .WAD, if present')
    group.add_argument('--objects', metavar='FILE',
                       help='object list, as JSON or as an editor objects.h '
                            '(default: the bundled objects.json)')
    group.add_argument('--root', metavar='DIR',
                       help='the editor folder (holding maps/ and graphics/) used for '
                            'files not given explicitly (default: three levels above the project, '
                            'i.e. the TRLE folder for TRLE/maps/level/level.prj)')
    output = parser.add_argument_group('output')
    output.add_argument('--out', metavar='DIR', default='.',
                        help='output directory (default: the current directory)')
    output.add_argument('--name', metavar='NAME',
                        help='base name of the output files (default: the name of the object file '
                             'stored in the project, as the editor does)')
    output.add_argument('-q', '--quiet', action='store_true', help='print only errors')
    return parser


def main(arguments=None):
    """Run the export; returns the process exit code."""
    options = build_parser().parse_args(arguments)
    project_path = os.path.abspath(find_existing(options.prj))
    root = options.root or os.path.dirname(os.path.dirname(os.path.dirname(project_path)))
    inputs = ProjectInputs(root=root, objects=options.objects, texture=options.texture,
                           wad=options.wad, swd=options.swd)
    log = (lambda *_: None) if options.quiet else print
    try:
        written = export_project(project_path, inputs, options.out, log, level_name=options.name)
    except (InputFileError, ObjectDataError, ProjectFormatError, OSError) as error:
        print(f'pyprj2tom: {error}', file=sys.stderr)
        return 1
    print('Written', written)
    return 0


if __name__ == '__main__':
    sys.exit(main())
