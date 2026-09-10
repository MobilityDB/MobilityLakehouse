#!/usr/bin/env python3
# Copyright(c) MobilityDB Contributors
#
# This software is licensed under a
# Creative Commons Attribution-Share Alike 3.0 License
# https://creativecommons.org/licenses/by-sa/3.0/
"""Check that a TemporalParquet file composes with GeoParquet.

TemporalParquet composes with GeoParquet rather than replacing it: a file may
carry both keys, and the `temporal` key owes the `geo` key three guarantees,
stated in `spec/conformance.md`. This checks the rules a file can be held to on
its own.

    non-interference   a file carrying `geo` still validates as GeoParquet,
                       under the rules of the version it declares
    no shadowing       no column is claimed by both keys
    bbox covering      every `bbox` covering `temporal` declares names a
                       GeoParquet bounding box column

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

# The GeoParquet requirements a `geo` value has to satisfy, per major version.
# The full schemas are published at
# https://geoparquet.org/releases/v1.1.0/schema.json and in the v2.0.0-rc.1
# release; what is reproduced here is the part a non-interference check needs —
# enough to catch a `geo` key that writing `temporal` has damaged, without a
# network fetch.
GEO_REQUIRED = ('version', 'primary_column', 'columns')
GEO_COLUMN_REQUIRED = ('encoding', 'geometry_types')
GEO_ENCODINGS = {
    '1': ('WKB', 'point', 'linestring', 'polygon', 'multipoint',
          'multilinestring', 'multipolygon'),
    '2': ('WKB',),
}

# The field names of a GeoParquet bounding box column, in their required order.
BBOX_FIELDS = (('xmin', 'ymin', 'xmax', 'ymax'),
               ('xmin', 'ymin', 'zmin', 'xmax', 'ymax', 'zmax'))


def geo_errors(geo):
    """Report the ways a `geo` value departs from what GeoParquet requires."""
    out = []
    if not isinstance(geo, dict):
        return ['`geo` is not a JSON object']
    for name in GEO_REQUIRED:
        if name not in geo:
            out.append('`geo` is missing the required member `%s`' % name)
    version = geo.get('version')
    major = version.split('.')[0] if isinstance(version, str) else None
    encodings = GEO_ENCODINGS.get(major)
    if version is not None and encodings is None:
        out.append('`geo.version` is `%s`, a GeoParquet version this check '
                   'does not know' % version)
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
        if encoding is not None and encodings is not None \
                and encoding not in encodings:
            out.append('`geo.columns.%s.encoding` is `%s`, which GeoParquet %s '
                       'does not define' % (name, encoding, version))
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


def bbox_errors(temporal, schema):
    """Report every `bbox` covering that names no GeoParquet bounding box
    column."""
    out = []
    for name, column in (temporal.get('columns') or {}).items():
        if not isinstance(column, dict):
            continue
        bbox = (column.get('covering') or {}).get('bbox')
        if bbox is None:
            continue
        where = '`temporal.columns.%s.covering.bbox`' % name
        if not isinstance(bbox, dict):
            out.append('%s is not a JSON object' % where)
            continue
        targets = {ref[0] for ref in bbox.values()
                   if isinstance(ref, list) and ref}
        if len(targets) != 1:
            out.append('%s points at %d columns, where GeoParquet requires one'
                       % (where, len(targets)))
            continue
        target = targets.pop()
        for key, ref in sorted(bbox.items()):
            if ref != [target, key]:
                out.append('%s maps `%s` to %s, not to ["%s", "%s"]'
                           % (where, key, json.dumps(ref), target, key))
        index = schema.get_field_index(target)
        if index < 0:
            out.append('%s names `%s`, which is not a column at the root of '
                       'the schema' % (where, target))
            continue
        kind = schema.field(index).type
        if not pa.types.is_struct(kind):
            out.append('%s names `%s`, which is not a struct column'
                       % (where, target))
            continue
        fields = tuple(kind.field(i).name for i in range(kind.num_fields))
        if fields not in BBOX_FIELDS:
            out.append('`%s` has the fields (%s), where a GeoParquet bounding '
                       'box column has (%s) or (%s)'
                       % (target, ', '.join(fields), ', '.join(BBOX_FIELDS[0]),
                          ', '.join(BBOX_FIELDS[1])))
        elif set(bbox) != set(fields):
            out.append('%s declares the bounds (%s) of a column holding (%s)'
                       % (where, ', '.join(sorted(bbox)), ', '.join(fields)))
        types = {kind.field(i).type for i in range(kind.num_fields)}
        if len(types) != 1 or not types <= {pa.float32(), pa.float64()}:
            out.append('`%s` holds fields of type (%s), where a GeoParquet '
                       'bounding box column holds one of FLOAT or DOUBLE'
                       % (target, ', '.join(sorted(str(t) for t in types))))
    return out


def check(path):
    """Report the ways the file at @p path breaks composition."""
    meta = pq.read_metadata(path).metadata or {}
    meta = {k.decode(): v.decode() for k, v in meta.items()}
    out = []

    geo = meta.get('geo')
    temporal = meta.get('temporal')
    if temporal is not None:
        try:
            temporal = json.loads(temporal)
        except ValueError as exc:
            return ['`temporal` is not valid JSON: %s' % exc], None
        out += bbox_errors(temporal, pq.read_schema(path))
    if geo is None:
        return out, ('carries `temporal` and no `geo`' if temporal
                     else 'carries neither `geo` nor `temporal`')
    try:
        geo = json.loads(geo)
    except ValueError as exc:
        return out + ['`geo` is not valid JSON: %s' % exc], None

    out += geo_errors(geo)

    if temporal is not None:
        shared = set(geo.get('columns') or {}) & set(temporal.get('columns') or {})
        for name in sorted(shared):
            out.append('column `%s` is described by both `geo` and `temporal`'
                       % name)
        shared = set(geo.get('columns') or {}) & covering_columns(temporal)
        for name in sorted(shared):
            out.append('`temporal` points a covering at `%s`, which `geo` '
                       'describes as a geometry column' % name)

    return out, 'carries `geo`%s' % (' and `temporal`' if temporal else '')


GEO_1 = {"version": "1.1.0", "primary_column": "geom",
         "columns": {"geom": {"encoding": "WKB", "geometry_types": ["Point"]}}}
GEO_2 = {"version": "2.0.0", "primary_column": "geom",
         "columns": {"geom": {"encoding": "WKB", "geometry_types": ["Point"]}}}
TEMPORAL = {"version": "2.0.0", "primary_temporal_column": "traj",
            "columns": {"traj": {"encoding": "MEOS-WKB", "base_type": "tgeompoint",
                                 "covering": {
                                     "bbox": {k: ["traj_bbox", k] for k in
                                              ("xmin", "ymin", "xmax", "ymax")},
                                     "tspan": {k: ["traj_tspan", k] for k in
                                               ("tmin", "tmax")}}}}}


def write(path, geo, temporal, bbox_fields=BBOX_FIELDS[0]):
    bbox = pa.struct([(f, pa.float64()) for f in bbox_fields])
    tspan = pa.struct([('tmin', pa.timestamp('us', tz='UTC')),
                       ('tmax', pa.timestamp('us', tz='UTC'))])
    table = pa.table({
        'geom': pa.array([b''], pa.binary()),
        'traj': pa.array([b''], pa.binary()),
        'traj_bbox': pa.array([{f: 0.0 for f in bbox_fields}], bbox),
        'traj_tspan': pa.array([{'tmin': 0, 'tmax': 0}], tspan)})
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
    cases.append(('a 1.1 `geo` beside `temporal`',
                  (GEO_1, TEMPORAL, BBOX_FIELDS[0]), True))
    cases.append(('a 2.0 `geo` beside `temporal`',
                  (GEO_2, TEMPORAL, BBOX_FIELDS[0]), True))
    cases.append(('`temporal` alone', (None, TEMPORAL, BBOX_FIELDS[0]), True))

    damaged = copy.deepcopy(GEO_2)
    del damaged['columns']['geom']['geometry_types']
    cases.append(('`geo` damaged by a writer',
                  (damaged, TEMPORAL, BBOX_FIELDS[0]), False))

    native = copy.deepcopy(GEO_2)
    native['columns']['geom']['encoding'] = 'point'
    cases.append(('a 2.0 `geo` with a 1.1 encoding',
                  (native, TEMPORAL, BBOX_FIELDS[0]), False))

    shadowing = copy.deepcopy(TEMPORAL)
    shadowing['columns']['geom'] = shadowing['columns'].pop('traj')
    cases.append(('`temporal` shadowing a geometry column',
                  (GEO_2, shadowing, BBOX_FIELDS[0]), False))

    squatting = copy.deepcopy(TEMPORAL)
    squatting['columns']['traj']['covering']['bbox']['xmin'] = ['geom', 'xmin']
    cases.append(('a covering pointed at a geometry column',
                  (GEO_2, squatting, BBOX_FIELDS[0]), False))

    timed = copy.deepcopy(TEMPORAL)
    for k in ('tmin', 'tmax'):
        timed['columns']['traj']['covering']['bbox'][k] = ['traj_bbox', k]
    cases.append(('a `bbox` covering carrying time bounds',
                  (GEO_2, timed, BBOX_FIELDS[0] + ('tmin', 'tmax')), False))

    ok = True
    with tempfile.TemporaryDirectory() as d:
        for label, (geo, temporal, fields), expected in cases:
            path = os.path.join(d, 'case.parquet')
            write(path, geo, temporal, fields)
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
