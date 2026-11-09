#!/usr/bin/env python3
"""Summarize the timed runs of 70_queries.py the way the paper reports them.

Every run is kept. Per (source, bounds, layout, window, query, mode): the number of runs, their
median and mean, the standard deviation and the half-width of the 95% confidence interval of the
mean from the Student-t distribution with n - 1 degrees of freedom, the fastest and the slowest
run, the answers the runs gave (a group whose runs disagree is flagged, and no time from it is
meaningful), the peak memory and the load at the start. Every layout's answer is also compared with
L0's for the same (source, bounds, window, query, mode).

  ./71_summarize.py [RUNS] > results/planar/query-runtime-summary.csv
  ./71_summarize.py RUNS --speedups [--ref L0] [--against OTHER_RUNS] > speedups.csv
  ./71_summarize.py RUNS --wilcoxon A B [--windows 1day 1week ...]

--speedups writes the speedup of every layout over the reference, the ratio of the reference's mean
to the layout's, with a 95% percentile bootstrap interval that resamples the runs of both
(10,000 resamples, a fixed seed): one row per (window, query) (scope `cell`), one per query as the
geometric mean over the windows (`query`), one per window over the queries (`window`), and one per
layout over every query and window (`all`). The point estimates are those #speedups and #geo of
74_figures.py draw; the intervals are new here, no other script of the tree resamples. With
--against, the reference is the same layout, window and query in OTHER_RUNS, the runs of another
source or mode (the catalogs over the plain files, cold over warm), and --ref is ignored.

--wilcoxon pairs the means of two layouts over the (window, query) conditions they share and
prints the Wilcoxon signed-rank statistic and its two-sided p-value, exact up to 25 untied pairs
and from the normal approximation with the tie correction beyond.

The grouping key is that of #main of 72_answers.py.

Environment: ROOT (the repository's data/), whose results/planar/query-runtime.csv is read when
no file is named.
"""
import argparse
import csv
import math
import os
import statistics
import sys
from collections import defaultdict

import numpy as np

# two-sided 95% Student-t critical values by degrees of freedom
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
        9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120,
        17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 25: 2.060, 30: 2.042}
RESAMPLES = 10000
SEED = 20260101
WINDOWS = ['1h', '1day', '1week', '1month']


def t975(df):
    """The critical value at df: the tabled one, the nearest tabled one below it up to 30, 1.96
    past 30"""
    if df in T975:
        return T975[df]
    return T975[max(k for k in T975 if k <= df)] if df <= 30 else 1.96


def read_runs(path):
    """{(source, bounds, layout, window, query, mode): [rows]}"""
    groups = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            key = (r['source'], r.get('bounds', ''), r['layout'], r['window'], r['query'], r['mode'])
            groups[key].append(r)
    return groups


def seconds(rows):
    return [float(r['seconds']) for r in rows if r['seconds']]


def summary(groups):
    truth = {}
    for (source, bounds, layout, window, query, mode), rows in groups.items():
        if layout == 'L0':
            truth[(source, bounds, window, query, mode)] = {r['answer'] for r in rows}
    w = csv.writer(sys.stdout, lineterminator='\n')
    w.writerow(['source', 'bounds', 'layout', 'window', 'query', 'mode', 'n', 'median_s', 'mean_s',
                'sd_s', 'ci95_s', 'min_s', 'max_s', 'answer', 'answers_agree', 'matches_L0',
                'peak_kb', 'load1', 'error'])
    for key in sorted(groups):
        rows = groups[key]
        secs = sorted(seconds(rows))
        answers = {r['answer'] for r in rows}
        errors = sorted({r['error'] for r in rows if r['error']})
        n = len(secs)
        mean = statistics.fmean(secs) if secs else float('nan')
        median = statistics.median(secs) if secs else float('nan')
        sd = statistics.stdev(secs) if n >= 2 else float('nan')
        ci = t975(n - 1) * sd / math.sqrt(n) if n >= 2 else float('nan')
        source, bounds, layout, window, query, mode = key
        ref = truth.get((source, bounds, window, query, mode))
        match = '' if ref is None else str(answers == ref)
        peak = max((int(r['peak_kb']) for r in rows if r['peak_kb']), default='')
        w.writerow([source, bounds, layout, window, query, mode, n, f'{median:.4f}',
                    f'{mean:.4f}', f'{sd:.4f}', f'{ci:.4f}',
                    f'{secs[0]:.4f}' if secs else '', f'{secs[-1]:.4f}' if secs else '',
                    ';'.join(sorted(answers)), str(len(answers) == 1), match, peak,
                    rows[0]['load1'], ' | '.join(errors)[:200]])


def cells(groups):
    """{(source, bounds, mode, layout): {(window, query): the runs' seconds as an array}}"""
    out = defaultdict(dict)
    for (source, bounds, layout, window, query, mode), rows in groups.items():
        secs = seconds(rows)
        if secs:
            out[(source, bounds, mode, layout)][(window, query)] = np.array(secs)
    return out


def boot_means(runs, rng):
    """RESAMPLES bootstrap means of one cell's runs"""
    idx = rng.integers(0, len(runs), size=(RESAMPLES, len(runs)))
    return runs[idx].mean(axis=1)


def condition_order(c):
    return (WINDOWS.index(c[0]) if c[0] in WINDOWS else len(WINDOWS), c[1])


def speedups(groups, ref, against):
    rng = np.random.default_rng(SEED)
    lay = cells(groups)
    base = cells(against) if against is not None else None
    w = csv.writer(sys.stdout, lineterminator='\n')
    w.writerow(['source', 'bounds', 'mode', 'layout', 'scope', 'window', 'query', 'conditions',
                'speedup', 'ci_low', 'ci_high'])
    for key in sorted(lay):
        source, bounds, mode, layout = key
        if base is None:
            if layout == ref:
                continue
            refcells = lay.get((source, bounds, mode, ref))
        else:
            refcells = next((v for (s, b, m, l), v in sorted(base.items())
                             if l == layout and b == bounds), None)
        if refcells is None:
            continue
        conds = sorted(set(lay[key]) & set(refcells), key=condition_order)
        if not conds:
            continue
        point, boot = {}, {}
        for c in conds:
            r, x = refcells[c], lay[key][c]
            point[c] = r.mean() / x.mean()
            boot[c] = boot_means(r, rng) / boot_means(x, rng)

        def row(scope, window, query, sel):
            est = math.exp(np.log(np.array([point[c] for c in sel])).mean())
            blogs = np.log(np.stack([boot[c] for c in sel])).mean(axis=0)
            lo, hi = np.exp(np.percentile(blogs, [2.5, 97.5]))
            w.writerow([source, bounds, mode, layout, scope, window, query, len(sel),
                        f'{est:.4f}', f'{lo:.4f}', f'{hi:.4f}'])

        for c in conds:
            row('cell', c[0], c[1], [c])
        for q in sorted({c[1] for c in conds}):
            row('query', '', q, [c for c in conds if c[1] == q])
        for win in [x for x in WINDOWS if any(c[0] == x for c in conds)]:
            row('window', win, '', [c for c in conds if c[0] == win])
        row('all', '', '', conds)


def signed_rank_cdf(n, w):
    """P(W+ <= w) under the null hypothesis for n untied, nonzero differences"""
    counts = [1] + [0] * (n * (n + 1) // 2)
    for k in range(1, n + 1):
        for s in range(len(counts) - 1, k - 1, -1):
            counts[s] += counts[s - k]
    return sum(counts[:w + 1]) / 2 ** n


def wilcoxon(groups, a, b, windows):
    means = {a: defaultdict(dict), b: defaultdict(dict)}
    for (source, bounds, layout, window, query, mode), rows in groups.items():
        secs = seconds(rows)
        if layout in means and secs and (not windows or window in windows):
            means[layout][(source, bounds, mode)][(window, query)] = statistics.fmean(secs)
    for key in sorted(set(means[a]) & set(means[b])):
        conds = sorted(set(means[a][key]) & set(means[b][key]), key=condition_order)
        d = [means[a][key][c] - means[b][key][c] for c in conds]
        d = [x for x in d if x != 0]
        n = len(d)
        order = sorted(range(n), key=lambda i: abs(d[i]))
        ranks, ties, i = [0.0] * n, [], 0
        while i < n:
            j = i
            while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
                j += 1
            for k in range(i, j + 1):
                ranks[order[k]] = (i + j) / 2 + 1
            ties.append(j - i + 1)
            i = j + 1
        wplus = sum(r for r, x in zip(ranks, d) if x > 0)
        wminus = sum(r for r, x in zip(ranks, d) if x < 0)
        stat = min(wplus, wminus)
        if n <= 25 and all(t == 1 for t in ties):
            p, how = min(1.0, 2 * signed_rank_cdf(n, int(stat))), 'exact'
        else:
            mu = n * (n + 1) / 4
            var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties) / 48
            z = (stat - mu) / math.sqrt(var) if var > 0 else 0.0
            p, how = min(1.0, math.erfc(abs(z) / math.sqrt(2))), 'normal'
        faster = sum(1 for x in d if x > 0)
        print(f'{key[0]} {key[1]} {key[2]}: {a} vs {b} over {len(conds)} conditions, '
              f'{b} faster in {faster}, W+ = {wplus:g}, W- = {wminus:g}, p = {p:.3g} ({how})')


def main():
    root = os.environ.get('ROOT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='?', default=os.path.join(root, 'results/planar/query-runtime.csv'))
    ap.add_argument('--speedups', action='store_true')
    ap.add_argument('--ref', default='L0')
    ap.add_argument('--against')
    ap.add_argument('--wilcoxon', nargs=2, metavar=('A', 'B'))
    ap.add_argument('--windows', nargs='+')
    a = ap.parse_args()
    groups = read_runs(a.runs)
    if a.speedups:
        speedups(groups, a.ref, read_runs(a.against) if a.against else None)
    elif a.wilcoxon:
        wilcoxon(groups, a.wilcoxon[0], a.wilcoxon[1], a.windows)
    else:
        summary(groups)


if __name__ == '__main__':
    main()
