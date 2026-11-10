#!/usr/bin/env python3
"""What each layout returns for each query, against L0, before it is reduced to a number.

Each query of queries/ has a form in queries_sets/ that stops short of its final aggregate: the
vessels of Queries 1, 2 and 3, the vessel pairs of Query 9, and for the queries returning values,
the value of each vessel or pair (the clipped length of Query 4, the box of Query 5, the top speed
of Query 6, the position at the instant of Query 7, the closest approach of each pair within the
gate of Query 8) and the temporal count of Query 10. Their regions, windows and parameters are
those of 70_queries.py, whose binding this script reads with the query text taken from
queries_sets/ instead of queries/, as 75_mobilitydb.py takes it from queries_pg/; the belt queries'
first steps are queries/'s own files.

Each layout's rows are compared with L0's for the same window and query: a set is equal when it
holds the same keys, and a value when it differs from L0's by at most 1e-6 relative to its
magnitude (and 1e-9 in absolute value near zero), the order of the rounding of a double summed
over the pieces into which tiling cuts a trajectory; the temporal count is compared as its text.
The source `uncut` reads, in place of a layout, the segments stage 2 of run_clean.sh writes before
L0 cuts them at midnight, under the trips schema and with the day predicate dropped, since an
uncut segment can span two days: L0 against it checks the cut.

  RUN=<run> ./56_answer_sets.py [--layouts L0X L3s ... uncut] [--windows 1h ...] > answer-sets.csv

Every row of the table is one (layout, window, query): the keys of L0 and of the layout, the keys
missing from either, the largest relative difference of a value and its key. Environment: RUN
(required); ROOT; DUCKDB_ENGINE; QUERY_MEMORY (12GB); SEGMENTS, the segment files of `uncut`
(RUN/segments/*.parquet).
"""
import argparse
import csv
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# A row the engine prints can hold one long field: the temporal count of Query 10 over a week is a
# single text value longer than the 131,072 characters the csv module reads by default
csv.field_size_limit(sys.maxsize)

# The binding of 70_queries.py, reading the query text from queries_sets/ instead of queries/
spec = importlib.util.spec_from_file_location('queries70', HERE / '70_queries.py')
q70 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q70)
q70.QDIR = HERE / 'queries_sets'

# query -> the number of key columns, the rest being values; 0 compares the row as text
KEYS = {'q01': 1, 'q02': 1, 'q03': 1, 'q04': 1, 'q05': 1, 'q06': 1, 'q07': 1, 'q08': 2,
        'q09': 2, 'q10': 0}
REL, ABS = 1e-6, 1e-9
DAY = re.compile(r"dt BETWEEN DATE '[0-9-]+' AND DATE '[0-9-]+'")


def uncut_source(segments):
    """The segments under the trips schema, the view #trips_source of 70_queries.py makes of a
    layout's files, their bounds computed from each trajectory"""
    return [f"""CREATE OR REPLACE VIEW trips AS
SELECT mmsi AS MMSI, ship_type, segment_type, trip,
  {{'xmin': xmin(b), 'ymin': ymin(b), 'xmax': xmax(b), 'ymax': ymax(b)}} AS trip_bbox,
  {{'tmin': t0, 'tmax': t1}} AS trip_tspan,
  xmin(b) AS trip_xmin, ymin(b) AS trip_ymin, xmax(b) AS trip_xmax, ymax(b) AS trip_ymax,
  t0 AS trip_tmin, t1 AS trip_tmax, 25832 AS srid, t0::DATE AS dt
FROM (SELECT *, stbox(tgeompointFromEWKB(trip)) AS b FROM read_parquet('{segments}'));"""]


def rows(source, stmts, uncut):
    """The rows the last statement prints, from one engine set up as #engine_script of
    70_queries.py sets one up"""
    if uncut:
        stmts = [DAY.sub('TRUE', s) for s in stmts]
    spill = q70.ROOT / 'tmp' / f'spill.{os.getpid()}'
    spill.mkdir(parents=True, exist_ok=True)
    lines = ['LOAD spatial;', "SET TimeZone = 'UTC';", f"SET memory_limit = '{q70.MEM}';",
             f"SET temp_directory = '{spill}';"] \
        + source + ['.mode csv', '.headers off'] + stmts[:-1] + ['.print @@OUT', stmts[-1]]
    proc = subprocess.run([q70.DUCKDB, '-unsigned'], input='\n'.join(lines) + '\n',
                          capture_output=True, text=True)
    if proc.returncode != 0 or '@@OUT' not in proc.stdout:
        sys.exit(f'engine failed: {proc.stderr.strip()[:400]}')
    out = proc.stdout.split('@@OUT', 1)[1].splitlines()
    return [next(csv.reader([l])) for l in out if l.strip()]


def as_map(query, rs):
    """{key: values} of one query's rows"""
    k = KEYS[query]
    if k == 0:
        return {tuple(r): () for r in rs}
    return {tuple(r[:k]): tuple(r[k:]) for r in rs}


def compare(query, ref, got):
    """The keys of each side, those missing from either, and the largest relative difference of a
    value over the keys both hold: #same of 75_mobilitydb.py, field by field, per key and within
    the tolerance of a summed double"""
    a, b = as_map(query, ref), as_map(query, got)
    missing, extra = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    worst, worst_key = 0.0, ''
    for key in set(a) & set(b):
        for x, y in zip(a[key], b[key]):
            if x == y:
                continue
            if x in ('', 'NULL') or y in ('', 'NULL'):
                worst, worst_key = float('inf'), '/'.join(key)
                continue
            fx, fy = float(x), float(y)
            d = abs(fx - fy)
            rel = 0.0 if d <= ABS else d / max(abs(fx), abs(fy))
            if rel > worst:
                worst, worst_key = rel, '/'.join(key)
    return len(a), len(b), missing, extra, worst, worst_key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--layouts', nargs='+', default=q70.LAYOUTS[1:])
    ap.add_argument('--windows', nargs='+', default=['1h', '1day', '1week', '1month'])
    ap.add_argument('--queries', nargs='+', default=list(KEYS))
    a = ap.parse_args()
    run = Path(os.environ.get('RUN') or sys.exit('RUN is required'))
    segments = os.environ.get('SEGMENTS', f'{run}/segments/*.parquet')
    regions, windows = q70.read_windows()
    w = csv.writer(sys.stdout, lineterminator='\n')
    w.writerow(['layout', 'window', 'query', 'keys_L0', 'keys', 'missing', 'extra', 'agree',
                'max_rel_diff', 'worst_key'])
    for window in a.windows:
        t0, t1 = windows[window]
        for q in a.queries:
            stmts = q70.statements(q, regions, t0, t1, 'flat')
            ref = rows(q70.trips_source('lake', run, 'L0', ''), stmts, False)
            for layout in a.layouts:
                uncut = layout == 'uncut'
                source = (uncut_source(segments) if uncut
                          else q70.trips_source('lake', run, layout, ''))
                n0, n1, missing, extra, worst, key = compare(q, ref, rows(source, stmts, uncut))
                agree = not missing and not extra and worst <= REL
                w.writerow([layout, window, q, n0, n1, len(missing), len(extra), agree,
                            f'{worst:.3g}', key])
                sys.stdout.flush()
                print(f'{layout} {window} {q}: {n1}/{n0} keys, {len(missing)} missing, '
                      f'{len(extra)} extra, max rel {worst:.3g}', file=sys.stderr, flush=True)


if __name__ == '__main__':
    try:
        main()
    finally:
        shutil.rmtree(q70.ROOT / 'tmp' / f'spill.{os.getpid()}', ignore_errors=True)
