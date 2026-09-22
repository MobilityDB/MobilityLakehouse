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
    """The grid the spatial layouts cut on, dashed so it reads as a rule and not as data."""
    return {"type": "vector", "path": STAGE + "tiles.gpkg", "layer": "tiles", "kind": "line",
            "style": {"line_color": "#111111", "line_width": "0.5", "line_style": "dash"}}


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


def job(fig, frames, layers, legend=None):
    """The job of one render: the frame, and the layers from bottom to top."""
    spec = {"output": STAGE + f"{fig}.png", "crs": "EPSG:25832", "extent": frames[fig],
            "size": SIZE, "dpi": 300, "land": LAND, "layers": [BASEMAP] + layers}
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


BUILDERS = {"L2_tiling_wide": tiling_job, "L3_tiling_wide": tiling_job,
            "L2_tiling_zoom": tiling_job, "L3_tiling_zoom": tiling_job,
            "segment_raw": raw_job, "clean_segmented": segmented_job}

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else HERE
    src = os.environ.get("FRAMES") or os.path.join(out, "frames.csv")
    if not os.path.exists(src):
        print(f"make_jobs.py: no frames at {src}; run 01_export.sql first", file=sys.stderr)
        raise SystemExit(1)
    with open(src, newline="", encoding="utf-8") as f:
        frames = {r["fig"]: [int(float(r[k])) for k in ("x0", "y0", "x1", "y1")]
                  for r in csv.DictReader(f)}
    missing = [n for n in BUILDERS if n not in frames]
    if missing:
        print("frames.csv states no frame for: " + ", ".join(missing), file=sys.stderr)
        raise SystemExit(1)
    for name, build in BUILDERS.items():
        path = os.path.join(out, f"job_{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(build(name, frames), f, indent=1)
        print("wrote", path)
