#!/usr/bin/env python3
"""Register the layouts of a run as Iceberg tables in the REST catalog over MinIO.

Each layout's Parquet files are copied to the object store under s3://warehouse/<namespace>/, at
their path relative to the run (L0/year=2026/month=01/day-...parquet, layouts_daily/L2/...), unless
the object already there has the file's size and was written after the file last changed, so a
file rewritten since its upload is sent again; then one table per layout, <namespace>.<layout>, is
created from the files' schema and the files are added to it. Adding files reads each file's
footer into the table's manifests, per-file bounds included, and never rewrites a file: the bytes
the catalog serves are the bytes the pipeline wrote.

The files carry their covering bounds twice, as the structs trip_bbox and trip_tspan and as the
top-level copies trip_xmin, ..., trip_tmax. pyiceberg records counts but no bounds for a field
inside a struct (apache/iceberg-python issue #2699), so the catalog prunes files on the top-level
copies and on dt.

  RUN=<run> python3 planar/80_register.py [--namespace trips] L0 L0X ...

The services come from planar/iceberg/docker-compose.yml; the packages from planar/requirements.txt.
"""
import argparse
import os
import sys
from pathlib import Path

import pyarrow.parquet as pq
import s3fs
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError

ENDPOINT = 'http://localhost:9000'
KEY, SECRET = 'admin', 'password'
BUCKET = 'warehouse'


def layout_files(run, layout):
    """The layout's files, as 70_queries.py reads them"""
    if layout == 'L0':
        return sorted(run.glob('L0/year=*/month=*/day-*.parquet'))
    if layout in ('L0X', 'L0Z', 'L0H'):
        return sorted(run.glob(f'layouts_daily/{layout}/day-*.parquet'))
    if layout in ('L1', 'L2', 'L3', 'L4'):
        return sorted(run.glob(f'layouts_daily/{layout}/day-*/**/*.parquet'))
    if layout in ('L1s', 'L2s', 'L3s', 'L4s'):
        return sorted(run.glob(f'layout_compact/{layout}/**/*.parquet'))
    sys.exit(f'unknown layout {layout}')


def layout_prefix(layout):
    """The object-key prefix under the namespace holding the layout's files, ending in '/' so that
    L1 never matches L1s"""
    if layout == 'L0':
        return 'L0/'
    if layout in ('L1s', 'L2s', 'L3s', 'L4s'):
        return f'layout_compact/{layout}/'
    return f'layouts_daily/{layout}/'


def uploaded(fs, key, f):
    """Whether the object at key holds the file as it now stands: the same size, written after the
    file last changed"""
    try:
        info = fs.info(key)
    except FileNotFoundError:
        return False
    st = f.stat()
    return info['size'] == st.st_size and info['LastModified'].timestamp() >= st.st_mtime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--namespace', default='trips')
    ap.add_argument('layouts', nargs='+')
    a = ap.parse_args()
    run = Path(os.environ.get('RUN') or sys.exit('RUN is required'))

    fs = s3fs.S3FileSystem(key=KEY, secret=SECRET, client_kwargs={'endpoint_url': ENDPOINT})
    if not fs.exists(BUCKET):
        fs.mkdir(BUCKET)
    catalog = load_catalog('rest', **{
        'uri': 'http://localhost:8181', 's3.endpoint': ENDPOINT, 's3.access-key-id': KEY,
        's3.secret-access-key': SECRET, 's3.region': 'us-east-1'})
    try:
        catalog.create_namespace(a.namespace)
    except NamespaceAlreadyExistsError:
        pass

    for layout in a.layouts:
        files = layout_files(run, layout)
        if not files:
            sys.exit(f'{layout}: no files under {run}')
        uris, copied = [], 0
        for f in files:
            key = f'{BUCKET}/{a.namespace}/{f.relative_to(run)}'
            if not uploaded(fs, key, f):
                fs.put_file(str(f), key)
                copied += 1
            uris.append(f's3://{key}')
        ident = (a.namespace, layout.lower())
        try:
            catalog.drop_table(ident)
        except NoSuchTableError:
            pass
        table = catalog.create_table(ident, schema=pq.read_schema(files[0]))
        table.add_files(file_paths=uris)
        n = sum(1 for _ in table.inspect.files().to_pylist())
        # An object under the layout's prefix that the layout no longer holds (a partition a
        # rebuild names differently, such as an L4 bin that moved) is referenced by no table; it
        # is removed, so the store holds exactly the layout's files
        keys = {u[len('s3://'):] for u in uris}
        prefix = f'{BUCKET}/{a.namespace}/{layout_prefix(layout)}'
        stale = [k for k in fs.find(prefix) if k not in keys]
        if stale:
            fs.rm(stale)
        print(f'{a.namespace}.{layout.lower()}: {len(files)} files ({copied} copied, '
              f'{len(stale)} stale removed), {n} in the table', flush=True)


if __name__ == '__main__':
    main()
