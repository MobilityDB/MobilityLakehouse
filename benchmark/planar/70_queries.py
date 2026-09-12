#!/usr/bin/env python3
"""The ten benchmark queries of the paper over the planar layouts, timed.

For each (layout, window, query) the query runs ITER times and each run records its answer, its
wall time as DuckDB's timer reports it (summed over the statements of the run: a belt query is its
prune into `mt` plus the query on it), and the engine's peak resident memory.

  warm  one engine per (layout, window, query): the query runs once untimed, which touches the
        files, then ITER timed runs in the same engine.
  cold  per run, the page cache is dropped and a fresh engine runs the query once.

The query text is queries/*.sql beside this script, the listings of the paper with its `:name`
parameters, bound here by substitution; the regions and windows are windows_25832.csv
(45_windows.sql). The source `lake` reads the layout's Parquet files directly with read_parquet;
`iceberg` reads the table <namespace>.<layout> through the REST catalog 80_register.py registers
the layouts in; `ducklake` reads it through the DuckLake catalog of 81_register_ducklake.sh.
`--bounds flat` (the default) filters on the top-level copies trip_xmin .. trip_tmax, which a
catalog prunes files on; `--bounds struct` filters on trip_bbox / trip_tspan, which only row-group
pruning reads.

  RUN=data/stage/planar/2026-01-01_2026-02-01 benchmark/planar/70_queries.py --mode warm \
      [--layouts L0 L0X ...] [--windows 1h 1day ...] [--queries q01 q02 ...] [--iter 5]

Environment: RUN (required); ROOT (the repository's data/), the tree holding results/, tmp/ and
the DuckLake store; DUCKDB; QUERY_MEMORY (12GB); QUERY_OUT.
"""
import argparse
import csv
import datetime as dt
import glob
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get('ROOT') or Path(__file__).resolve().parents[2] / 'data')
QDIR = Path(__file__).resolve().with_name('queries')
WINDOWS = Path(__file__).resolve().with_name('windows_25832.csv')
# The engine is $DUCKDB when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
os.environ['DUCKDB_ENGINE'] = os.environ.get('DUCKDB_ENGINE') or os.environ.get('DUCKDB') or ''
DUCKDB = str(Path(__file__).with_name('duckdb.sh'))
MEM =os.environ.get('QUERY_MEMORY', '12GB')
SRID = 25832

# query -> (region, gate in metres or None); q01 reads the two ports
QUERIES = {
    'q01': ('ports', None), 'q02': ('goteborg', None),
    'q03': ('belt', None), 'q04': ('belt', None), 'q05': ('belt', None),
    'q06': ('belt', None), 'q07': ('belt', None),
    'q08': ('belt', 2000), 'q09': ('belt', 300), 'q10': ('belt', 500)}
LAYOUTS = ['L0', 'L0X', 'L0Z', 'L0H', 'L1', 'L2', 'L3', 'L4', 'L1s', 'L2s', 'L3s', 'L4s']


def layout_glob(run, layout):
    """The layout's files, as 50_query_layouts.sh reads them"""
    if layout == 'L0':
        return f'{run}/L0/year=*/month=*/day-*.parquet'
    if layout in ('L0X', 'L0Z', 'L0H'):
        return f'{run}/layouts_daily/{layout}/day-*.parquet'
    if layout in ('L1', 'L2', 'L3', 'L4'):
        return f'{run}/layouts_daily/{layout}/day-*/**/*.parquet'
    if layout in ('L1s', 'L2s', 'L3s', 'L4s'):
        return f'{run}/layout_compact/{layout}/**/*.parquet'
    sys.exit(f'unknown layout {layout}')


def read_windows():
    """{region: (x0, y0, x1, y1)} and {window: (t0, t1)} from windows_25832.csv"""
    regions, windows = {}, {}
    with open(WINDOWS) as f:
        for name, x0, y0, x1, y1, t0, t1 in csv.reader(f):
            region, window = name.rsplit('_', 1)
            regions[region] = tuple(float(v) for v in (x0, y0, x1, y1))
            windows[window] = (dt.datetime.fromisoformat(t0), dt.datetime.fromisoformat(t1))
    return regions, windows


def bind(text, params):
    """Substitute the :name parameters, longest name first so :t0 never eats :t0s"""
    for name in sorted(params, key=len, reverse=True):
        text = text.replace(':' + name, str(params[name]))
    left = re.findall(r'(?<![:\w]):[a-z][a-z0-9]*\b', re.sub(r"'[^']*'", '', text))
    if left:
        sys.exit(f'unbound parameters {sorted(set(left))}')
    return text


FLAT = {'trip_bbox.xmin': 'trip_xmin', 'trip_bbox.ymin': 'trip_ymin',
        'trip_bbox.xmax': 'trip_xmax', 'trip_bbox.ymax': 'trip_ymax',
        'trip_tspan.tmin': 'trip_tmin', 'trip_tspan.tmax': 'trip_tmax'}


def covering(text, bounds):
    """The query text filtering on the struct covering fields or on their top-level copies"""
    if bounds == 'flat':
        for field, column in FLAT.items():
            text = text.replace(field, column)
    return text


def statements(query, regions, t0, t1, bounds):
    """The statements one run of the query executes, bound"""
    region, gate = QUERIES[query]
    ts = lambda t: t.strftime('%Y-%m-%d %H:%M:%S')
    tmid = t0 + (t1 - t0) / 2
    p = {'t0': f"TIMESTAMP '{ts(t0)}'", 't1': f"TIMESTAMP '{ts(t1)}'",
         't0z': f"TIMESTAMPTZ '{ts(t0)}+00'", 't1z': f"TIMESTAMPTZ '{ts(t1)}+00'",
         'tmidz': f"TIMESTAMPTZ '{ts(tmid)}+00'",
         't0s': f'{ts(t0)}+00', 't1s': f'{ts(t1)}+00',
         'd0': f"DATE '{t0.date()}'", 'd1': f"DATE '{t1.date()}'", 'srid': SRID}
    if gate is not None:
        p['gate'] = gate
    if region == 'ports':
        for k, r in (('a', 'rodby_port'), ('b', 'puttgarden')):
            x0, y0, x1, y1 = regions[r]
            p.update({k + 'xmin': x0, k + 'ymin': y0, k + 'xmax': x1, k + 'ymax': y1})
    else:
        x0, y0, x1, y1 = regions[region]
        p.update({'rxmin': x0, 'rymin': y0, 'rxmax': x1, 'rymax': y1})
    read = lambda n: covering((QDIR / n).read_text(), bounds)
    if region != 'belt':
        return [bind(read(query + '.sql'), p)]
    body = read('belt_clipped.sql')
    if gate is not None:
        body += read('belt_pairs.sql')
    body += read(query + '.sql')
    return [bind(read('belt_prelude.sql'), p), bind(body, p)]


def trips_source(kind, run, layout, namespace):
    """The statements that make `trips` the layout: its files, or its table in the catalog"""
    if kind == 'lake':
        return [f"CREATE OR REPLACE VIEW trips AS SELECT * FROM read_parquet('{layout_glob(run, layout)}');"]
    if kind == 'ducklake':
        return ['LOAD httpfs;', 'LOAD ducklake;',
                "CREATE SECRET (TYPE s3, KEY_ID 'admin', SECRET 'password', ENDPOINT 'localhost:9000', "
                "URL_STYLE 'path', USE_SSL false, REGION 'us-east-1');",
                f"ATTACH 'ducklake:{ROOT}/lakehouse-store/ducklake/{namespace}.ducklake' AS dl (READ_ONLY);",
                f'CREATE OR REPLACE VIEW trips AS SELECT * FROM dl.{layout.lower()};']
    return ['LOAD httpfs;', 'LOAD iceberg;',
            "CREATE SECRET (TYPE s3, KEY_ID 'admin', SECRET 'password', ENDPOINT 'localhost:9000', "
            "URL_STYLE 'path', USE_SSL false, REGION 'us-east-1');",
            "ATTACH 'warehouse' AS ice (TYPE iceberg, ENDPOINT 'http://localhost:8181', "
            "AUTHORIZATION_TYPE 'none');",
            f'CREATE OR REPLACE VIEW trips AS SELECT * FROM ice.{namespace}.{layout.lower()};']


def engine_script(path, source, spill, runs, stmts):
    """One engine's script: setup, then each run marked and timed"""
    lines = ['LOAD spatial;', "SET TimeZone = 'UTC';", f"SET memory_limit = '{MEM}';",
             f"SET temp_directory = '{spill}';"] + source + [
             '.mode list', '.headers off', '.timer on']
    for k in runs:
        lines.append(f'.print @@RUN {k}')
        lines.extend(stmts)
    path.write_text('\n'.join(lines) + '\n')


def run_engine(script):
    """Run one engine; return ({run: (seconds, answer)}, peak_kb, error)"""
    proc = subprocess.run(['/usr/bin/time', '-f', '@@PEAKKB %M', DUCKDB, '-unsigned',
                           '-c', f'.read {script}'], capture_output=True, text=True)
    peak = None
    m = re.search(r'@@PEAKKB (\d+)', proc.stderr)
    if m:
        peak = int(m.group(1))
    err = ' '.join(l for l in proc.stderr.splitlines()
                   if 'Error' in l or 'error' in l)[:300]
    runs, cur = {}, None
    for line in proc.stdout.splitlines():
        if line.startswith('@@RUN '):
            cur = int(line.split()[1])
            runs[cur] = [0.0, '']
        elif cur is None:
            continue
        elif line.startswith('Run Time (s): real'):
            runs[cur][0] += float(line.split()[4])
        elif line.strip():
            runs[cur][1] = line.strip()
    return {k: tuple(v) for k, v in runs.items()}, peak, err


def drop_caches(data_dir):
    subprocess.run(['sync', '-f', str(data_dir)], check=True)
    r = subprocess.run('echo 3 | sudo -n tee /proc/sys/vm/drop_caches', shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.exit(f'cannot drop the page cache: {r.stderr.strip()}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=('warm', 'cold'), default='warm')
    ap.add_argument('--layouts', nargs='+', default=LAYOUTS)
    ap.add_argument('--windows', nargs='+', default=['1h', '1day', '1week', '1month'])
    ap.add_argument('--queries', nargs='+', default=list(QUERIES))
    ap.add_argument('--iter', type=int, default=5)
    ap.add_argument('--source', choices=('lake', 'iceberg', 'ducklake'), default='lake')
    ap.add_argument('--namespace', default='trips')
    ap.add_argument('--bounds', choices=('flat', 'struct'), default='flat')
    a = ap.parse_args()

    run = Path(os.environ.get('RUN') or sys.exit('RUN is required'))
    out = Path(os.environ.get('QUERY_OUT', ROOT / 'results/planar/query-runtime.csv'))
    out.parent.mkdir(parents=True, exist_ok=True)
    gen = run / 'gen'
    gen.mkdir(exist_ok=True)
    spill = ROOT / 'tmp' / f'spill.{os.getpid()}.{time.time_ns()}'
    spill.mkdir(parents=True, exist_ok=True)
    regions, windows = read_windows()

    new = not out.exists() or out.stat().st_size == 0
    with open(out, 'a', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        if new:
            w.writerow(['source', 'bounds', 'layout', 'window', 'query', 'mode', 'iter', 'seconds',
                        'answer', 'peak_kb', 'load1', 'started', 'error'])
        for layout in a.layouts:
            if a.source == 'lake' and not glob.glob(layout_glob(run, layout), recursive=True):
                sys.exit(f'{layout}: no files match {layout_glob(run, layout)}')
            source = trips_source(a.source, run, layout, a.namespace)
            for window in a.windows:
                t0, t1 = windows[window]
                for q in a.queries:
                    stmts = statements(q, regions, t0, t1, a.bounds)
                    script = gen / f'query-{a.source}-{a.bounds}-{layout}-{window}-{q}.sql'
                    started = dt.datetime.now().isoformat(timespec='seconds')
                    load1 = os.getloadavg()[0]
                    if a.mode == 'warm':
                        engine_script(script, source, spill, range(a.iter + 1), stmts)
                        runs, peak, err = run_engine(script)
                        rows = [(k, runs.get(k, (None, ''))) for k in range(1, a.iter + 1)]
                        rows = [(k, s, ans, peak, err) for k, (s, ans) in rows]
                    else:
                        engine_script(script, source, spill, [1], stmts)
                        rows = []
                        for k in range(1, a.iter + 1):
                            drop_caches(run)
                            runs, peak, err = run_engine(script)
                            s, ans = runs.get(1, (None, ''))
                            rows.append((k, s, ans, peak, err))
                    for k, s, ans, peak, err in rows:
                        w.writerow([a.source, a.bounds, layout, window, q, a.mode, k,
                                    '' if s is None else f'{s:.4f}', ans, peak,
                                    f'{load1:.2f}', started, err])
                    f.flush()
                    secs = [r[1] for r in rows if r[1] is not None]
                    print(f'{layout} {window} {q} {a.mode}: '
                          f'{f"{min(secs):.3f}" if secs else "-"} s min, answer {rows[-1][2]!r}'
                          f'{" ERROR " + rows[-1][4] if rows[-1][4] else ""}', flush=True)
    subprocess.run(['rm', '-rf', str(spill)])


if __name__ == '__main__':
    main()
