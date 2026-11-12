"""The sequence of steps of an export: load the project, compile the rooms, write the .TOM."""
import os

from pyprj2tom.input_files import ProjectInputs
from pyprj2tom.project_repair import repair_project
from pyprj2tom.project_reader import read_project
from pyprj2tom.room_compiler import RoomCompiler
from pyprj2tom.tom_writer import TomWriter
from trle_common.paths import split_stored_path


def export_project(project_path, inputs, output_directory, log=print, level_name=None):
    """Export a project; returns the path of the written .TOM file.

    ``inputs`` is a :class:`ProjectInputs` saying where the texture, the .WAD and the object list
    are; a plain string is taken as the editor folder (``ProjectInputs(root=...)``).  The output
    files are named after the object file stored in the project, like the editor does, unless
    ``level_name`` is given.
    """
    if not isinstance(inputs, ProjectInputs):
        inputs = ProjectInputs(root=inputs)
    project = read_project(project_path, inputs, log)
    repair_project(project, log)
    compiler = RoomCompiler(project)
    compiler.compile_all()          # the editor compiles all rooms once after loading

    # Starting the export refreshes the doors of the current room and compiles everything again.
    compiler.refresh_current_room_doors()
    compiler.refresh_current_room_doors()
    compiler.compile_all()

    if not level_name:
        stored = split_stored_path(project.wad_path)
        source = stored[-1] if stored else os.path.basename(project_path)
        level_name = os.path.splitext(source)[0]
    os.makedirs(output_directory, exist_ok=True)
    output_base = os.path.join(output_directory, level_name)
    TomWriter(project, compiler, output_base, log).write()
    return output_base + '.TOM'
