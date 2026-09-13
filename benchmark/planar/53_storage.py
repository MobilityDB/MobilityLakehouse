#!/usr/bin/env python3
"""The storage cost of each layout of a run: files, rows, bytes and replication against L0.

Rows are read from the Parquet footers (the sum of each file's row-group row counts), bytes are
the files' sizes on disk, and replication is a layout's rows over L0's: a layout that never cuts a
trajectory reads 1.00, and a tiling layout reads how many pieces it stores per L0 segment.

  RUN=<run> python3 planar/53_storage.py > storage.csv

Environment: RUN (required); DUCKDB_ENGINE.
"""
import csv
import os
import subprocess
import sys
from pathlib import Path

# The engine is DUCKDB_ENGINE when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
DUCKDB = str(Path(__file__).with_name('duckdb.sh'))
LAYOUTS = ['L0', 'L0X', 'L0Z', 'L0H', 'L1', 'L2', 'L3', 'L4', 'L1s', 'L2s', 'L3s', 'L4s']


def layout_files(run, layout):
    """The layout's files, as 70_queries.py reads them"""
    if layout == 'L0':
        return sorted(run.glob('L0/year=*/month=*/day-*.parquet'))
    if layout in ('L0X', 'L0Z', 'L0H'):
        return sorted(run.glob(f'layouts_daily/{layout}/day-*.parquet'))
    if layout in ('L1', 'L2', 'L3', 'L4'):
        return sorted(run.glob(f'layouts_daily/{layout}/day-*/**/*.parquet'))
    return sorted(run.glob(f'layout_compact/{layout}/**/*.parquet'))


def rows(files):
    """The rows the files' footers declare. The statement travels on standard input: a month's
    layout lists thousands of files, past the length the kernel allows a command line"""
    listing = ', '.join(f"'{f}'" for f in files)
    out = subprocess.run([DUCKDB, '-unsigned', '-noheader', '-list'],
                         input=f"SELECT sum(n) FROM (SELECT file_name, row_group_id, "
                               f"any_value(row_group_num_rows) AS n "
                               f"FROM parquet_metadata([{listing}]) GROUP BY ALL);\n",
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip().splitlines()[-1])


def main():
    run = Path(os.environ.get('RUN') or sys.exit('RUN is required'))
    stats = {}
    for layout in LAYOUTS:
        files = layout_files(run, layout)
        if files:
            stats[layout] = (len(files), rows(files), sum(f.stat().st_size for f in files))
    if 'L0' not in stats:
        sys.exit(f'no L0 under {run}')
    base = stats['L0'][1]
    w = csv.writer(sys.stdout, lineterminator='\n')
    w.writerow(['layout', 'files', 'rows', 'replication', 'bytes', 'gb'])
    for layout, (nfiles, nrows, nbytes) in stats.items():
        w.writerow([layout, nfiles, nrows, f'{nrows / base:.2f}', nbytes, f'{nbytes / 1e9:.2f}'])


if __name__ == '__main__':
    main()
