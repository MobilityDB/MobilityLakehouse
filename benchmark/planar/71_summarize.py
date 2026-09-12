#!/usr/bin/env python3
"""Summarize the timed runs of 70_queries.py the way the paper reports them.

Per (source, bounds, layout, window, query, mode): the trimmed mean of the runs, dropping the
fastest and the slowest, its 95% confidence interval from the Student-t critical value at the
number of runs kept (5 runs keep 3, t(0.975, 2) = 4.303), the answers the runs gave (a group whose
runs disagree is flagged, and no time from it is meaningful), the peak memory and the load at the
start. Every layout's answer is also compared with L0's for the same (source, bounds, window,
query, mode).

  ./71_summarize.py [results/planar/query-runtime.csv] > results/planar/query-runtime-summary.csv

Environment: ROOT (the repository's data/), whose results/planar/query-runtime.csv is read when
no file is named.
"""
import csv
import math
import os
import statistics
import sys
from collections import defaultdict

# two-sided 95% Student-t critical values by degrees of freedom
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
        9: 2.262, 10: 2.228}


def main():
    root = os.environ.get('ROOT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, 'results/planar/query-runtime.csv')
    groups = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            key = (r['source'], r.get('bounds', ''), r['layout'], r['window'], r['query'], r['mode'])
            groups[key].append(r)

    truth = {}
    for (source, bounds, layout, window, query, mode), rows in groups.items():
        if layout == 'L0':
            truth[(source, bounds, window, query, mode)] = {r['answer'] for r in rows}

    w = csv.writer(sys.stdout, lineterminator='\n')
    w.writerow(['source', 'bounds', 'layout', 'window', 'query', 'mode', 'n', 'trimmed_mean_s',
                'ci95_s', 'min_s', 'max_s', 'answer', 'answers_agree', 'matches_L0', 'peak_kb',
                'load1', 'error'])
    for key in sorted(groups):
        rows = groups[key]
        secs = sorted(float(r['seconds']) for r in rows if r['seconds'])
        answers = {r['answer'] for r in rows}
        errors = sorted({r['error'] for r in rows if r['error']})
        trimmed = secs[1:-1] if len(secs) >= 3 else secs
        mean = statistics.fmean(trimmed) if trimmed else float('nan')
        ci = float('nan')
        if len(trimmed) >= 2:
            ci = T975.get(len(trimmed) - 1, 1.96) * statistics.stdev(trimmed) / math.sqrt(len(trimmed))
        source, bounds, layout, window, query, mode = key
        ref = truth.get((source, bounds, window, query, mode))
        match = '' if ref is None else str(answers == ref)
        peak = max((int(r['peak_kb']) for r in rows if r['peak_kb']), default='')
        w.writerow([source, bounds, layout, window, query, mode, len(secs), f'{mean:.4f}',
                    f'{ci:.4f}', f'{secs[0]:.4f}' if secs else '',
                    f'{secs[-1]:.4f}' if secs else '', ';'.join(sorted(answers)),
                    str(len(answers) == 1), match, peak, rows[0]['load1'],
                    ' | '.join(errors)[:200]])


if __name__ == '__main__':
    main()
