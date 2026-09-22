"""Write the QGIS job files of the paper's two cell-cover figures.

One function per figure, after `#pruning_figure` and its neighbours in planar/74_figures.py, which
is the nearest figure generator this repository holds: that one draws a results chart with
matplotlib straight from a run's csv, so it has no job file to write and nothing here can call it.
These figures are maps over a basemap, which QGIS draws, so each function returns the job QGIS is
given (render_map.py) rather than a drawn figure.

One palette serves the four renders: class (i) in blue, class (ii) in orange, the track in red with
its recorded positions as dots, the protected area outlined in green, and the excerpt the finer
figure states outlined in black. The frames are those of FigFrame in 07_frames.sql, in EPSG:25832,
each with a 4:3 aspect, read from that file rather than repeated here, so moving a figure is an
edit in one place.

Environment: STAGE, the directory holding the GeoPackages 08_export.sh writes and the PNGs the
render leaves beside them, as the QGIS process sees it; STAGE_QGIS overrides it where that process
names the directory differently. Under a QGIS running on the Windows host the QGIS name is
`C:\\Windows\\Temp\\h3cover` while the shell writes /mnt/c/Windows/Temp/h3cover, and render.sh
passes the QGIS one.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.environ.get("STAGE_QGIS") or os.environ.get("STAGE") or HERE
SEP = "\\" if ":" in STAGE[1:3] else "/"
if not STAGE.endswith(SEP):
    STAGE += SEP
SIZE = [1100, 825]

FRAMES = {
    m.group(1): [int(v) for v in m.group(2).split(",")]
    for m in re.finditer(r"\('(\w+)',[^\n]*ST_MakeEnvelope\(([\d, ]+), 25832\)",
                         open(os.path.join(HERE, "07_frames.sql"), encoding="utf-8").read())
}
LAND = "#f2efe9"
BASEMAP = {"type": "shortbread", "water": "#cfe1f1", "streets": "#d6cdbf",
           "buildings": "#e3dbd0"}


def grid(fig):
    """The H3 grid of the figure's resolution, drawn under everything but the basemap."""
    return {"type": "vector", "path": STAGE + "grid.gpkg", "layer": "grid",
            "filter": f"\"fig\" = '{fig}'", "kind": "fill",
            "style": {"style": "no", "outline_color": "#7f8fa3", "outline_width": "0.08"}}


def cells(gpkg, res, cls):
    """One class of cover cells: (i) blue, (ii) orange, the distinction each figure makes."""
    style = {
        "i": {"color": "66,146,198,150", "outline_color": "#08519c", "outline_width": "0.22"},
        "ii": {"color": "253,141,60,200", "outline_color": "#a63603", "outline_width": "0.22"},
    }[cls]
    return {"type": "vector", "path": STAGE + gpkg, "layer": "cells",
            "filter": f"\"res\" = {res} AND \"class\" = '{cls}'", "kind": "fill", "style": style}


def excerpt(fig):
    """The outline of the finer figure's frame, drawn on the coarser one."""
    return {"type": "vector", "path": STAGE + "excerpt.gpkg", "layer": "excerpt",
            "filter": f"\"fig\" = '{fig}'", "kind": "fill",
            "style": {"style": "no", "outline_color": "#000000", "outline_width": "0.5"}}


def job(fig, layers):
    """The job of one render: the frame, and the layers from bottom to top."""
    return {"output": STAGE + f"h3cover_{fig}.png", "crs": "EPSG:25832",
            "extent": FRAMES[fig], "size": SIZE, "dpi": 300, "land": LAND,
            "layers": [BASEMAP, grid(fig)] + layers}


def trip_job(fig, res, fine):
    """The cover of a vessel track, with the track and its recorded positions over it."""
    layers = [cells("trip_cells.gpkg", res, "i"), cells("trip_cells.gpkg", res, "ii"),
              {"type": "vector", "path": STAGE + "trip_line.gpkg", "layer": "line", "kind": "line",
               "style": {"line_color": "#e31a1c", "line_width": "0.55" if fine else "0.4"}},
              {"type": "vector", "path": STAGE + "trip_samples.gpkg", "layer": "samples",
               "kind": "marker",
               "style": {"name": "circle", "color": "#67000d", "outline_style": "no",
                         "size": "0.9" if fine else "0.45"}}]
    if not fine:
        layers.append(excerpt(fig))
    return job(fig, layers)


def region_job(fig, res, fine):
    """The cover of a protected area, with the area's rings over it."""
    layers = [cells("region_cells.gpkg", res, "i"), cells("region_cells.gpkg", res, "ii"),
              {"type": "vector", "path": STAGE + "region_polygon.gpkg", "layer": "polygon",
               "kind": "fill",
               "style": {"style": "no", "outline_color": "#1b7837",
                         "outline_width": "0.6" if fine else "0.45"}}]
    if not fine:
        layers.append(excerpt(fig))
    return job(fig, layers)


JOBS = {
    "trip_res8": trip_job("trip_res8", 8, False),
    "trip_res9": trip_job("trip_res9", 9, True),
    "region_res8": region_job("region_res8", 8, False),
    "region_res9": region_job("region_res9", 9, True),
}

if __name__ == "__main__":
    missing = [n for n in JOBS if n not in FRAMES]
    if missing:
        print("07_frames.sql states no frame for: " + ", ".join(missing), file=sys.stderr)
        raise SystemExit(1)
    out = sys.argv[1] if len(sys.argv) > 1 else HERE
    for name, spec in JOBS.items():
        path = os.path.join(out, f"job_{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1)
        print("wrote", path)
