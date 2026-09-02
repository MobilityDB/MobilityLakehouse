# Rebuild L3 for 2026-01-15 under bound/threshold variants and re-measure recall
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import pandas as pd

CLI = Path(os.getenv("LAKEHOUSE_ROOT")
           or Path(__file__).resolve().parents[3])
SCRATCH = Path(os.getenv("LAKEHOUSE_SCRATCH", tempfile.gettempdir()))
OUT = SCRATCH / 'l3_variants'

OLD_EXT = os.environ.get("MOBILITYDUCK_EXT_BASELINE", "")
DAY = '2026-01-15'
L0_SRC = CLI / f'data/L0/L0/year=2026/month=01/day={DAY}.parquet'
L0_TRIPS = CLI / f'data/trips/L0/L0/year=2026/month=01/day={DAY}.parquet'
SHIPPED_L3_TRIPS = CLI / f'data/trips/layouts_daily/L3/day={DAY}'

REGION_M = 50_000.0
SEGS_PER_BOX = 16
MIN_MOTION_POINTS = 3

VARIANTS = {
    #           border_inc, stage1 min, stage2 min, zero guards, input guards
    'cur':      ('true',  MIN_MOTION_POINTS, 2, True,  True),
    'relax':    ('true',  1,                 1, False, True),
    'excl':     ('false', MIN_MOTION_POINTS, 2, True,  True),
    # every defensive filter stripped: no point_count/duration/extent input
    # guards, no minimum-instants at either stage, no zero length/duration
    # guards. This is the variant that should resurface the MEOS
    # "Instant sequence must have inclusive bounds" error if it is still live.
    'noguard':  ('true',  1,                 1, False, False),
}

# Time-synced builds: shift the traj EWKB +1h so it lands on the same true-UTC
# clock as the sidecars, instead of sitting 3600 s behind it. This is the
# "dong bo thoi gian" build. sync_nomin additionally strips every threshold, so
# the pair isolates whether numInstants >= 2 still matters once the clocks agree.
SYNC_VARIANTS = {
    'sync':       ('true', MIN_MOTION_POINTS, 2, True,  True),
    'sync_nomin': ('true', 1,                 1, False, False),
}
VARIANTS.update(SYNC_VARIANTS)
TRAJ_SHIFT = "INTERVAL '1 hour'"

OUTPUT_COLS = """
    mmsi, segment_type, vessel_name, imo, callsign, vessel_type,
    start_time, end_time, duration_s,
    bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
    point_count, track_length_m,
    quality_flags, source_file,
    traj
"""

def connect() -> duckdb.DuckDBPyConnection:
    # No SET TimeZone: the shipped pipeline (open_pipeline_conn) does not set one,
    # so it runs in the system default. Forcing UTC here would desynchronise the
    # naive sidecar clock from the 1h-early traj EWKB and make the
    # _temporal_within_parent guard fire spuriously.
    con = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
    con.execute('PRAGMA threads=4;')
    con.execute('SET preserve_insertion_order = false;')
    con.execute('INSTALL spatial; LOAD spatial;')
    con.execute(f"LOAD '{OLD_EXT}';")
    return con

def register_base(con) -> int:
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW base_segments AS
        SELECT * EXCLUDE (traj_wkb, geometry, bbox),
               bbox.xmin AS bbox_min_x, bbox.ymin AS bbox_min_y,
               bbox.xmax AS bbox_max_x, bbox.ymax AS bbox_max_y,
               tgeompointFromBinary(traj_wkb) AS traj
        FROM read_parquet('{L0_SRC.as_posix()}', hive_partitioning=true);
    """)
    return con.execute('SELECT count(*) FROM base_segments').fetchone()[0]

def enumerate_cells(con) -> list[tuple[int, int]]:
    rows = con.execute(f"""
        SELECT DISTINCT rx, ry FROM (
            SELECT floor(ST_X(sp.spaceBin) / {REGION_M})::INTEGER AS rx,
                   floor(ST_Y(sp.spaceBin) / {REGION_M})::INTEGER AS ry
            FROM (SELECT * FROM base_segments
                  WHERE segment_type = 'in motion' AND duration_s > 0
                    AND (bbox_max_x > bbox_min_x OR bbox_max_y > bbox_min_y)) bs,
                 LATERAL spaceSplit(bs.traj, {REGION_M}, {REGION_M}, 1.0,
                                    ST_Point(0, 0), FALSE) sp(spaceBin, tpoint)
            WHERE numInstants(sp.tpoint) >= 1
            UNION
            SELECT floor(bbox_min_x / {REGION_M})::INTEGER AS rx,
                   floor(bbox_min_y / {REGION_M})::INTEGER AS ry
            FROM base_segments WHERE segment_type = 'stationary'
        )
    """).fetchall()
    return sorted((int(a), int(b)) for a, b in rows)

def motion_sql(box, border, min1, min2, zero_guards, input_guards=True) -> str:
    x0, x1, y0, y1 = box
    zg = (" AND length(traj) > 0"
          " AND DATE_DIFF('second', startTimestamp(traj), endTimestamp(traj)) > 0"
          if zero_guards else "")
    ig = (f" AND point_count >= {MIN_MOTION_POINTS}"
          f" AND duration_s > 0"
          f" AND (bbox_max_x > bbox_min_x OR bbox_max_y > bbox_min_y)"
          if input_guards else "")
    sub = f"""
        SELECT * FROM (
            SELECT mmsi, segment_type, vessel_name, imo, callsign, vessel_type,
                   start_time, end_time, duration_s,
                   bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                   point_count, track_length_m, quality_flags, source_file,
                   UNNEST(sequences(atStbox(traj,
                       ST_MakeEnvelope({x0}, {y0}, {x1}, {y1})::STBOX,
                       {border}))) AS traj
            FROM base_segments
            WHERE segment_type = 'in motion' {ig}
              AND bbox_max_x >= {x0} AND bbox_min_x < {x1}
              AND bbox_max_y >= {y0} AND bbox_min_y < {y1}
        ) AS _exploded
        WHERE traj IS NOT NULL AND numInstants(traj) >= {min1} {zg}
    """
    return f"""
        SELECT bs.mmsi, 'in motion' AS segment_type,
               bs.vessel_name, bs.imo, bs.callsign, bs.vessel_type,
               startTimestamp(clipped.traj) AS start_time,
               endTimestamp(clipped.traj)   AS end_time,
               DATE_DIFF('second', startTimestamp(clipped.traj),
                         endTimestamp(clipped.traj))::INTEGER AS duration_s,
               Xmin(mb.box)::DOUBLE AS bbox_min_x, Ymin(mb.box)::DOUBLE AS bbox_min_y,
               Xmax(mb.box)::DOUBLE AS bbox_max_x, Ymax(mb.box)::DOUBLE AS bbox_max_y,
               numInstants(clipped.traj)::INTEGER AS point_count,
               length(clipped.traj)::DOUBLE       AS track_length_m,
               bs.quality_flags, bs.source_file,
               clipped.traj AS traj
        FROM ({sub}) bs,
             LATERAL UNNEST(splitEachNStboxes(bs.traj, {SEGS_PER_BOX})) AS mb(box),
             LATERAL (SELECT atStbox(bs.traj, mb.box, {border}) AS traj) AS clipped
        WHERE clipped.traj IS NOT NULL
          AND numInstants(clipped.traj) >= {min2}
          AND Xmin(mb.box) >= {x0} AND Xmin(mb.box) < {x1}
          AND Ymin(mb.box) >= {y0} AND Ymin(mb.box) < {y1}
          AND startTimestamp(clipped.traj) >= bs.start_time
          AND endTimestamp(clipped.traj) <= bs.end_time
    """

def passthrough_sql(box) -> str:
    x0, x1, y0, y1 = box
    return f"""
        SELECT {OUTPUT_COLS}
        FROM base_segments
        WHERE segment_type = 'stationary'
          AND bbox_min_x >= {x0} AND bbox_min_x < {x1}
          AND bbox_min_y >= {y0} AND bbox_min_y < {y1}
    """

# The flat `trips` projection the benchmark queries read (mirrors
# -m lakehouse.process.trips). L3's start_time is TIMESTAMPTZ -> the Europe/Rome
# re-interpretation that puts the 1h-early traj clock back on true UTC.
TRIPS_PROJECT = """
    mmsi, vessel_type AS ship_type, segment_type,
    asEWKB({traj})::BLOB AS traj,
    ({tmin}) AS tmin, ({tmax}) AS tmax,
    bbox_min_x AS xmin, bbox_max_x AS xmax,
    bbox_min_y AS ymin, bbox_max_y AS ymax,
    ((({tmin})) AT TIME ZONE 'UTC')::DATE AS dt
"""
# NB the explicit AT TIME ZONE 'UTC' on dt: build_trips.py runs its projection
# under SET TimeZone='UTC', so its `tmin::DATE` is a UTC date. This script keeps
# the session in the system TZ (the layout build needs that -- see connect()),
# so the cast has to be pinned or late-evening rows get tomorrow's partition date
# and fall out of the queries' `dt BETWEEN` predicate.

def build_variant(con, name, cells) -> dict:
    border, min1, min2, zero_guards, input_guards = VARIANTS[name]
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    parts, rows = [], 0
    errors: list[str] = []
    for i, (rx, ry) in enumerate(cells, 1):
        box = (rx * REGION_M, (rx + 1) * REGION_M, ry * REGION_M, (ry + 1) * REGION_M)
        sel = (f"{motion_sql(box, border, min1, min2, zero_guards, input_guards)} "
               f"UNION ALL {passthrough_sql(box)}")
        # tmin/tmax exactly as -m lakehouse.process.trips derives them for L3: the
        # UNION ALL has already coerced the stationary passthrough's naive
        # start_time to TIMESTAMPTZ through the session TZ, so the whole column
        # is tz-aware and build_trips takes its is_tz=True branch -- one uniform
        # Europe/Rome re-interpretation that puts the 1h-early traj clock back on
        # true UTC. Special-casing stationary rows here would NOT match the
        # shipped layout.
        if name in SYNC_VARIANTS:
            # Put the traj EWKB on true UTC, then derive the sidecars FROM it, so
            # the two clocks agree by construction instead of by the accident of
            # a Europe/Rome re-interpretation cancelling a 3600 s lag.
            shifted = f"shiftTime(traj, {TRAJ_SHIFT})"
            proj = TRIPS_PROJECT.format(
                traj=shifted,
                tmin=f"startTimestamp({shifted})",
                tmax=f"endTimestamp({shifted})")
        else:
            proj = TRIPS_PROJECT.format(
                traj="traj",
                tmin="(start_time AT TIME ZONE 'Europe/Rome') AT TIME ZONE 'UTC'",
                tmax="(end_time AT TIME ZONE 'Europe/Rome') AT TIME ZONE 'UTC'")
        tgt = out / f'cell_{rx}_{ry}.parquet'
        try:
            con.execute(f"""
                COPY (SELECT {proj} FROM ({sel}))
                TO '{tgt.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD);
            """)
        except Exception as e:
            errors.append(f'({rx},{ry}): {type(e).__name__}: {str(e)[:160]}')
            print(f'    [{name}] ERROR cell ({rx},{ry}): {str(e)[:160]}', flush=True)
            continue
        n = con.execute(
            f"SELECT count(*) FROM read_parquet('{tgt.as_posix()}')").fetchone()[0]
        if n == 0:
            tgt.unlink()
        else:
            parts.append(tgt)
            rows += n
        if i % 25 == 0 or i == len(cells):
            print(f"    [{name}] {i}/{len(cells)} cells, {rows} rows, "
                  f"{time.perf_counter()-t0:.0f}s", flush=True)
    dt = time.perf_counter() - t0
    stats = con.execute(f"""
        SELECT count(*) AS n_rows, count(DISTINCT mmsi) AS n_vessels,
               sum(segment_type = 'in motion')::BIGINT AS n_motion
        FROM read_parquet('{(out / "*.parquet").as_posix()}')
    """).fetchone()
    nbytes = sum(p.stat().st_size for p in parts)
    if errors:
        print(f'    [{name}] {len(errors)} cell(s) FAILED:')
        for e in errors[:10]:
            print(f'      {e}')
    return {'variant': name, 'files': len(parts), 'rows': stats[0],
            'vessels': stats[1], 'motion_rows': stats[2],
            'bytes': nbytes, 'build_s': round(dt, 1),
            'failed_cells': len(errors)}

def main():
    only = sys.argv[1:] or list(VARIANTS)
    OUT.mkdir(parents=True, exist_ok=True)
    os.chdir(CLI)
    con = connect()
    print('session TZ:', con.execute("SELECT current_setting('TimeZone')").fetchone()[0])
    n = register_base(con)
    print(f'base_segments for {DAY}: {n} rows')
    t = time.perf_counter()
    cells = enumerate_cells(con)
    print(f'{len(cells)} region cells enumerated ({time.perf_counter()-t:.0f}s)')

    out_rows = []
    for name in only:
        print(f'\n=== building L3 variant "{name}" '
              f'(border_inc={VARIANTS[name][0]}, stage1>={VARIANTS[name][1]}, '
              f'stage2>={VARIANTS[name][2]}, zero_guards={VARIANTS[name][3]}) ===',
              flush=True)
        out_rows.append(build_variant(con, name, cells))
        print(out_rows[-1], flush=True)

    df = pd.DataFrame(out_rows)
    df.to_csv(SCRATCH / 'l3_variant_build_stats.csv', index=False)
    print('\n' + df.to_string(index=False))

if __name__ == '__main__':
    main()
