
# Benchmark

What a physical layout costs and what it buys. One month of Danish AIS is built
into six layouts, published to an Iceberg REST catalog, and queried through both
the catalog and plain Parquet, measuring files and bytes read against the totals.

| stage | what it does |
| --- | --- |
| [`pipeline/`](pipeline) | ingesting, cleaning and segmentation, the L0-L4 layouts, the trips projection, publication, and the query runners |
| [`queries/`](queries) | the ten queries, in three dialects |
| [`deploy/`](deploy) | MinIO and an Iceberg REST catalog |
| [`experiments/`](experiments) | [`evaluation`](experiments/evaluation) turns the results into the figures and tables; also the H3 prefilter study and a raster proof of concept |
| [`results/`](results) | the runs the evaluation quotes |

The raw zone is shell and DuckDB SQL. The rest is Python, because the layout
builders call MobilityDuck through the DuckDB Python API.

## Prerequisites

**MobilityDuck**, built against **DuckDB 1.4.4**, and a build that can construct
L2/L3/L4: the layout writers call `spaceSplit`, `splitEachNStboxes` and
`spaceTimeSplit`. `experiments/raster` needs the build for
`rasterValue`

**Python 3.12+** and **Docker**, the latter only from the publish step onward.

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e pipeline              # [h3] and [evaluation] add the experiments
cp .env.example .env                 # then set MOBILITYDUCK__EXTENSION_PATH
lakehouse --help
```

`.env` is read from the working directory, so run from `benchmark/`. Everything
else resolves against this directory; set `LAKEHOUSE_ROOT` to override.

### The protected-area registry

`queries/iceberg_region/` and `experiments/h3_prefilter/` read
`data/natural_areas/natural_areas.parquet`, which is not committed: it is 31 MB
of derived geometry. Build it from the Danish protected-area registry export:

```bash
python scripts/natural_areas_to_parquet.py path/to/natural_areas.csv
```

The script adds the scalar covering box (xmin, ymin, xmax, ymax) beside each
WKT geometry, which is what the region queries prune on. Nothing else in
`benchmark/` needs it.

On a larger machine, raise the scaling knobs commented out in `.env.example`.
Set the memory limit to about **70 % of RAM**, not all of it: DuckDB does not
account for the MEOS temporal values, and a limit equal to physical RAM is what
gets the process killed.

## Build and query one month

```bash
lakehouse pipeline ingest-range 2026-01-01 2026-01-31
lakehouse pipeline build-l0

lakehouse pipeline build-layout L3 --month 2026-01 --granularity daily \
    --layout-dir data/layouts_daily --region-size-m 50000 --segs-per-box 16
lakehouse pipeline compact-layout L3 --month 2026-01 \
    --src-dir data/layouts_daily --dst-dir data/layout_compact

python -m lakehouse.process.trips L3 L3c
python -m lakehouse.process.sort_compact --layouts L3
```

`--granularity daily` is required: `compact-layout` reads day files, and the
default `monthly` leaves it nothing to do. The per-layout arguments are the only
thing that changes between them:

| layout | `build-layout` arguments |
| --- | --- |
| L1 | `--num-shards 16` |
| L2 | `--region-size-m 50000 --tile-size-m 1000` |
| L3 | `--region-size-m 50000 --segs-per-box 16` |
| L4 | `--time-bin '1 hour'` |

Three names, three directories, not interchangeable: `L3` is the daily layout,
`L3c` is compacted but **not yet sorted** and is only an input to `L3s`, and
`L3s` is what the queries read. `sort_compact` reads what `trips L3c` writes, so
skipping the `L3c` step leaves `L3s` silently empty.

Then publish and query:

```bash
cd deploy/iceberg_rest && docker compose up -d && cd ../..
. deploy/iceberg_rest/env.rest
python -m lakehouse.store.publish L3s
python -m lakehouse.query.run_iceberg --layouts L3s --sels day,week --iters 5
```

`run_parquet` takes the same arguments and reads the Parquet directly, without
the catalog, which is how to confirm both paths agree.

`scripts/run_pipeline.sh` does all four layouts end to end;
`scripts/run_pipeline_L3.sh` does L3 only, month by month, and can prune as it
goes.

## Where the data lives

Two independent choices, both defaulting to local so nothing has to be set up:

| | default | set by |
| --- | --- | --- |
| where `trips` Parquet is written | `data/trips` | `TRIPS_DEST` |
| where the catalog stores tables | `s3://warehouse/` on the compose MinIO | `ICEBERG_WAREHOUSE` |

Any S3-compatible store works, including Google Cloud Storage: there is no
separate code path, TLS is derived from the endpoint URL, so pointing
`ICEBERG_CATALOG_PROP__S3__ENDPOINT` at an `https://` endpoint is enough. The
bucket must already exist; only the compose file creates one, and only on MinIO.
The REST catalog is `apache/iceberg-rest-fixture`, a test fixture, so for anything
beyond experiments use a real catalog and point `ICEBERG_REST_URI` at it.

## Disk

About 1.2 GB per day through the L3 chain. The chain leaves five copies on disk
at five stages, so a month is roughly 20 GB per layout if nothing is pruned
between stages, against ~3.5 GB for the L0 it came from. `L3s` is the only one
queried; the rest is scaffolding and can be deleted once the answers look right.
