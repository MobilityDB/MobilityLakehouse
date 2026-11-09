#!/usr/bin/env python3
"""Other regions and other times for the benchmark: the windows files of the diversity runs.

The benchmark's regions lie in one corridor and its windows are anchored on one morning. This
script draws, with a fixed seed, conditions that move one of the two:

  region    a rectangle of the belt's size (13,370 m by 15,743 m in EPSG:25832) placed on a cell of
            a grid of that size, drawn at random among the cells of one traffic class: `dense`, the
            densest 1% of the cells holding traffic (ports and straits), `sea`, the cells between
            the 40th and the 60th percentile, and `sparse`, those between the 5th and the 20th;
            the density of a cell is
            the number of L0 segments whose box centre lies in it. The rectangle replaces the belt
            and the Port of Goteborg, so every region query reads it; the two ferry ports of
            Query 1 stay. The windows keep the benchmark's anchor.
  anchor    the benchmark's regions with the windows anchored at an hour drawn at random over the
            month, so the anchors fall on different days of the week and hours of the day.
  season    the same over the study year, one anchor drawn at random in each of January, April,
            July and October.

Each condition is one windows file, written as 45_windows.sql writes windows_25832.csv (the
regions crossed with the windows 1h, 1day, 1week and 1month, the month the calendar month of the
anchor), and 70_queries.py reads it through QUERY_WINDOWS. conditions.csv lists them.

  RUN=<month run> ./47_diversity_windows.py draw OUTDIR [--regions 4] [--anchors 8] \
      [--year-run <year run>] [--seed 20260115]
  ./47_diversity_windows.py merge OUTDIR > diversity.csv

`merge` gathers the runs of 70_queries.py written as OUTDIR/runs/<condition>.csv into one table,
each window named <condition>/<window>, which 71_summarize.py --speedups and --wilcoxon read as
conditions of their own; the class of each condition is the part of its name before the first `-`.

Environment: RUN (the month run, for draw); DUCKDB_ENGINE.
"""
import argparse
import csv
import datetime as dt
import os
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DUCKDB = str(HERE / 'duckdb.sh')
BENCH = HERE / 'windows_25832.csv'
WIDTH, HEIGHT = 13370.0, 15743.0
CLASSES = {'dense': (99.0, 100.0), 'sea': (40.0, 60.0), 'sparse': (5.0, 20.0)}
SPANS = {'1h': dt.timedelta(hours=1), '1day': dt.timedelta(days=1), '1week': dt.timedelta(days=7)}


def bench_regions():
    """{region: (x0, y0, x1, y1)} and the benchmark's anchor, from windows_25832.csv as
    #read_windows of 70_queries.py reads it"""
    regions, anchor = {}, None
    with open(BENCH) as f:
        for name, x0, y0, x1, y1, t0, t1 in csv.reader(f):
            region, window = name.rsplit('_', 1)
            regions[region] = tuple(float(v) for v in (x0, y0, x1, y1))
            if window == '1h':
                anchor = dt.datetime.fromisoformat(t0)
    return regions, anchor


def density(run):
    """[(count, cx, cy)] over the grid cells holding at least one L0 segment's box centre"""
    sql = (f"SELECT count(*), floor((trip_xmin + trip_xmax) / 2 / {WIDTH})::BIGINT, "
           f"floor((trip_ymin + trip_ymax) / 2 / {HEIGHT})::BIGINT "
           f"FROM read_parquet('{run}/L0/year=*/month=*/day-*.parquet') GROUP BY ALL;\n")
    out = subprocess.run([DUCKDB, '-unsigned', '-noheader', '-csv'], input=sql,
                         capture_output=True, text=True, check=True).stdout
    return [tuple(int(v) for v in line.split(',')) for line in out.splitlines() if line]


def percentile(sorted_counts, p):
    k = (len(sorted_counts) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(sorted_counts) - 1)
    return sorted_counts[lo] + (sorted_counts[hi] - sorted_counts[lo]) * (k - lo)


def month_of(t):
    """The calendar month holding t, from its first midnight to the next month's, as the 1month
    window #read_windows of 70_queries.py reads from windows_25832.csv spans January"""
    t0 = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return t0, (t0 + dt.timedelta(days=32)).replace(day=1)


def windows(anchor):
    out = {w: (anchor, anchor + span) for w, span in SPANS.items()}
    out['1month'] = month_of(anchor)
    return out


def write(path, regions, wins):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        for r in sorted(regions):
            for name in sorted(wins):
                t0, t1 = wins[name]
                w.writerow([f'{r}_{name}', *(f'{v:.1f}' for v in regions[r]),
                            t0.strftime('%Y-%m-%d %H:%M:%S'), t1.strftime('%Y-%m-%d %H:%M:%S')])


def random_anchor(rng, start, end):
    """An hour drawn uniformly in [start, end), whole hours"""
    hours = int((end - start).total_seconds() // 3600)
    return start + dt.timedelta(hours=rng.randrange(hours))


def draw(a):
    run = Path(a.run)
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    regions, anchor = bench_regions()
    cells = density(run)
    counts = sorted(c for c, _, _ in cells)
    rows = []
    for cls, (plo, phi) in CLASSES.items():
        lo, hi = percentile(counts, plo), percentile(counts, phi)
        pool = sorted((cx, cy, c) for c, cx, cy in cells if lo <= c <= hi)
        for k, (cx, cy, c) in enumerate(rng.sample(pool, min(a.regions, len(pool))), 1):
            rect = (cx * WIDTH, cy * HEIGHT, (cx + 1) * WIDTH, (cy + 1) * HEIGHT)
            name = f'{cls}-{k}'
            regs = dict(regions, belt=rect, goteborg=rect)
            write(out / f'{name}.csv', regs, windows(anchor))
            rows.append([name, cls, str(run), *(f'{v:.1f}' for v in rect), c, anchor.isoformat()])
    t0, _ = month_of(anchor)
    # the anchors leave the week window inside the month
    last = month_of(anchor)[1] - SPANS['1week']
    for k in range(1, a.anchors + 1):
        t = random_anchor(rng, t0, last)
        write(out / f'anchor-{k}.csv', regions, windows(t))
        rows.append([f'anchor-{k}', 'anchor', str(run), *(f'{v:.1f}' for v in regions['belt']),
                     '', t.isoformat()])
    if a.year_run:
        for month in (1, 4, 7, 10):
            start = dt.datetime(anchor.year, month, 1)
            t = random_anchor(rng, start, month_of(start)[1] - SPANS['1week'])
            name = f'season-{start:%m}'
            write(out / f'{name}.csv', regions, windows(t))
            rows.append([name, 'season', a.year_run, *(f'{v:.1f}' for v in regions['belt']),
                         '', t.isoformat()])
    with open(out / 'conditions.csv', 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['condition', 'class', 'run', 'xmin', 'ymin', 'xmax', 'ymax', 'segments',
                    'anchor'])
        w.writerows(rows)
    for r in rows:
        print(' '.join(str(v) for v in r))


def merge(a):
    out = Path(a.outdir)
    w = csv.writer(sys.stdout, lineterminator='\n')
    header = None
    for path in sorted((out / 'runs').glob('*.csv')):
        cond = path.stem
        with open(path) as f:
            rd = csv.DictReader(f)
            if header is None:
                header = rd.fieldnames
                w.writerow(header)
            for r in rd:
                r['window'] = f"{cond}/{r['window']}"
                w.writerow([r[k] for k in header])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('draw')
    d.add_argument('outdir')
    d.add_argument('--regions', type=int, default=4)
    d.add_argument('--anchors', type=int, default=8)
    d.add_argument('--year-run')
    d.add_argument('--seed', type=int, default=20260115)
    m = sub.add_parser('merge')
    m.add_argument('outdir')
    a = ap.parse_args()
    if a.cmd == 'draw':
        a.run = os.environ.get('RUN') or sys.exit('RUN is required')
        draw(a)
    else:
        merge(a)


if __name__ == '__main__':
    main()
