"""The sequence of steps that turns a .TOM level into a .TR4 file."""
import os
import struct

from pytom2pc.animated_textures import AnimatedTextures
from pytom2pc.level_files import LevelFiles, read_optional_file
from pytom2pc.level_writer import write_level_body, write_texture_pages
from pytom2pc.object_data import ObjectMeshConverter, WadObjectData
from pytom2pc.room_converter import RoomConverter
from pytom2pc.room_overview import read_tom_overview
from pytom2pc.sprite_atlas import SpriteAtlas
from pytom2pc.texture_info_table import TextureInfoTable, TextureSource
from pytom2pc.texture_repacker import TextureRepacker
from pytom2pc.texture_sheets import (compute_page_layout, detect_transparent_tiles,
                                   read_source_headers)
from pytom2pc.tr4_container import build_sample_section, compress_level
from pytom2pc.vertex_effects import VertexEffects


class LevelConverter:
    """Converts one level.  A new instance is needed for each conversion."""

    def __init__(self, destination_directory, samples_directory, log=print):
        """Set where the output goes and where the sound samples are."""
        self.destination_directory = destination_directory
        self.samples_directory = samples_directory
        self.log = log

    def convert(self, tom_path, overrides=None, output_path=None):
        """Convert the level and return the path of the new .TR4, or ``None`` if a file is missing.

        ``overrides`` gives explicit paths for the other input files (see
        :class:`~pytom2pc.level_files.LevelFiles`); the rest are expected beside the ``.tom``.
        ``output_path`` replaces the default ``<destination>/<level>.tr4``.
        """
        files = LevelFiles(tom_path, overrides)
        self.log(f'Converting {tom_path}')

        # Texture headers of all three source files, and the combined page stack.
        headers = read_source_headers(files, self.log)
        layout = compute_page_layout(headers.room_tiles, headers.object_tiles)
        room_textures = TextureSource(headers.room_tiles, layout.room_base,
                                      detect_transparent_tiles(headers.room_tiles,
                                                               headers.room_pixels))
        object_textures = TextureSource(headers.object_tiles, layout.object_base,
                                        detect_transparent_tiles(headers.object_tiles,
                                                                 headers.object_pixels))
        atlas = SpriteAtlas(layout, headers, self.log)
        page_count = atlas.build()

        # Objects of the .WAD; texture records are created as they are met.
        wad_rest = self._load_rest(files, 'wad', headers.wad_rest_offset)
        if wad_rest is None:
            return None
        wad = WadObjectData(wad_rest)
        texture_table = TextureInfoTable(headers.room_attributes)
        meshes = ObjectMeshConverter(wad, texture_table, object_textures, self.log)
        meshes.convert_all()

        level = bytearray()
        write_texture_pages(level, atlas, page_count, files, self.log)

        swd_rest = self._load_rest(files, 'swd', headers.swd_rest_offset)
        if swd_rest is None:
            return None
        sequence_count = struct.unpack_from('<i', swd_rest, 0)[0]
        sprite_sequences = (struct.pack('<i', sequence_count)
                            + swd_rest[4:4 + 8 * sequence_count])

        tom_rest = self._load_rest(files, 'tom', headers.tom_rest_offset)
        if tom_rest is None:
            return None
        overview = read_tom_overview(read_optional_file(files.path('tom')))
        animations = AnimatedTextures(overview.animated_ranges, headers.room_tiles, self.log)
        rooms = RoomConverter(tom_rest, texture_table, room_textures, animations,
                              VertexEffects(overview.rooms), self.log)

        self.log('Outputing uncompressed level')
        cinematic = read_optional_file(files.path('cin'))
        write_level_body(level, rooms, meshes, wad, atlas, sprite_sequences, tom_rest, animations,
                         texture_table, room_textures, cinematic, self.log)

        sound_effects = read_optional_file(files.path('sfx'))
        if not sound_effects:
            self.log(f"Cant Seem to Load {files.path('sfx')}")
            return None
        level += sound_effects
        self.log(f' Text Infos {len(texture_table.records)} '
                 f'Transparent {texture_table.transparent_count}')
        self.log(f' Maximum Room Vertices {rooms.largest_vertex_count}')
        self.log(f' Maximum Object Vertices {meshes.largest_vertex_count}')

        # PC-specific texture pass, compression and sound samples.
        self.log('Reprocess Data - PC Specific')
        repacked = TextureRepacker(bytes(level), self.log).run()
        tr4 = compress_level(repacked)
        if not output_path:
            output_path = os.path.join(self.destination_directory, files.level_name + '.tr4')
        self.log(f'Compressing To {output_path}')

        sample_list = read_optional_file(files.path('sam'))
        if sample_list:
            self.log('Adding Samples')
            tr4 += build_sample_section(sample_list, self.samples_directory, self.log)
        else:
            self.log(f"Cant Seem to Load {files.path('sam')}")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, 'wb') as file:
            file.write(tr4)
        return output_path

    def _load_rest(self, files, extension, offset):
        """Return a file's content after its texture header, or ``None`` if it cannot be read."""
        data = read_optional_file(files.path(extension))
        if not data or not data[offset:]:
            self.log(f'Cant Seem to Load {files.path(extension)}')
            return None
        return data[offset:]
