"""Write the QGIS job files of the tiling and segmentation figures.

One function per figure, after `#trip_job` and `#region_job` in planar/coverfig/make_jobs.py, whose
renderer (coverfig/render_map.py) draws these too: they are the same kind of map, vector layers
over an OpenStreetMap basemap, and differ only in the layers 01_export.sql writes.

The frames come from frames.csv, which 01_export.sql computes, so the track figures follow the
vessel's own extent and no coordinate is written twice.

Environment: STAGE, the directory holding the GeoPackages and taking the PNGs; STAGE_QGIS, the same
directory as the QGIS process names it, where that differs.
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.environ.get("STAGE_QGIS") or os.environ.get("STAGE") or HERE
SEP = "\\" if ":" in STAGE[1:3] else "/"
if not STAGE.endswith(SEP):
    STAGE += SEP
SIZE = [1100, 825]
LAND = "#f2efe9"
BASEMAP = {"type": "shortbread", "water": "#cfe1f1", "streets": "#d6cdbf",
           "buildings": "#e3dbd0"}
MOVE, STOP = "#2171b5", "#e31a1c"


def boxes(layout, fine):
    """The stored bounding box of every segment of one layout, drawn as an outline."""
    return {"type": "vector", "path": STAGE + "boxes.gpkg", "layer": "boxes",
            "filter": f"\"layout\" = '{layout}'", "kind": "fill",
            "style": {"style": "no", "outline_color": "#08519c",
                      "outline_width": "0.12" if fine else "0.06"}}


def tiles():
    """The region cells `spaceTiles` answers, outlined and dashed so they read as a rule, not data.

    An unfilled outline, after `#region` and `#boxes` beside it, since a cell is a rectangle like
    they are; the dash is what separates the rule from the data drawn inside it.
    """
    return {"type": "vector", "path": STAGE + "tiles.gpkg", "layer": "tiles", "kind": "fill",
            "style": {"style": "no", "outline_color": "#111111", "outline_width": "0.5",
                      "outline_style": "dash"}}


def tile_lines(width):
    """The region cells' boundaries as lines, each drawn once, dashed, after `#tiles`.

    A map that dashes the grid over data draws these rather than the cells, since the edge two
    cells share would be drawn twice with two dash phases and read as solid in places.
    """
    return {"type": "vector", "path": STAGE + "tile_lines.gpkg", "kind": "line",
            "style": {"line_color": "#000000", "line_width": width, "line_style": "dash"}}


def region(name):
    """The query region the tiles are compared against."""
    return {"type": "vector", "path": STAGE + "region.gpkg", "layer": "region",
            "filter": f"\"name\" = '{name}'", "kind": "fill",
            "style": {"style": "no", "outline_color": "#e31a1c", "outline_width": "0.9"}}


def segments(kind, color, width):
    """One class of segment, the type the segmentation assigned."""
    return {"type": "vector", "path": STAGE + "track_segments.gpkg", "layer": "track_segments",
            "filter": f"\"type\" = '{kind}'", "kind": "line",
            "style": {"line_color": color, "line_width": width}}


def job(fig, frames, layers, legend=None, size=None):
    """The job of one render: the frame, and the layers from bottom to top."""
    spec = {"output": STAGE + f"{fig}.png", "crs": "EPSG:25832", "extent": frames[fig],
            "size": size or SIZE, "dpi": 300, "land": LAND, "layers": [BASEMAP] + layers}
    if legend:
        spec["legend"] = legend
    return spec


def tiling_job(fig, frames):
    """A layout's stored boxes over the grid it cuts on, with the query region over both."""
    layout, fine = fig.split("_")[0], fig.endswith("zoom")
    return job(fig, frames, [boxes(layout, fine), tiles(), region("belt_1month")])


def raw_job(fig, frames):
    """Every report the feed carries for one vessel on one day, before cleaning."""
    return job(fig, frames, [{"type": "vector", "path": STAGE + "track_raw.gpkg",
                              "layer": "track_raw", "kind": "marker",
                              "style": {"name": "circle", "color": MOVE,
                                        "outline_style": "no", "size": "0.5"}}])


def seg_points(kind, color, size):
    """A segment of a single position, which is a point rather than a line.

    The marker counterpart of `#segments` beside it, so the figure draws every segment its
    caption counts rather than only those long enough to be a line.
    """
    return {"type": "vector", "path": STAGE + "track_points.gpkg", "layer": "track_points",
            "filter": f"\"type\" = '{kind}'", "kind": "marker",
            "style": {"name": "circle", "color": color, "outline_style": "no", "size": size}}


def segmented_job(fig, frames):
    """The same vessel after cleaning and segmentation, each segment drawn by its type."""
    return job(fig, frames,
               [segments("In motion", MOVE, "0.5"), segments("Stationary", STOP, "1.4"),
                seg_points("In motion", MOVE, "0.8"), seg_points("Stationary", STOP, "1.4")],
               legend={"origin": [0.02, 0.05], "font_px": 26,
                       "items": [["stop segment", STOP], ["in motion", MOVE]]})


TRIP_FILE_COLORS = ["#2166ac", "#d6604d", "#1b7837", "#762a83"]
TRIP_FILES = []


def trip_tiling_job(fig, frames):
    """One trip's pieces in one layout, after `#tiling_job`: per file, the pieces' stored boxes
    outlined and their tracks in that file's colour, and the region cells dashed on top.

    A file is one region cell of the day, so the colour says which file a piece is stored in; the
    files are coloured in the order of their cells, alike in both layouts.
    """
    layout = fig.split("_")[1]
    layers = []
    for f, c in zip(TRIP_FILES, TRIP_FILE_COLORS):
        flt = f"\"layout\" = '{layout}' AND \"file\" = '{f}'"
        layers.append({"type": "vector", "path": STAGE + "trip_boxes.gpkg", "filter": flt,
                       "kind": "fill",
                       "style": {"style": "no", "outline_color": c, "outline_width": "0.25"}})
        layers.append({"type": "vector", "path": STAGE + "trip_tracks.gpkg", "filter": flt,
                       "kind": "line", "style": {"line_color": c, "line_width": "0.4"}})
    return job(fig, frames, layers + [tile_lines("0.4")])


FATE = {"file": "#9a9a9a", "read": "#2166ac", "mbb": "#e66101"}
CROSSING = {"q101_overview": ([660, 1100], 0.25, 0.35), "q101_rodby": (SIZE, 0.5, 0.5),
            "q101_puttgarden": (SIZE, 0.5, 0.5)}


def crossing_job(fig, frames):
    """One crossing of the query of both ports, after `#tiling_job`: the region cells and the box L0
    stores for the crossing dashed, the boxes of its L3 pieces by what the query does with them
    (gray: file discarded by the catalog; blue: read, rejected by the box test; orange: kept by the
    box test), the track, the ports, the part refinement keeps, and a ring around a piece too small
    to see where the frame shows one.
    """
    size, box_w, line_w = CROSSING[fig]
    layers = [tile_lines("0.3"),
              {"type": "vector", "path": STAGE + "q101_l0box.gpkg", "kind": "fill",
               "style": {"style": "no", "outline_color": "#000000", "outline_width": "0.45",
                         "outline_style": "dash"}}]
    for fate, color in FATE.items():
        layers.append({"type": "vector", "path": STAGE + "q101_pieces.gpkg", "kind": "fill",
                       "filter": f"\"fate\" = '{fate}'",
                       "style": {"style": "no", "outline_color": color,
                                 "outline_width": str(box_w)}})
    layers += [
        {"type": "vector", "path": STAGE + "q101_track.gpkg", "kind": "line",
         "style": {"line_color": "#333333", "line_width": str(line_w)}},
        {"type": "vector", "path": STAGE + "q101_ports.gpkg", "kind": "fill",
         "style": {"color": "215,48,39,40", "outline_color": "#d73027", "outline_width": "0.45"}},
        {"type": "vector", "path": STAGE + "q101_kept.gpkg", "kind": "line",
         "style": {"line_color": "#b2182b", "line_width": "1.2"}}]
    for fate, color in FATE.items():
        layers.append({"type": "vector", "path": STAGE + "q101_small.gpkg", "kind": "fill",
                       "filter": f"\"fig\" = '{fig}' AND \"fate\" = '{fate}'",
                       "style": {"style": "no", "outline_color": color, "outline_width": "0.6"}})
    return job(fig, frames, layers, size=size)


USE_REGION = {"type": "vector", "path": STAGE + "use_region.gpkg", "kind": "fill",
              "style": {"style": "no", "outline_color": "#d73027", "outline_width": "0.6"}}


def usefulness_month_job(fig, frames):
    """The month's in-motion segments over the study area, with the query region."""
    return job(fig, frames, [
        {"type": "vector", "path": STAGE + "use_month_tracks.gpkg", "kind": "line",
         "style": {"line_color": "33,102,172,40", "line_width": "0.05"}},
        dict(USE_REGION, style=dict(USE_REGION["style"], outline_width="0.8"))])


def usefulness_query_job(fig, frames):
    """The query region over its window: the month's traffic, the region cells, the pieces of the
    admitted files, those whose box intersects the query, and the region."""
    return job(fig, frames, [
        {"type": "vector", "path": STAGE + "use_month_tracks.gpkg", "kind": "line",
         "style": {"line_color": "120,120,120,60", "line_width": "0.08"}},
        tile_lines("0.35"),
        {"type": "vector", "path": STAGE + "use_pieces.gpkg", "kind": "line",
         "filter": "\"mbb\" = 0", "style": {"line_color": "#2166ac", "line_width": "0.25"}},
        {"type": "vector", "path": STAGE + "use_pieces.gpkg", "kind": "line",
         "filter": "\"mbb\" = 1", "style": {"line_color": "#e66101", "line_width": "0.35"}},
        USE_REGION])


BUILDERS = {"L2_tiling_wide": tiling_job, "L3_tiling_wide": tiling_job,
            "L2_tiling_zoom": tiling_job, "L3_tiling_zoom": tiling_job,
            "segment_raw": raw_job, "clean_segmented": segmented_job,
            "tiling_L2_trip": trip_tiling_job, "tiling_L3_trip": trip_tiling_job,
            "q101_overview": crossing_job, "q101_rodby": crossing_job,
            "q101_puttgarden": crossing_job,
            "usefulness_month": usefulness_month_job, "usefulness_query": usefulness_query_job}

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else HERE
    src = os.environ.get("FRAMES") or os.path.join(out, "frames.csv")
    if not os.path.exists(src):
        print(f"make_jobs.py: no frames at {src}; run 01_export.sql first", file=sys.stderr)
        raise SystemExit(1)
    with open(src, newline="", encoding="utf-8") as f:
        frames = {r["fig"]: [int(float(r[k])) for k in ("x0", "y0", "x1", "y1")]
                  for r in csv.DictReader(f)}
    # The files the trip's pieces are stored in, in the order of their cells, which 02_trip_export.sql
    # writes; there are no more files than colours, since a trip framed at a corner of four cells
    # meets four at most
    trip_src = os.path.join(out, "trip_files.csv")
    if not os.path.exists(trip_src):
        print(f"make_jobs.py: no trip files at {trip_src}; run 02_trip_export.sql first",
              file=sys.stderr)
        raise SystemExit(1)
    with open(trip_src, newline="", encoding="utf-8") as f:
        TRIP_FILES.extend(r["file"] for r in csv.DictReader(f))
    if len(TRIP_FILES) > len(TRIP_FILE_COLORS):
        print(f"make_jobs.py: the trip meets {len(TRIP_FILES)} files, more than "
              f"{len(TRIP_FILE_COLORS)} colours", file=sys.stderr)
        raise SystemExit(1)
    missing = [n for n in BUILDERS if n not in frames]
    if missing:
        print("frames.csv states no frame for: " + ", ".join(missing), file=sys.stderr)
        raise SystemExit(1)
    for name, build in BUILDERS.items():
        path = os.path.join(out, f"job_{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(build(name, frames), f, indent=1)
        print("wrote", path)
