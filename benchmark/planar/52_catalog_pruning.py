#!/usr/bin/env python3
"""What each catalog lets a query window skip at the FILE level, measured two ways, beside the
plain data lake.

Per (catalog, bounds, layout, window):
  admitted   the files, and their bytes, whose bounds recorded in the catalog meet the window:
             pyiceberg's scan planning (manifest bounds) for Iceberg, the per-file column
             statistics for DuckLake;
  read       the files DuckDB's scan of the table actually reads (`Total Files Read` of EXPLAIN
             ANALYZE on a count over the window's bounds filter), so a catalog that records
             bounds its engine does not use shows up as admitted < read.
The `lake` rows, written when RUN names the run, are the same scan over the layout's files through
a read_parquet glob, which no catalog stands in front of: every file of the layout is admitted, and
read is what the scan reads. `bounds` is the covering form the filter names: `flat` (trip_xmin ..
trip_tmax, top-level) or `struct` (trip_bbox / trip_tspan fields). The windows are
windows_25832.csv; the tables are the <namespace>.<layout> of 80_register.py and
81_register_ducklake.sh.

  NAMESPACE=trips RUN=<run> python3 planar/52_catalog_pruning.py [L0 L3s ...]

Environment: ROOT (the repository's data/); RUN (the run whose files the lake rows read; without it
there are none); NAMESPACE (trips); DUCKDB_ENGINE; PRUNING_OUT (the table written,
$ROOT/results/planar/<namespace>-catalog-pruning.csv).
"""
import csv
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import And, GreaterThanOrEqual, LessThanOrEqual

ROOT = Path(os.environ.get('ROOT') or Path(__file__).resolve().parents[2] / 'data')
WINDOWS = Path(__file__).with_name('windows_25832.csv')
# The engine is DUCKDB_ENGINE when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
DUCKDB = str(Path(__file__).with_name('duckdb.sh'))
NS = os.environ.get('NAMESPACE', 'trips')
LAYOUTS = ['L0', 'L0X', 'L0Z', 'L0H', 'L1', 'L2', 'L3', 'L4', 'L1s', 'L2s', 'L3s', 'L4s']
COLS = {'flat': ('trip_xmin', 'trip_ymin', 'trip_xmax', 'trip_ymax', 'trip_tmin', 'trip_tmax'),
        'struct': ('trip_bbox.xmin', 'trip_bbox.ymin', 'trip_bbox.xmax', 'trip_bbox.ymax',
                   'trip_tspan.tmin', 'trip_tspan.tmax')}
SETUP = ["LOAD httpfs;", "LOAD iceberg;", "LOAD ducklake;",
         "CREATE SECRET (TYPE s3, KEY_ID 'admin', SECRET 'password', ENDPOINT 'localhost:9000', "
         "URL_STYLE 'path', USE_SSL false, REGION 'us-east-1');",
         "ATTACH 'warehouse' AS ice (TYPE iceberg, ENDPOINT 'http://localhost:8181', "
         "AUTHORIZATION_TYPE 'none');",
         f"ATTACH 'ducklake:{ROOT}/lakehouse-store/ducklake/{NS}.ducklake' AS dl (READ_ONLY);",
         "SET memory_limit = '4GB';"]


def layout_glob(run, layout):
    """The layout's files, as 70_queries.py reads them"""
    if layout == 'L0':
        return f'{run}/L0/year=*/month=*/day-*.parquet'
    if layout in ('L0X', 'L0Z', 'L0H'):
        return f'{run}/layouts_daily/{layout}/day-*.parquet'
    if layout in ('L1', 'L2', 'L3', 'L4'):
        return f'{run}/layouts_daily/{layout}/day-*/**/*.parquet'
    return f'{run}/layout_compact/{layout}/**/*.parquet'


def read_windows():
    with open(WINDOWS) as f:
        return [(n, float(x0), float(y0), float(x1), float(y1), t0, t1)
                for n, x0, y0, x1, y1, t0, t1 in csv.reader(f)]


def sql_filter(bounds, w):
    _, x0, y0, x1, y1, t0, t1 = w
    xmin, ymin, xmax, ymax, tmin, tmax = COLS[bounds]
    return (f"{xmax} >= {x0} AND {xmin} <= {x1} AND {ymax} >= {y0} AND {ymin} <= {y1} "
            f"AND {tmax} >= TIMESTAMP '{t0}' AND {tmin} <= TIMESTAMP '{t1}'")


def duck(lines):
    proc = subprocess.run([DUCKDB, '-unsigned', '-noheader', '-list'], input='\n'.join(lines) + '\n',
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f'duckdb failed: {proc.stderr[:400]}')
    return proc.stdout


def iceberg_admitted(catalog, layout, bounds, w):
    """Files and bytes pyiceberg's planning admits for the window, and the table's totals"""
    table = catalog.load_table((NS, layout.lower()))
    _, x0, y0, x1, y1, t0, t1 = w
    xmin, ymin, xmax, ymax, tmin, tmax = COLS[bounds]
    iso = lambda t: t.replace(' ', 'T')
    flt = And(GreaterThanOrEqual(xmax, x0), LessThanOrEqual(xmin, x1),
              GreaterThanOrEqual(ymax, y0), LessThanOrEqual(ymin, y1),
              GreaterThanOrEqual(tmax, iso(t0)), LessThanOrEqual(tmin, iso(t1)))
    adm = list(table.scan(row_filter=flt).plan_files())
    return len(adm), sum(t.file.file_size_in_bytes for t in adm)


def iceberg_totals(catalog, layout):
    allf = list(catalog.load_table((NS, layout.lower())).scan().plan_files())
    return len(allf), sum(t.file.file_size_in_bytes for t in allf)


def ducklake_admitted(layout, windows):
    """{window: (files, bytes)} DuckLake's column statistics admit, and the table's totals"""
    m = '__ducklake_metadata_dl'
    values = ', '.join(f"('{n}', {x0}, {y0}, {x1}, {y1}, TIMESTAMP '{t0}', TIMESTAMP '{t1}')"
                       for n, x0, y0, x1, y1, t0, t1 in windows)
    out = duck(SETUP + [f"""
WITH t AS (SELECT table_id FROM {m}.ducklake_table WHERE table_name = '{layout.lower()}' AND end_snapshot IS NULL),
f AS (SELECT data_file_id, file_size_bytes FROM {m}.ducklake_data_file
      WHERE table_id = (SELECT table_id FROM t) AND end_snapshot IS NULL),
c AS (SELECT column_id, column_name FROM {m}.ducklake_column
      WHERE table_id = (SELECT table_id FROM t) AND end_snapshot IS NULL AND parent_column IS NULL),
s AS (SELECT s.data_file_id,
        max(CASE WHEN c.column_name = 'trip_xmin' THEN s.min_value END)::DOUBLE AS fx0,
        max(CASE WHEN c.column_name = 'trip_xmax' THEN s.max_value END)::DOUBLE AS fx1,
        max(CASE WHEN c.column_name = 'trip_ymin' THEN s.min_value END)::DOUBLE AS fy0,
        max(CASE WHEN c.column_name = 'trip_ymax' THEN s.max_value END)::DOUBLE AS fy1,
        max(CASE WHEN c.column_name = 'trip_tmin' THEN s.min_value END)::TIMESTAMP AS ft0,
        max(CASE WHEN c.column_name = 'trip_tmax' THEN s.max_value END)::TIMESTAMP AS ft1
      FROM {m}.ducklake_file_column_stats s JOIN c USING (column_id)
      WHERE s.table_id = (SELECT table_id FROM t) GROUP BY s.data_file_id),
w(name, x0, y0, x1, y1, t0, t1) AS (VALUES {values})
SELECT w.name || '|' || count(*) FILTER (WHERE adm) || '|' || coalesce(sum(file_size_bytes) FILTER (WHERE adm), 0)
  || '|' || count(*) || '|' || sum(file_size_bytes)
FROM w, LATERAL (SELECT f.file_size_bytes,
    (s.fx1 >= w.x0 AND s.fx0 <= w.x1 AND s.fy1 >= w.y0 AND s.fy0 <= w.y1
     AND s.ft1 >= w.t0 AND s.ft0 <= w.t1) AS adm
  FROM f JOIN s USING (data_file_id))
GROUP BY w.name;"""])
    res = {}
    for line in out.splitlines():
        if line.count('|') == 4:
            name, af, ab, tf, tb = line.split('|')
            res[name] = (int(af), int(ab), int(tf), int(tb))
    return res


def files_read(relation, windows, setup=SETUP):
    """{(window, bounds): files the engine's scan of the relation reads}"""
    lines = list(setup)
    for w in windows:
        for bounds in ('flat', 'struct'):
            lines.append(f"SELECT '@@W {w[0]} {bounds}';")
            lines.append(f"EXPLAIN ANALYZE SELECT count(*) FROM {relation} "
                         f"WHERE {sql_filter(bounds, w)};")
    out = duck(lines)
    res, cur, label = {}, None, False
    for line in out.splitlines():
        if line.startswith('@@W '):
            _, name, bounds = line.split()
            cur, label = (name, bounds), False
            res[cur] = 0          # a scan pruned to nothing prints no file count
        elif cur and 'Total Files Read' in line:
            label = True          # the plan box wraps a count too wide for the label's line
        if cur and label:
            n = re.search(r'(\d+)\s*│\s*$', line)
            if n:
                res[cur], label = int(n.group(1)), False
    return res


def main():
    layouts = sys.argv[1:] or LAYOUTS
    windows = read_windows()
    run = os.environ.get('RUN')
    out = Path(os.environ.get('PRUNING_OUT', ROOT / f'results/planar/{NS}-catalog-pruning.csv'))
    out.parent.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog('rest', **{
        'uri': 'http://localhost:8181', 's3.endpoint': 'http://localhost:9000',
        's3.access-key-id': 'admin', 's3.secret-access-key': 'password', 's3.region': 'us-east-1'})
    with open(out, 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['catalog', 'bounds', 'layout', 'window', 'files_admitted', 'files_read',
                    'files_total', 'bytes_admitted', 'bytes_total', 'pct_bytes'])
        for layout in layouts:
            itf, itb = iceberg_totals(catalog, layout)
            iread = files_read(f'ice.{NS}.{layout.lower()}', windows)
            dadm = ducklake_admitted(layout, windows)
            dread = files_read(f'dl.{layout.lower()}', windows)
            if run:
                files = glob.glob(layout_glob(run, layout), recursive=True)
                ltf, ltb = len(files), sum(os.path.getsize(p) for p in files)
                lread = files_read(f"read_parquet('{layout_glob(run, layout)}', "
                                   f"hive_partitioning = false)", windows, setup=[])
            for win in windows:
                name = win[0]
                for bounds in ('flat', 'struct'):
                    af, ab = iceberg_admitted(catalog, layout, bounds, win)
                    w.writerow(['iceberg', bounds, layout, name, af, iread[(name, bounds)], itf,
                                ab, itb, f'{100.0 * ab / itb:.3f}'])
                    # DuckLake's file pruning reads the statistics of top-level columns only; the
                    # struct fields' statistics are recorded but not read, so a struct filter's
                    # admission is the files the engine reads.
                    daf, dab, dtf, dtb = dadm[name]
                    if bounds == 'struct':
                        daf, dab = dread[(name, bounds)], (dtb if dread[(name, bounds)] == dtf else '')
                    w.writerow(['ducklake', bounds, layout, name, daf, dread[(name, bounds)], dtf,
                                dab, dtb, f'{100.0 * dab / dtb:.3f}' if dab != '' else ''])
                    if run:
                        w.writerow(['lake', bounds, layout, name, ltf, lread[(name, bounds)], ltf,
                                    ltb, ltb, '100.000'])
            f.flush()
            print(f'{layout}: done', flush=True)


if __name__ == '__main__':
    main()
