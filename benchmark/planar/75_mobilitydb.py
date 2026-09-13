#!/usr/bin/env python3
"""The ten benchmark queries answered by MobilityDB in PostgreSQL over the L0 files of a run.

The L0 day files are copied into the table `trips` of a PostgreSQL database with pg_parquet: the
trajectory stays its EWKB bytes and every other column is the file's, the covering structs as
composite types. Each query of queries_pg/ then runs once per window over that table, the
PostgreSQL text of the query of the same name in queries/: the same prune on the covering columns
followed by the same MobilityDB functions, its `:name` parameters bound as 70_queries.py binds them,
in a session whose time zone is UTC. Each answer is compared with L0's in the answers table of
72_answers.py number by number, since the two engines print a rounded value each in its own form
(`0.0` and `0`).

  RUN=<run> python3 planar/75_mobilitydb.py --answers answers.csv [--windows 1day ...] [--no-load]

Environment: RUN (required); ROOT (the repository's data/); PSQL (psql); PGHOST, PGPORT,
PGDATABASE and PGUSER, the database the extensions postgis, mobilitydb and pg_parquet are created
in, by a role that may read the server's files; MOBILITYDB_OUT (the table written,
$ROOT/results/planar/mobilitydb-answers.csv).
"""
import argparse
import csv
import glob
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('ROOT') or HERE.parents[1] / 'data')
PSQL = os.environ.get('PSQL', 'psql')

# The binding of 70_queries.py, reading the query text from queries_pg/ instead of queries/
spec = importlib.util.spec_from_file_location('queries70', HERE / '70_queries.py')
q70 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q70)
q70.QDIR = HERE / 'queries_pg'

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS mobilitydb CASCADE;
CREATE EXTENSION IF NOT EXISTS pg_parquet;
DROP TABLE IF EXISTS trips;
DROP TYPE IF EXISTS trip_bbox_t;
DROP TYPE IF EXISTS trip_tspan_t;
CREATE TYPE trip_bbox_t AS (xmin float8, ymin float8, xmax float8, ymax float8);
CREATE TYPE trip_tspan_t AS (tmin timestamp, tmax timestamp);
CREATE TABLE trips (mmsi bigint, ship_type text, segment_type text, trip bytea,
  trip_bbox trip_bbox_t, trip_tspan trip_tspan_t, trip_xmin float8, trip_ymin float8,
  trip_xmax float8, trip_ymax float8, trip_tmin timestamp, trip_tmax timestamp, srid integer,
  dt date);
"""


def psql(sql):
    """Run the statements in one session and return what its queries print, a NULL as `NULL`,
    the way DuckDB prints it in the answers table"""
    proc = subprocess.run([PSQL, '-X', '-q', '-A', '-t', '-F', '|', '-P', 'null=NULL',
                           '-v', 'ON_ERROR_STOP=1'],
                          input="SET TimeZone = 'UTC';\n" + sql, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f'psql failed: {proc.stderr[:600]}')
    return proc.stdout


def load(run):
    """The run's L0 day files into the table trips"""
    files = sorted(glob.glob(f'{os.path.abspath(run)}/L0/year=*/month=*/day-*.parquet'))
    if not files:
        sys.exit(f'no L0 under {run}')
    psql(SCHEMA + ''.join(f"COPY trips FROM '{f}' WITH (format 'parquet');\n" for f in files)
         + 'ANALYZE trips;')
    n = psql('SELECT count(*) FROM trips;').strip()
    print(f'trips: {n} rows from {len(files)} L0 files', flush=True)


def same(a, b):
    """Whether two answers state the same numbers, field by field"""
    fa, fb = a.split('|'), b.split('|')
    try:
        return len(fa) == len(fb) and all(float(x) == float(y) for x, y in zip(fa, fb))
    except ValueError:
        return a == b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--windows', nargs='+', default=['1h', '1day', '1week', '1month'])
    ap.add_argument('--queries', nargs='+', default=list(q70.QUERIES))
    ap.add_argument('--answers', help="72_answers.py's table of L0's answers, compared with")
    ap.add_argument('--no-load', action='store_true', help='query the table already loaded')
    a = ap.parse_args()
    run = os.environ.get('RUN') or sys.exit('RUN is required')
    out = Path(os.environ.get('MOBILITYDB_OUT', ROOT / 'results/planar/mobilitydb-answers.csv'))
    out.parent.mkdir(parents=True, exist_ok=True)
    if not a.no_load:
        load(run)

    truth = {}
    if a.answers:
        with open(a.answers) as f:
            for r in csv.DictReader(f):
                for w in a.windows:
                    truth[(r['query'], w)] = r.get(w) or ''

    regions, windows = q70.read_windows()
    agree = total = 0
    with open(out, 'w', newline='') as f:
        wr = csv.writer(f, lineterminator='\n')
        wr.writerow(['window', 'query', 'answer', 'l0_answer', 'matches_l0'])
        for win in a.windows:
            t0, t1 = windows[win]
            for q in a.queries:
                lines = psql('\n'.join(q70.statements(q, regions, t0, t1, 'flat'))).splitlines()
                answer = lines[-1].strip() if lines else ''
                ref = truth.get((q, win), '')
                match = str(same(answer, ref)) if ref else ''
                total += 1
                agree += match == 'True'
                wr.writerow([win, q, answer, ref, match])
                print(f'{win} {q}: {answer}   L0 {ref or "-"}', flush=True)
    if a.answers:
        print(f'{agree} of {total} answers equal L0\'s', flush=True)


if __name__ == '__main__':
    main()
