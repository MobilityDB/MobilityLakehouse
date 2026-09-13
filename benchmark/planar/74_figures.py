#!/usr/bin/env python3
"""Draw the evaluation figures of the paper from the results of a run.

PNG files in the output directory, from the tables reproduce.sh writes in the results directory:
  pruning_bytes.png    per layout, the share of its bytes a query reads in each window, the mean
                       over the four regions (layout-pruning.csv);
  speedup_heatmap.png  per query and layout, the speedup over L0, the geometric mean over the four
                       windows of L0's trimmed mean over the layout's (query-runtime-summary.csv,
                       files read directly, flat bounds);
  tradeoff.png         per layout, the geometric-mean speedup over every query and window against
                       the mean share of its bytes read over the sixteen region-window pairs, each
                       point sized by the rows the layout stores per row of L0 (storage.csv);
  lakehouse.png        per layout, the files a query's scan reads through the Iceberg catalog
                       against those it reads over the plain files, the mean over the region-window
                       pairs (catalog-pruning.csv), and the speedup of the queries through Iceberg
                       and through DuckLake over the same queries on the plain files, the geometric
                       mean over queries and windows (query-runtime-iceberg-summary.csv,
                       query-runtime-ducklake-summary.csv); drawn when those three tables are there.

  ./74_figures.py [RESULTS_DIR [OUTDIR]]

Environment: ROOT (the repository's data/), whose results/planar/ is the results directory when
none is named; OUTDIR defaults to figures/ inside the results directory.
"""
import csv
import math
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

LAYOUTS = ['L0', 'L0X', 'L0Z', 'L0H', 'L1', 'L2', 'L3', 'L4', 'L1s', 'L2s', 'L3s', 'L4s']
WINDOWS = ['1h', '1day', '1week', '1month']
WLABEL = {'1h': 'hour', '1day': 'day', '1week': 'week', '1month': 'month'}
QUERIES = [f'q{i:02d}' for i in range(1, 11)]
FAMILY = {'L0': 'baseline', 'L0X': 'in-file order', 'L0Z': 'in-file order', 'L0H': 'in-file order',
          'L1': 'daily partition', 'L2': 'daily partition', 'L3': 'daily partition',
          'L4': 'daily partition', 'L1s': 'sorted compact', 'L2s': 'sorted compact',
          'L3s': 'sorted compact', 'L4s': 'sorted compact'}
FCOLOR = {'baseline': '#52514e', 'in-file order': '#9ecae1', 'daily partition': '#2a78d6',
          'sorted compact': '#eb6834'}
WCOLOR = ['#c6dbef', '#6baed6', '#2171b5', '#08306b']
# label offsets in points where the in-file orders, L1 and L0 crowd one another
LABEL_AT = {'L0X': (-4, 8), 'L0H': (-26, 2), 'L0Z': (-22, -12), 'L1': (7, -3)}
INK, MUTED, GRID = '#0b0b0b', '#52514e', '#d8d7d2'


def geo(values):
    return math.exp(sum(math.log(v) for v in values) / len(values))


def style(ax):
    ax.grid(axis='y', color=GRID, lw=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=MUTED)


def bytes_read(path):
    """Mean share of the bytes read over the regions, per (layout, window)."""
    acc = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            window = r['window'].rsplit('_', 1)[1]
            acc[(r['layout'], window)].append(float(r['pct_bytes']))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def runtimes(path, source):
    """The warm trimmed mean per (layout, window, query) of one source, flat bounds"""
    t = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            if (r['source'], r['bounds'], r['mode']) == (source, 'flat', 'warm'):
                t[(r['layout'], r['window'], r['query'])] = float(r['trimmed_mean_s'])
    return t


def speedups(path):
    """L0's trimmed mean over each layout's, per (layout, window, query)."""
    t = runtimes(path, 'lake')
    return {(lay, w, q): t[('L0', w, q)] / s for (lay, w, q), s in t.items()}


def replication(path):
    with open(path) as f:
        return {r['layout']: float(r['replication']) for r in csv.DictReader(f)}


def catalog_files(path):
    """The mean files read per (catalog, layout) over the region-window pairs, flat bounds, and
    each layout's files"""
    acc, total = defaultdict(list), {}
    with open(path) as f:
        for r in csv.DictReader(f):
            if r['bounds'] != 'flat':
                continue
            acc[(r['catalog'], r['layout'])].append(int(r['files_read']))
            total[r['layout']] = int(r['files_total'])
    return {k: sum(v) / len(v) for k, v in acc.items()}, total


def pruning_figure(share, out):
    layouts = [lay for lay in LAYOUTS if (lay, '1h') in share]
    fig, ax = plt.subplots(figsize=(10, 3.8))
    width = 0.2
    for i, w in enumerate(WINDOWS):
        xs = [j + (i - 1.5) * width for j in range(len(layouts))]
        ax.bar(xs, [share[(lay, w)] for lay in layouts], width, color=WCOLOR[i],
               label=WLABEL[w], edgecolor='white', linewidth=0.5)
    ax.set_yscale('log')
    ax.set_ylim(0.1, 100)
    ax.set_xticks(range(len(layouts)))
    ax.set_xticklabels(layouts, fontsize=9, color=INK)
    ax.set_ylabel('bytes read (% of the layout)', fontsize=9, color=MUTED)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f'{v:g}'))
    ax.legend(title='window', fontsize=8, title_fontsize=8, frameon=False, ncol=4,
              loc='lower center', bbox_to_anchor=(0.5, 1.0))
    style(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor='white')
    plt.close(fig)


def heatmap_figure(sp, out):
    layouts = [lay for lay in LAYOUTS[1:] if (lay, '1h', 'q01') in sp]
    grid = [[geo([sp[(lay, w, q)] for w in WINDOWS]) for lay in layouts] for q in QUERIES]
    logs = [[math.log2(v) for v in row] for row in grid]
    lim = max(abs(v) for row in logs for v in row)
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    im = ax.imshow(logs, aspect='auto', cmap='RdBu', vmin=-lim, vmax=lim)
    ax.set_xticks(range(len(layouts)))
    ax.set_xticklabels(layouts, fontsize=8, color=INK)
    ax.set_yticks(range(len(QUERIES)))
    ax.set_yticklabels([f'Q{i}' for i in range(1, 11)], fontsize=8, color=INK)
    for i, row in enumerate(grid):
        for j, v in enumerate(row):
            ax.text(j, i, f'{v:.1f}', ha='center', va='center', fontsize=6.5,
                    color='white' if abs(logs[i][j]) > lim * 0.55 else INK)
    cb = fig.colorbar(im, ax=ax)
    cb.set_label('speedup over $L0$ ($\\log_2$)', fontsize=8, color=MUTED)
    cb.ax.tick_params(colors=MUTED, labelsize=7)
    ax.tick_params(colors=MUTED, length=0)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor='white')
    plt.close(fig)


def tradeoff_figure(share, sp, repl, out):
    fig, ax = plt.subplots(figsize=(7, 4.4))
    seen = set()
    for lay in LAYOUTS:
        if (lay, '1h') not in share:
            continue
        x = sum(share[(lay, w)] for w in WINDOWS) / len(WINDOWS)
        y = geo([sp[(lay, w, q)] for w in WINDOWS for q in QUERIES])
        fam = FAMILY[lay]
        ax.scatter(x, y, s=60 * repl.get(lay, 1.0), color=FCOLOR[fam], alpha=0.85,
                   edgecolor='white', linewidth=0.8, label=None if fam in seen else fam, zorder=3)
        seen.add(fam)
        ax.annotate(lay, (x, y), textcoords='offset points', xytext=LABEL_AT.get(lay, (6, 4)),
                    fontsize=8, color=INK)
    ax.axhline(1.0, color=MUTED, lw=1, ls=(0, (4, 3)))
    ax.set_xscale('log')
    ax.set_xlim(1, 150)
    ax.set_xticks([1, 2, 5, 10, 20, 50, 100])
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f'{v:g}'))
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlabel('bytes read (% of the layout, mean over regions and windows)', fontsize=9,
                  color=MUTED)
    ax.set_ylabel('speedup over $L0$ (geometric mean)', fontsize=9, color=MUTED)
    legend = ax.legend(fontsize=8, frameon=False, loc='upper right')
    for handle in legend.legend_handles:
        handle.set_sizes([40])
    ax.text(0.01, 0.02, 'point area: rows stored per row of $L0$', transform=ax.transAxes,
            fontsize=7, color=MUTED)
    style(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor='white')
    plt.close(fig)


def lakehouse_figure(files, total, lake, catalogs, out):
    """Files read and speedup of the queries through the catalogs over the plain files"""
    layouts = [lay for lay in LAYOUTS if lay in total]
    xs = list(range(len(layouts)))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    width = 0.38
    a1.bar([x - width / 2 for x in xs], [files.get(('lake', lay), total[lay]) for lay in layouts],
           width, color='#9ecae1', label='data lake (read_parquet)', edgecolor='white')
    a1.bar([x + width / 2 for x in xs], [files[('iceberg', lay)] for lay in layouts], width,
           color='#2a78d6', label='lakehouse (Iceberg)', edgecolor='white')
    a1.set_yscale('log')
    # The floor lies below one file, so a layout whose queries read one file draws a bar
    a1.set_ylim(0.5, 10000)
    a1.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f'{v:g}'))
    a1.set_ylabel('files read per query window', fontsize=9, color=MUTED)
    a2_colors = {'iceberg': '#2a78d6', 'ducklake': '#eb6834'}
    for i, (name, t) in enumerate(catalogs.items()):
        sp = [geo([lake[k] / t[k] for k in t if k[0] == lay and k in lake]) for lay in layouts]
        a2.bar([x + (i - 0.5) * width for x in xs], sp, width, color=a2_colors[name],
               label=name.replace('iceberg', 'Iceberg').replace('ducklake', 'DuckLake'),
               edgecolor='white')
    a2.axhline(1.0, color=MUTED, lw=1, ls=(0, (4, 3)))
    a2.set_ylabel('speedup over the plain files (geometric mean)', fontsize=9, color=MUTED)
    for ax in (a1, a2):
        ax.set_xticks(xs)
        ax.set_xticklabels(layouts, fontsize=8, color=INK, rotation=45)
        ax.legend(fontsize=8, frameon=False)
        style(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor='white')
    plt.close(fig)
    return {name: {lay: geo([lake[k] / t[k] for k in t if k[0] == lay and k in lake])
                   for lay in layouts} for name, t in catalogs.items()}


def main():
    root = os.environ.get('ROOT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')
    args = sys.argv[1:]
    res = args[0] if args else os.path.join(root, 'results/planar')
    outdir = args[1] if len(args) > 1 else os.path.join(res, 'figures')
    os.makedirs(outdir, exist_ok=True)
    summary = os.path.join(res, 'query-runtime-summary.csv')
    share = bytes_read(os.path.join(res, 'layout-pruning.csv'))
    sp = speedups(summary)
    repl = replication(os.path.join(res, 'storage.csv'))
    pruning_figure(share, os.path.join(outdir, 'pruning_bytes.png'))
    heatmap_figure(sp, os.path.join(outdir, 'speedup_heatmap.png'))
    tradeoff_figure(share, sp, repl, os.path.join(outdir, 'tradeoff.png'))
    for lay in LAYOUTS:
        if (lay, '1h') in share:
            print(lay, ' '.join(f'{share[(lay, w)]:.3f}' for w in WINDOWS),
                  f'{geo([sp[(lay, w, q)] for w in WINDOWS for q in QUERIES]):.2f}x')

    cat = {n: os.path.join(res, f) for n, f in (
        ('pruning', 'catalog-pruning.csv'), ('iceberg', 'query-runtime-iceberg-summary.csv'),
        ('ducklake', 'query-runtime-ducklake-summary.csv'))}
    if all(os.path.exists(p) for p in cat.values()):
        files, total = catalog_files(cat['pruning'])
        catalogs = {n: runtimes(cat[n], n) for n in ('iceberg', 'ducklake')}
        gain = lakehouse_figure(files, total, runtimes(summary, 'lake'), catalogs,
                                os.path.join(outdir, 'lakehouse.png'))
        for lay in LAYOUTS:
            if lay in total:
                print(f'{lay} files lake {files.get(("lake", lay), total[lay]):.1f} '
                      f'iceberg {files[("iceberg", lay)]:.1f}  speedup iceberg '
                      f'{gain["iceberg"][lay]:.2f}x ducklake {gain["ducklake"][lay]:.2f}x')


if __name__ == '__main__':
    main()
