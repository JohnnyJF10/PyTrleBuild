"""Export a Tomb Raider Level Editor project (.PRJ) as a .TOM level file.

The package does the same work as the editor's "Output Wad" command and produces the same file,
apart from the light fields the editor leaves uninitialised (they are written as zero).  The order
of the work is shown in :mod:`pyprj2tom.exporter`.
"""
