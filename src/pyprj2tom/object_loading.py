"""Loading what the export needs to know about objects: slot names, and the .WAD and .SWD files.

The export uses very little of the object data: which slots are present, what class each one is
(moveable, static mesh, sprite), which mesh or sprite each uses, and whether its mesh stores vertex
normals.  The animations and meshes themselves are only used later, by ``pytom2pc``.

The **object list** names every slot the editor knows, in slot order.  The editor reads it from the
C header ``objects.h`` in its folder; this package ships the same list as
``pyprj2tom/data/objects.json`` and can read either form (see :func:`read_object_list`).
"""
import json
import os
from dataclasses import dataclass
from typing import List

from pyprj2tom.binary_reader import BinaryReader
from pyprj2tom.input_files import ProjectInputs
from pyprj2tom.project_model import (ObjectSlot, SLOT_MOVEABLE, SLOT_SPRITE, SLOT_STATIC)

_SLOT_TOKEN_DELIMITERS = ', \n\t'
_SECTION_END = '};'
_MAX_SLOT_NAMES = 0x400
_MAX_OBJECT_TEXTURES = 0x9C4
_MAX_SPRITES = 0x100
_BYTES_PER_ANIMATION = 0x28
_STATIC_RECORD_REST = 0x1A
_OBJECT_LIST_FORMAT = 'pyprj2tom-object-list'


class ObjectDataError(Exception):
    """An object definition file is missing or does not match."""


@dataclass
class ObjectList:
    """The slot names of the editor: moveables (animated objects) first, then static meshes."""
    object_version: int
    moveables: List[str]
    statics: List[str]


def read_object_list(path):
    """Read an object list from ``objects.json`` (this package's format) or ``objects.h``."""
    if path.lower().endswith('.json'):
        return _read_object_list_json(path)
    return read_objects_header(path)


def _read_object_list_json(path):
    """Read the JSON form of the object list."""
    with open(path, encoding='utf-8') as file:
        data = json.load(file)
    if data.get('format') != _OBJECT_LIST_FORMAT:
        raise ObjectDataError(f'{path} is not an object list (expected "format": '
                              f'"{_OBJECT_LIST_FORMAT}")')
    return ObjectList(int(data['object_version']), list(data['moveables']), list(data['statics']))


def object_list_to_json(object_list, source):
    """Return the JSON text for an object list; ``source`` describes where it came from."""
    statics = list(object_list.statics)
    if statics and statics[-1].startswith('NUMBER_'):
        statics.pop()           # the count entry of the C enum is not a slot
    document = {
        'format': _OBJECT_LIST_FORMAT,
        'source': source,
        'object_version': object_list.object_version,
        'moveables': object_list.moveables,
        'statics': statics,
    }
    return json.dumps(document, indent=1) + '\n'


def load_object_slots(project, inputs):
    """Fill in the project's object slots from the object list chosen by ``inputs``."""
    path = inputs.objects_path()
    if not os.path.isfile(path):
        raise ObjectDataError(f'object list not found: {path} (use --objects)')
    object_list = read_object_list(path)
    project.object_version = object_list.object_version
    project.moveable_slot_count = len(object_list.moveables)
    names = (object_list.moveables + object_list.statics)[:_MAX_SLOT_NAMES]
    project.object_slots = [ObjectSlot(name) for name in names]


def read_objects_header(path):
    """Read the object list from the editor's C header ``objects.h``, the way the editor does.

    The editor does not parse C.  It looks for the line containing ``OBJECT_VERSION`` and takes the
    first number on it, then for the line containing ``object_types`` and splits every following line
    at commas, spaces and tabs until a line containing ``};``.  The last name of that section is the
    count ``NUMBER_OBJECTS`` and is dropped.  The section after ``static_types`` is read the same way
    (its count name is kept, as in the editor; it is never used as a slot).
    """
    with open(path, 'rb') as file:
        # The editor opens the file in text mode, in which CR LF arrives as LF.
        lines = file.read().decode('latin-1').replace('\r\n', '\n').splitlines(True)
    position = 0

    def advance_to_line_containing(text):
        """Move to the next line containing ``text`` and return it."""
        nonlocal position
        while position < len(lines):
            line = lines[position]
            position += 1
            if text in line:
                return line
        raise ObjectDataError(f'{path}: no line containing "{text}"')

    def read_names_until_section_end(names):
        """Collect the names on the following lines, up to the end of the section."""
        nonlocal position
        while position < len(lines):
            line = lines[position]
            position += 1
            if _SECTION_END in line:
                return
            names.extend(_split_tokens(line))
        del names[_MAX_SLOT_NAMES:]

    version_line = advance_to_line_containing('OBJECT_VERSION')
    digits = ''.join(ch for ch in version_line if ch.isdigit() or ch == ' ').split()
    object_version = int(digits[0])

    advance_to_line_containing('object_types')
    moveable_names = []
    read_names_until_section_end(moveable_names)
    # The last name of the moveable section only counts the entries (NUMBER_OBJECTS).
    moveable_names.pop()

    advance_to_line_containing('static_types')
    static_names = []
    read_names_until_section_end(static_names)
    return ObjectList(object_version, moveable_names, static_names)


def _split_tokens(line):
    """Split a line at the delimiters of ``objects.h``."""
    tokens, current = [], []
    for character in line:
        if character in _SLOT_TOKEN_DELIMITERS:
            if current:
                tokens.append(''.join(current))
                current = []
        else:
            current.append(character)
    if current:
        tokens.append(''.join(current))
    return tokens


def load_wad_objects(project, inputs: ProjectInputs):
    """Read the slot usage of the .WAD and of the .SWD beside it."""
    wad_path = inputs.wad_path(project.wad_path)
    with open(wad_path, 'rb') as file:
        wad = BinaryReader(file.read())
    version = wad.i32()
    if version != project.object_version:
        raise ObjectDataError(f'{wad_path} has object version {version}, '
                              f'the object list has {project.object_version}')
    tile_count = wad.i32()
    wad.skip(tile_count * 8)
    image_bytes = wad.i32()
    wad.skip(image_bytes)

    pointer_count = wad.i32()
    mesh_pointers = list(wad.read(f'{pointer_count}i'))
    word_count = wad.i32()
    mesh_words = list(wad.read(f'{word_count}h'))
    for record_size in (_BYTES_PER_ANIMATION, 6, 8, 2):
        count = wad.i32()
        wad.skip(count * record_size)
    for _ in range(2):      # mesh trees and frames
        count = wad.i32()
        wad.skip(count * (4 if _ == 0 else 2))

    slots = project.object_slots
    moveable_count = wad.i32()
    for _ in range(moveable_count):
        slot_index = wad.i32()
        wad.skip(2)                 # number of meshes
        slot = slots[slot_index]
        slot.number = wad.i16()
        wad.skip(4 + 4 + 2)         # mesh tree, frame offset, first animation
        slot.kind = SLOT_MOVEABLE if slot_index < project.moveable_slot_count else SLOT_STATIC
    static_count = wad.i32()
    for _ in range(static_count):
        slot_index = wad.i32() + project.moveable_slot_count
        slot = slots[slot_index]
        slot.kind = SLOT_STATIC
        slot.number = wad.i16()
        wad.skip(_STATIC_RECORD_REST)

    project.mesh_has_negative_normal_count = [
        _mesh_stores_shades(mesh_words, pointer) for pointer in mesh_pointers]

    _load_sprite_sequences(project, inputs)


def _mesh_stores_shades(mesh_words, pointer):
    """A mesh with a negative normal count stores per-vertex shades instead of normals."""
    start = ((pointer & ~1) + 8) // 2
    vertex_count = mesh_words[start + 1]
    return mesh_words[start + 2 + 3 * vertex_count] < 0


def _load_sprite_sequences(project, inputs):
    """Read which slots are sprites from the .SWD (the sprites themselves are not needed)."""
    swd_path = inputs.swd_path(project.wad_path)
    if not swd_path or not os.path.isfile(swd_path):
        return
    with open(swd_path, 'rb') as file:
        swd = BinaryReader(file.read())
    swd.skip(4)                     # version
    sprite_count = swd.i32()
    swd.skip(min(sprite_count, _MAX_SPRITES) * 16 + max(sprite_count - _MAX_SPRITES, 0) * 16)
    image_bytes = swd.i32()
    swd.skip(image_bytes)

    sequence_count = swd.i32()
    for _ in range(sequence_count):
        slot_index = swd.i32()
        slot = project.object_slots[slot_index]
        slot.kind = SLOT_SPRITE + (0x100 if slot_index >= project.moveable_slot_count else 0)
        swd.skip(2)                 # sprite count of the sequence
        slot.number = swd.i16()
