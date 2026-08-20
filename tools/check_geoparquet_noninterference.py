#!/usr/bin/env python3
# Copyright(c) MobilityDB Contributors
#
# This software is licensed under a
# Creative Commons Attribution-Share Alike 3.0 License
# https://creativecommons.org/licenses/by-sa/3.0/
"""Check that a TemporalParquet file does not interfere with GeoParquet.

TemporalParquet composes with GeoParquet rather than replacing it: a file may
carry both keys, and the `temporal` key owes the `geo` key three guarantees,
stated in `spec/conformance.md`. This checks the two of them a file can be held
to on its own.

    non-interference   a file carrying `geo` still validates as GeoParquet
    no shadowing       no column is claimed by both keys

Additivity is a property of readers rather than of a file, so it is not checked
here; the engine conformance matrix covers it.

Usage:
    check_geoparquet_noninterference.py FILE...   check the given Parquet files
    check_geoparquet_noninterference.py --selftest
        build a file carrying both keys, and a file that violates each rule,
        and confirm the check passes the first and reports the others

Exit status is 0 when every file checked satisfies the rules.
"""

import argparse
import json
import sys

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - reported, not raised
    sys.exit('check_geoparquet_noninterference: pyarrow is required')

# The GeoParquet 1.1 requirements a `geo` value has to satisfy. The full schema
# is published at https://geoparquet.org/releases/v1.1.0/schema.json; what is
# reproduced here is the part a non-interference check needs — enough to catch
# a `geo` key that writing `temporal` has damaged, without a network fetch.
GEO_REQUIRED = ('version', 'primary_column', 'columns')
GEO_COLUMN_REQUIRED = ('encoding', 'geometry_types')
GEO_ENCODINGS = ('WKB', 'point', 'linestring', 'polygon', 'multipoint',
                 'multilinestring', 'multipolygon')


def geo_errors(geo):
    """Report the ways a `geo` value departs from what GeoParquet requires."""
    out = []
    if not isinstance(geo, dict):
        return ['`geo` is not a JSON object']
    for name in GEO_REQUIRED:
        if name not in geo:
            out.append('`geo` is missing the required member `%s`' % name)
    columns = geo.get('columns')
    if not isinstance(columns, dict):
        out.append('`geo.columns` is not a JSON object')
        return out
    primary = geo.get('primary_column')
    if primary is not None and primary not in columns:
        out.append('`geo.primary_column` names `%s`, which `geo.columns` does '
                   'not describe' % primary)
    for name, column in columns.items():
        if not isinstance(column, dict):
            out.append('`geo.columns.%s` is not a JSON object' % name)
            continue
        for member in GEO_COLUMN_REQUIRED:
            if member not in column:
                out.append('`geo.columns.%s` is missing the required member '
                           '`%s`' % (name, member))
        encoding = column.get('encoding')
        if encoding is not None and encoding not in GEO_ENCODINGS:
            out.append('`geo.columns.%s.encoding` is `%s`, which GeoParquet '
                       'does not define' % (name, encoding))
    return out


def covering_columns(temporal):
    """The column names a `temporal` value's coverings point at."""
    names = set()
    for column in (temporal.get('columns') or {}).values():
        if not isinstance(column, dict):
            continue
        for bounds in (column.get('covering') or {}).values():
            if not isinstance(bounds, dict):
                continue
            for reference in bounds.values():
                if isinstance(reference, list) and reference:
                    names.add(reference[0])
    return names


def check(path):
    """Report the ways the file at @p path breaks composition."""
    meta = pq.read_metadata(path).metadata or {}
    meta = {k.decode(): v.decode() for k, v in meta.items()}
    out = []

    geo = meta.get('geo')
    temporal = meta.get('temporal')
    if geo is None:
        return out, 'carries no `geo` key, so there is nothing to interfere with'
    try:
        geo = json.loads(geo)
    except ValueError as exc:
        return ['`geo` is not valid JSON: %s' % exc], None

    out += geo_errors(geo)

    if temporal is not None:
        try:
            temporal = json.loads(temporal)
        except ValueError as exc:
            return out + ['`temporal` is not valid JSON: %s' % exc], None
        shared = set(geo.get('columns') or {}) & set(temporal.get('columns') or {})
        for name in sorted(shared):
            out.append('column `%s` is described by both `geo` and `temporal`'
                       % name)
        shared = set(geo.get('columns') or {}) & covering_columns(temporal)
        for name in sorted(shared):
            out.append('`temporal` points a covering at `%s`, which `geo` '
                       'describes as a geometry column' % name)

    return out, 'carries `geo`%s' % (' and `temporal`' if temporal else '')


GEO = {"version": "1.1.0", "primary_column": "geom",
       "columns": {"geom": {"encoding": "WKB", "geometry_types": ["Point"]}}}
TEMPORAL = {"version": "1.0.0", "primary_temporal_column": "traj",
            "columns": {"traj": {"encoding": "MEOS-WKB", "base_type": "tgeompoint",
                                 "covering": {"bbox": {
                                     "xmin": ["traj_bbox", "xmin"],
                                     "tmin": ["traj_bbox", "tmin"]}}}}}


def write(path, geo, temporal):
    table = pa.table({'geom': pa.array([b''], pa.binary()),
                      'traj': pa.array([b''], pa.binary())})
    meta = {}
    if geo is not None:
        meta['geo'] = json.dumps(geo)
    if temporal is not None:
        meta['temporal'] = json.dumps(temporal)
    table = table.replace_schema_metadata(meta)
    pq.write_table(table, path)


def selftest():
    import copy
    import tempfile
    import os

    cases = []
    good = copy.deepcopy(GEO), copy.deepcopy(TEMPORAL)
    cases.append(('a file carrying both keys', good, True))

    damaged = copy.deepcopy(GEO)
    del damaged['columns']['geom']['geometry_types']
    cases.append(('`geo` damaged by a writer', (damaged, TEMPORAL), False))

    shadowing = copy.deepcopy(TEMPORAL)
    shadowing['columns']['geom'] = shadowing['columns'].pop('traj')
    cases.append(('`temporal` shadowing a geometry column',
                  (GEO, shadowing), False))

    squatting = copy.deepcopy(TEMPORAL)
    squatting['columns']['traj']['covering']['bbox']['xmin'] = ['geom', 'xmin']
    cases.append(('a covering pointed at a geometry column',
                  (GEO, squatting), False))

    ok = True
    with tempfile.TemporaryDirectory() as d:
        for label, (geo, temporal), expected in cases:
            path = os.path.join(d, 'case.parquet')
            write(path, geo, temporal)
            errors, _ = check(path)
            passed = not errors
            mark = 'ok' if passed == expected else 'UNEXPECTED'
            print('  %-42s %-8s %s' % (label, 'clean' if passed else 'reported',
                                       mark))
            for e in errors:
                print('        %s' % e)
            ok &= passed == expected
    print('selftest: %s' % ('every case behaves as the rules require' if ok
                            else 'A CASE DID NOT BEHAVE AS THE RULES REQUIRE'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='*', metavar='FILE')
    ap.add_argument('--selftest', action='store_true',
                    help='check the checker against a file per rule')
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.files:
        ap.print_help()
        return 2

    failed = 0
    for path in args.files:
        errors, note = check(path)
        if errors:
            failed += 1
            print('%s: breaks composition with GeoParquet' % path)
            for e in errors:
                print('    %s' % e)
        else:
            print('%s: composes (%s)' % (path, note))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
