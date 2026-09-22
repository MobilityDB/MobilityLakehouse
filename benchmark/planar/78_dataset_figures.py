#!/usr/bin/env python3
"""Draw the dataset and in-file-ordering figures of the paper from a run.

PNG files in the output directory, beside the evaluation figures 74_figures.py writes:
  spatial_heatmap.png  the AIS messages of the raw zone binned on a lon/lat grid and coloured by
                       their count, which is where the traffic concentrates and so where the
                       evaluation regions are drawn from;
  inorder.png          one panel per in-file ordering, the segments of one day as their bounding
                       boxes' centroids, coloured by the row group holding them, with each row
                       group's own bounding box drawn over them: a tighter box is a box a query
                       can skip on.

  ./78_dataset_figures.py [DAY [OUTDIR]]

DAY is the day the ordering panels are drawn from, 2026-01-15 by default. The heatmap reads the
whole raw zone, binned by DuckDB so that only the occupied bins reach this process.

Environment: ROOT (the repository's data/); HEAT_BBOX (3,53,18,60), the lon/lat box the heatmap counts in; RUN (the run under ROOT/stage/planar/), holding L0/ and
layouts_daily/; RAW (ROOT/raw), the raw zone; DUCKDB_ENGINE (duckdb), the shell that reads the
Parquet; BIN, the heatmap's bin in degrees (0.01); OUTDIR defaults to figures/ under
ROOT/results/planar.
"""
import os
import subprocess
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# 74_figures.py's ink, and a qualitative ramp for the row groups, which are a nominal scale: a
# sequential one would suggest an order between groups that pruning does not care about.
INK, MUTED, GRID = '#0b0b0b', '#52514e', '#d8d7d2'
GROUP_COLORS = plt.get_cmap('tab20').colors
ORDERINGS = [('L0', 'L0 base (no sort)'), ('L0X', 'Lexicographic'),
             ('L0Z', 'Z-order (Morton)'), ('L0H', 'Hilbert')]


def duckdb(sql):
    """Run one statement and return its rows, the engine reporting its own errors."""
    engine = os.environ.get('DUCKDB_ENGINE', 'duckdb')
    out = subprocess.run([engine, '-csv', '-noheader', '-c', sql],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f'78_dataset_figures.py: {engine} failed\n{out.stderr.strip()}')
    return [line.split(',') for line in out.stdout.splitlines() if line]


def layout_file(run, layout, day):
    """The one Parquet file of a layout for a day; L0 keeps the Hive partitioning, the rest do not."""
    if layout == 'L0':
        y, m, _ = day.split('-')
        return os.path.join(run, 'L0', f'year={y}', f'month={m}', f'day-{day}.parquet')
    return os.path.join(run, 'layouts_daily', layout, f'day-{day}.parquet')


def heatmap(raw, out, binsize, bbox):
    """The raw zone's messages per lon/lat bin, binned in the engine rather than read out.

    One figure, one function, after `#pruning_figure` and its neighbours in 74_figures.py; nothing
    there reads the raw zone, so this reads it here rather than a results csv.

    The bin counts are taken over the feed's own area, HEAT_BBOX. The raw zone is the archives as
    they arrive, and they carry reports from every ocean, bins of one or two messages whose
    position or identity is wrong; 20_clean.sql removes them, but this figure is drawn before it.
    Left in, they stretch the axes across the whole world and press the traffic this figure is
    about into a few pixels.
    """
    glob = os.path.join(raw, '*.parquet')
    x0, y0, x1, y1 = bbox
    rows = duckdb(f"""
      SELECT round(Longitude / {binsize}) * {binsize} AS lon,
             round(Latitude / {binsize}) * {binsize} AS lat, count(*) AS n
      FROM read_parquet('{glob}')
      WHERE Longitude BETWEEN {x0} AND {x1} AND Latitude BETWEEN {y0} AND {y1}
      GROUP BY 1, 2""")
    if not rows:
        sys.exit(f'78_dataset_figures.py: no messages under {glob}')
    lon = [float(r[0]) for r in rows]
    lat = [float(r[1]) for r in rows]
    import math
    n = [math.log10(float(r[2])) for r in rows]

    fig, ax = plt.subplots(figsize=(7.6, 6.4))
    sc = ax.scatter(lon, lat, c=n, s=1.4, cmap='magma', linewidths=0, rasterized=True)
    cb = fig.colorbar(sc, ax=ax, pad=0.02)
    cb.set_label('log10(messages)', color=INK)
    cb.outline.set_edgecolor(GRID)
    ax.set_xlabel('longitude', color=INK)
    ax.set_ylabel('latitude', color=INK)
    ax.set_title('AIS traffic density, Danish waters', color=INK)
    ax.tick_params(colors=MUTED)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.set_aspect(1 / math.cos(math.radians(sum(lat) / len(lat))))
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor='white')
    plt.close(fig)
    print(f'wrote {out}  ({len(rows)} bins of {binsize} degrees)')


def row_groups(path):
    """Each row group's first and last row index, from the row counts the footer states."""
    rows = duckdb(f"""SELECT row_group_id, any_value(row_group_num_rows) FROM
                      parquet_metadata('{path}') GROUP BY 1 ORDER BY 1""")
    spans, start = [], 0
    for _, num in rows:
        spans.append((start, start + int(num)))
        start += int(num)
    return spans


def segments(path):
    """Every segment's centroid in file order, which is the order the row groups cut."""
    rows = duckdb(f"""SELECT file_row_number, (trip_xmin + trip_xmax) / 2,
                             (trip_ymin + trip_ymax) / 2
                      FROM read_parquet('{path}', file_row_number = true)
                      ORDER BY file_row_number""")
    return [(float(r[1]), float(r[2])) for r in rows]


def inorder(run, day, out):
    """One panel per ordering, the segments coloured by row group under each group's own box."""
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    for ax, (layout, title) in zip(axes.flat, ORDERINGS):
        path = layout_file(run, layout, day)
        if not os.path.exists(path):
            sys.exit(f'78_dataset_figures.py: no {path}')
        pts = segments(path)
        spans = row_groups(path)
        for g, (lo, hi) in enumerate(spans):
            part = pts[lo:hi]
            if not part:
                continue
            color = GROUP_COLORS[g % len(GROUP_COLORS)]
            ax.scatter([p[0] for p in part], [p[1] for p in part], s=3.5, color=color,
                       linewidths=0, rasterized=True)
            xs = [p[0] for p in part]
            ys = [p[1] for p in part]
            ax.add_patch(Rectangle((min(xs), min(ys)), max(xs) - min(xs), max(ys) - min(ys),
                                   fill=False, edgecolor=color, linewidth=1.4))
        ax.set_title(f'{title}  ({len(spans)} row group{"" if len(spans) == 1 else "s"})',
                     color=INK)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_aspect('equal')
        for s in ax.spines.values():
            s.set_color(GRID)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor='white')
    plt.close(fig)
    print(f'wrote {out}  (day {day})')


def main():
    # An empty DAY is the default rather than an error, so a caller naming only the output
    # directory passes one positionally without having to repeat the day.
    day = (sys.argv[1] if len(sys.argv) > 1 else '') or os.environ.get('DAY') or '2026-01-15'
    root = os.environ.get('ROOT') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    run = os.environ.get('RUN') or os.path.join(root, 'stage', 'planar')
    raw = os.environ.get('RAW') or os.path.join(root, 'raw')
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, 'results', 'planar', 'figures')
    os.makedirs(outdir, exist_ok=True)
    bbox = [float(v) for v in os.environ.get('HEAT_BBOX', '3,53,18,60').split(',')]
    heatmap(raw, os.path.join(outdir, 'spatial_heatmap.png'),
            float(os.environ.get('BIN', '0.01')), bbox)
    inorder(run, day, os.path.join(outdir, 'inorder.png'))


if __name__ == '__main__':
    main()
