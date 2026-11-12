#!/usr/bin/env python3
"""Convert an editor object list (``objects.h``) into the JSON form read by ``pyprj2tom``.

    python tools/objects_h_to_json.py path/to/OBJECTS.H  out/objects.json

The header is read exactly the way the level editor reads it (see
``pyprj2tom.object_loading.read_objects_header``), so the JSON holds the same slot names in the same
order.  This is how ``src/pyprj2tom/data/objects.json`` was made from the editor's OBJECTS.H
(object version 129).  Use it for editor variants with a different object list.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from pyprj2tom.object_loading import object_list_to_json, read_objects_header  # noqa: E402


def main(arguments=None):
    """Read the header and write the JSON file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('header', help='objects.h of the level editor')
    parser.add_argument('json', help='JSON file to write')
    parser.add_argument('--source', default=None,
                        help='text for the "source" field (default: the header file name)')
    options = parser.parse_args(arguments)
    object_list = read_objects_header(options.header)
    source = options.source or os.path.basename(options.header)
    with open(options.json, 'w', encoding='utf-8', newline='\n') as file:
        file.write(object_list_to_json(object_list, source))
    with open(options.json, encoding='utf-8') as file:
        written = json.load(file)
    print(f'{options.json}: object version {written["object_version"]}, '
          f'{len(written["moveables"])} moveable and {len(written["statics"])} static slot names')
    return 0


if __name__ == '__main__':
    sys.exit(main())
