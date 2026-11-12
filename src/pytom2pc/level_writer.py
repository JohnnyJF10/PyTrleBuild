"""Writing the uncompressed level in the game's order of sections.

Texture pages come first, then the rooms, followed by the objects taken from the .WAD and finally
the sections that .TOM and .WAD share (cameras, boxes, items and so on).  Some of these sections
are copied unchanged and others reduced to the form the game wants.
"""
import struct

import numpy as np

from trle_common.c_arithmetic import to_int16

_CAMERA_SIZE = 16
_FLYBY_CAMERA_SIZE = 0x28
_SOUND_SOURCE_SIZE = 16
_BOX_SIZE = 20
_ZONE_TABLES = 10
_ITEM_SIZE = 0x18
_AI_OBJECT_SIZE = 0x18
_SKIPPED_LIGHT_TABLE = 0x2000
_FONT_BYTES = 0x40000
_SKY_BYTES = 0x30000
_SECTOR_UNITS_SHIFT = 10           # world units per sector are 1024


def write_texture_pages(output, atlas, page_count, files, log):
    """Append page count, pages as 32-bit colour, the font and the sky."""
    output += struct.pack('<i', page_count)
    pixel_count = page_count * 0x10000
    rgb = atlas.rgb_pages[:pixel_count * 3]
    if rgb.size < pixel_count * 3:
        rgb = np.concatenate([rgb, np.zeros(pixel_count * 3 - rgb.size, np.uint8)])
    rgb = rgb.reshape(-1, 3).astype(np.uint32)
    argb = 0xFF000000 | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]
    argb[argb == 0xFFFF00FF] = 0                       # magenta becomes fully transparent
    output += argb.astype('<u4').tobytes()
    _append_font(output, files, log)
    _append_sky(output, files, log)


def _append_font(output, files, log):
    """Append the game's font image, if the file exists."""
    try:
        with open(files.shared_path('font.pc'), 'rb') as file:
            output += file.read(_FONT_BYTES)
    except OSError:
        log('Unable To Load Font File')


def _append_sky(output, files, log):
    """Append the level's own sky image, the default one, or nothing."""
    sky = None
    try:
        with open(files.path('raw'), 'rb') as file:
            sky = file.read(_SKY_BYTES)
        log('Using Specific Level Sky Texture File')
    except OSError:
        try:
            with open(files.shared_path('pcsky.raw'), 'rb') as file:
                sky = file.read(_SKY_BYTES)
            log('Using Default Sky Texture File')
        except OSError:
            log('Unable To Load Sky Texture File')
    if sky is None:
        return
    rgb = np.frombuffer(sky.ljust(_SKY_BYTES, b'\0'), np.uint8).reshape(-1, 3)
    bgra = np.empty((0x10000, 4), np.uint8)
    bgra[:, 0] = rgb[:, 2]
    bgra[:, 1] = rgb[:, 1]
    bgra[:, 2] = rgb[:, 0]
    bgra[:, 3] = 0xFF
    output += bgra.tobytes()


def write_level_body(output, room_converter, mesh_converter, wad, atlas, sprite_sequences,
                     tom_rest, animations, texture_table, room_textures, cinematic, log):
    """Append everything after the texture pages to the level buffer."""
    output += struct.pack('<i', 0)                     # reserved field
    offset = room_converter.write_rooms(output)

    output += struct.pack('<i', len(mesh_converter.mesh_bytes) // 2) + mesh_converter.mesh_bytes
    output += struct.pack('<i', len(mesh_converter.mesh_pointers))
    output += struct.pack(f'<{len(mesh_converter.mesh_pointers)}i', *mesh_converter.mesh_pointers)
    for count, chunk in wad.chunks.in_level_order():
        output += struct.pack('<i', count) + chunk

    output += b'SPR'
    output += struct.pack('<i', len(atlas.placed_sprites))
    for sprite in atlas.placed_sprites:
        output += struct.pack('<8H', *[word & 0xFFFF for word in sprite.as_words()])
    output += sprite_sequences

    offset = _write_cameras(output, tom_rest, offset)
    offset = _copy_counted(output, tom_rest, offset, _SOUND_SOURCE_SIZE)
    offset = _write_boxes_and_zones(output, tom_rest, offset)

    # The animated-range list of the .TOM is replaced by the converter's own table.
    range_count = struct.unpack_from('<i', tom_rest, offset)[0]
    animations.write(output, texture_table, room_textures)
    output += b'TEX'
    log(f'Textures {len(texture_table.records)}')
    output += texture_table.pack_all()
    offset += 4 + range_count * 8

    offset = _copy_counted(output, tom_rest, offset, _ITEM_SIZE)
    offset += _SKIPPED_LIGHT_TABLE                      # the 256-colour light table is not used
    map_bytes = struct.unpack_from('<i', tom_rest, offset)[0]
    log(f'2D Map {map_bytes + 4}')
    offset += map_bytes + 4
    ai_count = struct.unpack_from('<i', tom_rest, offset)[0]
    log(f'AI Stuff {ai_count}')
    output += tom_rest[offset:offset + 4 + _AI_OBJECT_SIZE * ai_count]
    output += cinematic or b'\0\0'


def _copy_counted(output, data, offset, record_size):
    """Copy a list that starts with a 32-bit record count."""
    count = struct.unpack_from('<i', data, offset)[0]
    output += data[offset:offset + 4 + count * record_size]
    return offset + 4 + count * record_size


def _write_cameras(output, data, offset):
    """Copy the cameras and flyby cameras.

    The second count is converted to a byte size and rounded towards zero to whole 32-bit words
    while skipping.  As flyby records are 40 bytes this has no effect in practice.
    """
    offset = _copy_counted(output, data, offset, _CAMERA_SIZE)
    count = struct.unpack_from('<i', data, offset)[0]
    output += data[offset:offset + 4 + _FLYBY_CAMERA_SIZE * count]
    size = count * _FLYBY_CAMERA_SIZE
    return offset + 4 + ((size + ((size >> 31) & 3)) >> 2) * 4


def _write_boxes_and_zones(output, data, offset):
    """Copy boxes (rewritten from world units to sector numbers), overlaps and zones."""
    box_count = struct.unpack_from('<i', data, offset)[0]
    output += struct.pack('<i', box_count)
    offset += 4
    for _ in range(box_count):
        x_min, x_max, z_min, z_max = struct.unpack_from('<4i', data, offset)
        output += bytes([(x_min >> _SECTOR_UNITS_SHIFT) & 0xFF,
                         ((x_max + 1) >> _SECTOR_UNITS_SHIFT) & 0xFF,
                         (z_min >> _SECTOR_UNITS_SHIFT) & 0xFF,
                         ((z_max + 1) >> _SECTOR_UNITS_SHIFT) & 0xFF])
        output += data[offset + 16:offset + 20]
        offset += _BOX_SIZE
    offset = _copy_counted(output, data, offset, 2)
    for _ in range(_ZONE_TABLES):
        output += data[offset:offset + 2 * box_count]
        offset += 2 * box_count
    return offset


def signed_page_count(value):
    """Page counts are stored as signed 16-bit numbers in the level."""
    return to_int16(value)
