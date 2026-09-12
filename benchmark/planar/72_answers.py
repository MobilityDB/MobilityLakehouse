#!/usr/bin/env python3
"""The benchmark's answers and each layout's recall, from the runs of 70_queries.py.

  answers   the L0 answer of each query at each window (tab:eval-answers), read from one source
            and covering form, after checking every run of a group gives the same answer;
  recall    for the queries whose answer counts entities that L0 maximizes (q01 vessels at both
            ports, q02 vessels in the port, q03 vessels in the belt, q07 vessels at an instant,
            q09 and q10 vessel pairs) and for the vessel count inside q04's fleet summary: a
            layout's count over L0's, per window, with the mean over the counting queries;
  agreement every (source, bounds, layout) answer beside L0's for the same query and window, so a
            source or layout that disagrees on any query, counting or not, is listed.

  planar/72_answers.py results/planar/query-runtime.csv [--source lake] [--bounds flat] \
      [--answers answers.csv] [--recall recall.csv]
"""
import argparse
import csv
import sys
from collections import defaultdict

COUNTS = ['q01', 'q02', 'q03', 'q07', 'q09', 'q10']


def value(query, answer):
    """The count a query's answer states, q04's being its vessel count"""
    if query == 'q04':
        return float(answer.split('|')[1])
    return float(answer)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs')
    ap.add_argument('--source', default='lake')
    ap.add_argument('--bounds', default='flat')
    ap.add_argument('--answers', default='/dev/stdout')
    ap.add_argument('--recall', default='/dev/stdout')
    a = ap.parse_args()

    groups = defaultdict(set)
    with open(a.runs) as f:
        for r in csv.DictReader(f):
            if r['error'].strip():
                continue
            groups[(r['source'], r.get('bounds', ''), r['layout'], r['window'], r['query'])].add(r['answer'])
    split = [k for k, v in groups.items() if len(v) != 1]
    if split:
        sys.exit(f'runs of one group disagree: {split[:5]}')
    ans = {k: next(iter(v)) for k, v in groups.items()}

    # every answer against L0's for the same source, bounds, query and window
    disagree = [(k, v, ans.get((k[0], k[1], 'L0', k[3], k[4])))
                for k, v in ans.items() if k[2] != 'L0'
                and v != ans.get((k[0], k[1], 'L0', k[3], k[4]))]
    print(f'agreement: {len(ans) - len(disagree)} of {len(ans)} (source, bounds, layout, window, '
          f'query) answers equal L0\'s', file=sys.stderr)
    for k, v, ref in disagree:
        print(f'  {k}: {v} vs L0 {ref}', file=sys.stderr)

    windows = sorted({k[3] for k in ans if k[0] == a.source and k[1] == a.bounds})
    queries = sorted({k[4] for k in ans})
    with open(a.answers, 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['query'] + windows)
        for q in queries:
            w.writerow([q] + [ans.get((a.source, a.bounds, 'L0', win, q), '') for win in windows])

    layouts = sorted({k[2] for k in ans if k[0] == a.source and k[1] == a.bounds},
                     key=lambda l: (len(l), l))
    with open(a.recall, 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['layout', 'window'] + COUNTS + ['mean', 'q04_vessels'])
        for layout in layouts:
            for win in windows:
                row, vals = [layout, win], []
                for q in COUNTS:
                    ref = ans.get((a.source, a.bounds, 'L0', win, q))
                    got = ans.get((a.source, a.bounds, layout, win, q))
                    if ref is None or got is None or value(q, ref) == 0:
                        row.append('')
                        continue
                    vals.append(value(q, got) / value(q, ref))
                    row.append(f'{vals[-1]:.4f}')
                row.append(f'{sum(vals) / len(vals):.4f}' if vals else '')
                ref = ans.get((a.source, a.bounds, 'L0', win, 'q04'))
                got = ans.get((a.source, a.bounds, layout, win, 'q04'))
                row.append(f'{value("q04", got) / value("q04", ref):.4f}'
                           if ref and got and value('q04', ref) else '')
                w.writerow(row)


if __name__ == '__main__':
    main()
