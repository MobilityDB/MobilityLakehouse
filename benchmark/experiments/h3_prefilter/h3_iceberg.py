# Read the lakehouse layouts from the Iceberg REST catalog for the H3 prefilter
from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

BENCH_ROOT = Path(os.getenv("LAKEHOUSE_ROOT")
                  or Path(__file__).resolve().parents[2])
ENV_REST = BENCH_ROOT / "deploy" / "iceberg_rest" / "env.rest"
QUERY_DIR = BENCH_ROOT / "queries"
CACHE = Path(__file__).resolve().parent / "cache"

FINEST_RES = 11          # cells stored at this res; 8/9/10 derived as parents
NAMESPACE = "ais"

# Rodby-Puttgarden alert belt, EPSG:32632.
BELT = (640730.0, 654100.0, 6042487.0, 6058230.0)   # (x0, x1, y0, y1)

def load_env() -> None:
    if not ENV_REST.exists():
        raise FileNotFoundError(f"missing {ENV_REST}")
    for line in ENV_REST.read_text().splitlines():
        m = re.match(r"\s*export\s+([A-Z0-9_]+)=(.*)$", line)
        if m:
            os.environ.setdefault(m.group(1), m.group(2).strip().strip('"'))

def _mobilityduck_ext() -> str:
    env = BENCH_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "MOBILITYDUCK__EXTENSION_PATH" and Path(v.strip()).exists():
                os.environ.setdefault("MOBILITYDUCK_EXT", v.strip())
                return v.strip()
    from lakehouse.query.registry import MOBILITYDUCK_EXT
    return MOBILITYDUCK_EXT

def connect():
    import duckdb
    load_env()
    c = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    c.execute("INSTALL iceberg; LOAD iceberg;")
    c.execute("INSTALL httpfs; LOAD httpfs;")
    c.execute(f"LOAD '{_mobilityduck_ext()}';")
    c.execute("INSTALL spatial; LOAD spatial;")
    ep = os.getenv("ICEBERG_CATALOG_PROP__S3__ENDPOINT",
                   "http://localhost:9000").removeprefix("http://")
    c.execute(f"CREATE SECRET (TYPE S3, KEY_ID '{os.getenv('AWS_ACCESS_KEY_ID','admin')}', "
              f"SECRET '{os.getenv('AWS_SECRET_ACCESS_KEY','password')}', ENDPOINT '{ep}', "
              f"URL_STYLE 'path', USE_SSL false, REGION 'us-east-1');")
    rest = os.getenv("ICEBERG_REST_URI", "http://localhost:8181")
    c.execute(f"ATTACH '' AS lake (TYPE ICEBERG, ENDPOINT '{rest}', "
              f"AUTHORIZATION_TYPE 'none');")
    return c

def sample_step_m(res: int) -> float:
    import h3
    return max(6.0, h3.average_hexagon_edge_length(res, unit="m") / 2.5)

def _cells_sql(step_m: float, res: int) -> str:
    return f"""
    geo AS (
      SELECT oid, CASE WHEN ST_GeometryType(g) = 'LINESTRING'
                       THEN ST_Length(g) ELSE 0.0 END AS len_m
      FROM src
    ),
    -- Normalize to one row per simple part. A tile can hold a MULTILINESTRING
    -- (the vessel re-enters it), and legs must not be built across the gap
    -- between pieces. Simple geometries take a branch that never dumps a
    -- dumped geometry: nesting the two unnests makes DuckDB copy the geometry
    -- per vertex and blows out memory on long trajectories.
    line AS (
      SELECT oid, 1 AS part, g FROM src WHERE ST_GeometryType(g) = 'LINESTRING'
      UNION ALL
      -- a single-instant segment decodes to a POINT; doubling it into a
      -- zero-length line yields exactly its own cell
      SELECT oid, 1, ST_MakeLine([g, g]) FROM src WHERE ST_GeometryType(g) = 'POINT'
      UNION ALL
      SELECT s.oid, coalesce(u.d.path[1], 1),
             CASE WHEN ST_GeometryType(u.d.geom) = 'POINT'
                  THEN ST_MakeLine([u.d.geom, u.d.geom]) ELSE u.d.geom END
      FROM src s, unnest(ST_Dump(s.g)) AS u(d)
      WHERE ST_GeometryType(s.g) NOT IN ('LINESTRING', 'POINT')
    ),
    verts AS (
      SELECT l.oid, l.part, v.d.path[1] AS i,
             ST_X(v.d.geom) AS x, ST_Y(v.d.geom) AS y
      FROM line l, unnest(ST_Dump(ST_Points(l.g))) AS v(d)
    ),
    legs AS (
      SELECT oid, x AS x1, y AS y1,
             lead(x) OVER (PARTITION BY oid, part ORDER BY i) AS x2,
             lead(y) OVER (PARTITION BY oid, part ORDER BY i) AS y2
      FROM verts
    ),
    legn AS (
      SELECT oid, x1, y1, x2, y2,
             greatest(1, ceil(sqrt((x2 - x1) ^ 2 + (y2 - y1) ^ 2)
                              / {step_m})::BIGINT) AS n
      FROM legs WHERE x2 IS NOT NULL
    ),
    -- k = n of one leg is k = 0 of the next, so the path is covered end to end.
    samp AS (
      SELECT oid, x1 + (x2 - x1) * k::DOUBLE / n AS x,
                  y1 + (y2 - y1) * k::DOUBLE / n AS y
      FROM legn, unnest(range(0, n + 1)) AS t(k)
    ),
    cells AS (
      SELECT oid, list_distinct(list(geoToH3Cell(
               ST_Transform(ST_Point(x, y), 'EPSG:32632', 'EPSG:4326', true),
               {res})::UBIGINT)) AS cells
      FROM samp GROUP BY oid
    )"""

def _where(area, d0: str, d1: str) -> str:
    w = f"dt BETWEEN DATE '{d0}' AND DATE '{d1}'"
    if area is None:
        return w
    x0, x1, y0, y1 = area
    return (f"{w} AND xmin <= {x1} AND xmax >= {x0} "
            f"AND ymin <= {y1} AND ymax >= {y0}")

def area_tag(area) -> str:
    if area is None:
        return "all"
    if tuple(area) == BELT:
        return "belt"
    x0, x1, y0, y1 = area
    return f"{x0:.0f}_{x1:.0f}_{y0:.0f}_{y1:.0f}"

def load_rows(con, table: str, *, d0: str, d1: str, area=BELT,
              res: int = FINEST_RES) -> pd.DataFrame:
    step = sample_step_m(res)
    sql = f"""
    WITH src AS (
      SELECT row_number() OVER (ORDER BY mmsi, tmin, xmin, ymin) AS oid, mmsi,
             xmin, xmax, ymin, ymax,
             trajectory(tgeompointFromEWKB(traj)) AS g
      FROM lake.{NAMESPACE}.{table}
      WHERE {_where(area, d0, d1)}
    ),
    {_cells_sql(step, res)}
    SELECT s.oid, s.mmsi, s.xmin, s.xmax, s.ymin, s.ymax,
           ST_AsWKB(s.g) AS wkb, g.len_m, c.cells
    FROM src s JOIN geo g USING (oid) JOIN cells c USING (oid)
    ORDER BY s.oid
    """
    return con.execute(sql).df()

def load(con, name: str, *, d0: str, d1: str, area=BELT, res: int = FINEST_RES,
         cache: bool = True) -> pd.DataFrame:
    CACHE.mkdir(exist_ok=True)
    key = f"{name}_{area_tag(area)}_{d0}_{d1}_r{res}.parquet"
    path = CACHE / key
    if cache and path.exists():
        return pd.read_parquet(path)
    df = load_rows(con, name, d0=d0, d1=d1, area=area, res=res)
    if cache:
        df.to_parquet(path, index=False)
    return df

def table_stats(tables=("L0", "L2s", "L3s")) -> pd.DataFrame:
    load_env()
    from lakehouse.store.catalog import catalog

    cat = catalog()
    rows = []
    for t in tables:
        tasks = list(cat.load_table(f"{NAMESPACE}.{t}").scan().plan_files())
        rows.append(dict(table=t, files=len(tasks),
                         rows=sum(x.file.record_count for x in tasks),
                         MB=round(sum(x.file.file_size_in_bytes for x in tasks) / 1e6, 1)))
    df = pd.DataFrame(rows)
    df["rows_vs_L0"] = (df["rows"] / df.loc[df.table == "L0", "rows"].iloc[0]).round(2)
    df["MB_vs_L0"] = (df["MB"] / df.loc[df.table == "L0", "MB"].iloc[0]).round(3)
    return df

# Query regions: the Danish protected-area registry.

AREAS_PARQUET = BENCH_ROOT / "data" / "natural_areas" / "natural_areas.parquet"

# Realms whose regions a vessel can actually be inside. Terrestrial §3 habitats
# (bogs, heaths, meadows) are real regions with the loosest boxes in the file,
# but "which ships entered it" is not a question about them.
SEA_REALMS = ("Marine", "Coastal")

def load_areas(con, *, realms=SEA_REALMS, area=None) -> pd.DataFrame:
    where = []
    if realms:
        where.append("realm IN (%s)" % ", ".join(f"'{r}'" for r in realms))
    if area is not None:
        x0, x1, y0, y1 = area
        where.append(f"xmin <= {x1} AND xmax >= {x0} AND ymin <= {y1} AND ymax >= {y0}")
    sql = f"""
    SELECT row_number() OVER (ORDER BY name_eng, desig_eng, xmin, ymin) AS aid,
           name_eng AS name, desig_eng AS desig, realm, n_parts, n_vertices,
           xmin, xmax, ymin, ymax,
           ST_Area(ST_GeomFromText(geom))  AS area_m2,
           ST_AsWKB(ST_GeomFromText(geom)) AS wkb
    FROM read_parquet('{AREAS_PARQUET.as_posix()}')
    {'WHERE ' + ' AND '.join(where) if where else ''}
    ORDER BY aid
    """
    df = con.execute(sql).df()
    df["bbox_area_m2"] = (df.xmax - df.xmin) * (df.ymax - df.ymin)
    df["fill_ratio"] = df.area_m2 / df.bbox_area_m2.replace(0, float("nan"))
    return df

def to_wgs84(geom, transformer):
    import numpy as np
    import shapely
    return shapely.transform(
        geom, lambda a: np.column_stack(transformer.transform(a[:, 0], a[:, 1])))

def region_cells(geom4326, res):
    import numpy as np
    from h3.api import numpy_int as h3n
    shape = h3n.geo_to_h3shape(geom4326)
    cs = h3n.polygon_to_cells_experimental(shape, res, contain="overlap")
    return np.unique(np.asarray(cs, dtype=np.uint64))

# Cell sets as arrays: parents, packing, membership.

def to_parent(cells, res: int):
    import numpy as np
    c = np.asarray(cells, dtype=np.uint64)
    res_mask = np.uint64(0xF) << np.uint64(52)
    out = (c & ~res_mask) | (np.uint64(res) << np.uint64(52))
    return out | ((np.uint64(1) << np.uint64(3 * (15 - res))) - np.uint64(1))

def pack(cell_lists):
    import numpy as np
    lens = np.fromiter((len(c) for c in cell_lists), dtype=np.int64,
                       count=len(cell_lists))
    off = np.zeros(len(lens) + 1, dtype=np.int64)
    np.cumsum(lens, out=off[1:])
    flat = (np.concatenate([np.asarray(c, dtype=np.uint64) for c in cell_lists])
            if len(lens) else np.zeros(0, dtype=np.uint64))
    return flat, off, lens

def any_in(packed, idx, region_sorted):
    import numpy as np
    flat, off, lens = packed
    n = lens[idx]
    out = np.zeros(len(idx), dtype=bool)
    if len(region_sorted) == 0 or n.sum() == 0:
        return out
    cum = np.zeros(len(idx) + 1, dtype=np.int64)
    np.cumsum(n, out=cum[1:])
    pos = np.repeat(off[idx] - cum[:-1], n) + np.arange(cum[-1], dtype=np.int64)
    cells = flat[pos]
    j = np.searchsorted(region_sorted, cells)
    np.clip(j, 0, len(region_sorted) - 1, out=j)
    m = region_sorted[j] == cells
    nz = np.flatnonzero(n)
    out[nz] = np.logical_or.reduceat(m, cum[:-1][nz])
    return out

def publish_h3_sidecar(con, table: str, *, d0: str, d1: str, res: int = FINEST_RES,
                       suffix: str = "_h3") -> str:
    import pyarrow.parquet as pq

    load_env()
    from lakehouse.store.catalog import catalog

    name = f"{table}{suffix}"
    bucket = os.getenv("ICEBERG_WAREHOUSE", "s3://warehouse/").removeprefix("s3://").rstrip("/")
    step = sample_step_m(res)
    days = [r[0] for r in con.execute(
        f"SELECT DISTINCT dt FROM lake.{NAMESPACE}.{table} "
        f"WHERE dt BETWEEN DATE '{d0}' AND DATE '{d1}' ORDER BY 1").fetchall()]
    uris = []
    for day in days:
        # One file per day, matching how the layout tables are partitioned. A single
        # multi-day file would force the "+ H3" arm to open the whole range for a
        # one-day query while the plain arm opens one small file: a handicap that
        # has nothing to do with the prefilter.
        day_uri = f"s3://{bucket}/h3/{name}/dt={day}_r{res}.parquet"
        con.execute(f"""
        COPY (
          WITH src AS (
            SELECT row_number() OVER (ORDER BY mmsi, tmin, xmin, ymin) AS oid, t.*,
                   trajectory(tgeompointFromEWKB(t.traj)) AS g
            FROM lake.{NAMESPACE}.{table} t
            WHERE t.dt = DATE '{day}'
          ),
          {_cells_sql(step, res)}
          SELECT s.* EXCLUDE (g), list_transform(c.cells, x -> x::BIGINT) AS cells
          FROM src s JOIN cells c USING (oid)
        ) TO '{day_uri}' (FORMAT parquet, COMPRESSION zstd)
        """)
        uris.append(day_uri)

    cat = catalog()
    cat.create_namespace_if_not_exists((NAMESPACE,))
    ident = f"{NAMESPACE}.{name}"
    try:
        cat.drop_table(ident)
    except Exception:
        pass
    schema = pq.ParquetFile(uris[0].removeprefix("s3://"), filesystem=_s3fs()).schema_arrow
    cat.create_table(ident, schema=schema).add_files(uris)
    return ident

def _s3fs():
    from lakehouse.store import s3io
    return s3io.s3_fs()
