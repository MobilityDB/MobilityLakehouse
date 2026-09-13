# The planar benchmark

The experiments of the lakehouse paper run on the AIS reports the Danish Maritime Authority (DMA)
publishes, one archive per day. The scripts here take a period of those archives to the L0 base
layer of TemporalParquet trips in EPSG:25832 and to the eleven layouts the paper compares, check
every layout before a figure is read from it, and answer and time the paper's ten queries on each.

## The data

The data are DMA's and are not redistributed with this repository. They are published at
<http://aisdata.ais.dk/> as `aisdk-YYYY-MM-DD.zip`, one CSV per day. The paper measures January
2026, the 31 archives `aisdk-2026-01-01.zip` to `aisdk-2026-01-31.zip`, 17.33 GB in all; one week
of it, 2026-01-15 to 2026-01-21, is 4.15 GB. `ingest/fetch_dma.sh` downloads a period onto the
machine that runs the benchmark, into the repository's `data/` directory, which git ignores:

```bash
benchmark/ingest/fetch_dma.sh 2026-01-01 2026-01-31           # the month
benchmark/ingest/fetch_dma.sh 2026-01-15 2026-01-21 --check   # the size of a week, nothing downloaded
```

A download is renamed into place only once it is complete, and a day already downloaded or already
in the raw zone is skipped, so an interrupted download resumes where it stopped.

## Requirements

- A DuckDB shell carrying the MobilityDuck extension, built from
  <https://github.com/MobilityDB/MobilityDuck>, named by `DUCKDB_ENGINE` or by a local file
  `planar/engine.path` holding its path (git ignores it).
- `bash`, `curl`, `unzip`, `python3` with matplotlib, which draws the figures, and GNU `time` at
  `/usr/bin/time`, which the query harness reads each engine's peak memory from.
- For the catalogs, Docker Compose, which runs MinIO and the Iceberg REST catalog
  (`planar/iceberg/docker-compose.yml`), and the Python packages of `planar/requirements.txt`:

  ```bash
  python3 -m venv .venv && .venv/bin/pip install -r benchmark/planar/requirements.txt
  export PYTHON=$PWD/.venv/bin/python
  ```
- For the answers of MobilityDB, a PostgreSQL server carrying MobilityDB, PostGIS and
  [pg_parquet](https://github.com/CrunchyData/pg_parquet), `psql`, and a role that may read the
  server's files, named by the usual `PGHOST`, `PGPORT`, `PGDATABASE` and `PGUSER`.
- For the answers of MobilitySpark, a JDK 21, Maven, and a
  [MobilitySpark](https://github.com/MobilityDB/MobilitySpark) checkout built by its
  `tools/refresh-from-master.sh`, which builds MEOS, JMEOS and MobilitySpark from MobilityDB master,
  named by `MOBILITYSPARK`:

  ```bash
  git clone https://github.com/MobilityDB/MobilitySpark.git && MobilitySpark/tools/refresh-from-master.sh
  export MOBILITYSPARK=$PWD/MobilitySpark
  ```
- For the cell-cover soundness, a MEOS install built with H3 from MobilityDB at the commit
  MobilityDuck builds its MEOS from (`_MEOS_REF` in MobilityDuck's `vcpkg_ports/meos/portfile.cmake`),
  named by `MEOS_PREFIX`, and the H3 static archive that MEOS build links (`H3_INCLUDE_DIR`,
  `H3_LIBRARY`, by default `/usr/local/include/h3` and `/usr/local/lib/libh3.a`).
  `planar/cellcover/build.sh` links the harness against both:

  ```bash
  cmake -S MobilityDB -B build-meos -DMEOS=ON -DALL=ON -DH3=ON -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=$HOME/meos
  cmake --build build-meos -j && cmake --install build-meos
  export MEOS_PREFIX=$HOME/meos
  ```
- Disk: for the month, 17.3 GB of archives, 11 GB of raw zone, and in the run directory 9.0 GB of
  vessel buckets, 9.2 GB of clean points, 8.0 GB of segments, 3.3 GB of L0, 23.0 GB of daily
  layouts and 12.6 GB of compact layouts.

## Reproducing

```bash
benchmark/reproduce.sh 2026-01-01 2026-01-31
```

runs, for the days given, both included, the raw zone of every day not yet in it
(`ingest/raw_zone.sh`), the cleaning, segmentation and L0 (`planar/run_clean.sh`), the layouts
(`planar/40_order_layouts.sh`, `planar/41_part_layouts.sh`, `planar/42_compact_layouts.sh`) and
their checks (`planar/92_check_layouts.sh`), the sensitivity of the spatial layouts to their
region cell (`planar/54_cell_sensitivity.sql`), the table of the cleaned segments by type
(`planar/73_segment_table.sql`), the vessels each layout answers each window with against L0's
(`planar/51_verify_answers.sh`), what the row-group statistics of each layout let a window skip
(`planar/50_query_layouts.sh`), the storage each layout takes (`planar/53_storage.py`), the
registration of every layout as an Iceberg table in the REST catalog over MinIO
(`planar/80_register.py`) and as a DuckLake table (`planar/81_register_ducklake.sh`), what the two
catalogs let each window skip at the file level (`planar/52_catalog_pruning.py`), the soundness of
the H3 cell cover over the moving trips carried to WGS84 at resolutions 7 to 12, its coarsening
and its cost (`planar/46_trips.sh`, `planar/60_soundness.sh`, `planar/cellcover/`), then the ten
queries (`planar/queries/`) on every layout and window, timed over the files and through each
catalog, and the evaluation figures of the paper (`planar/74_figures.py`). Without arguments it
runs the paper's month. It stops and names the download command when a day of the period is missing, and stops at
the answer gate when a layout's vessels differ from L0's.

The run lands in `data/stage/planar/<FROM>_<TO + 1 day>/`: `L0/`, `layouts_daily/` (L0X, L0Z, L0H,
L1 to L4) and `layout_compact/` (L1s to L4s). The results land in
`data/results/planar/<FROM>_<TO + 1 day>/`:

- `cell_size.csv` and `density.csv`, computed from L0's bounds alone: per region cell size, the
  files a compact spatial layout writes, the cells per segment and each query region's share of
  the stored bytes; at the layouts' 50 km cell, that share for the query regions and for four
  open-water boxes;
- `segment-table.txt`, per segment type the rows, vessels, median and mean duration, mean instants
  and mean length of L0, then all rows over all vessels;
- `layout-answers.csv`, the vessels each layout answers per window, which equal L0's in every row;
- `layout-pruning.csv`, per layout and window the row groups, files, rows and bytes the window
  admits and their share of the layout;
- `storage.csv`, per layout the files, rows, rows per row of L0 and bytes;
- `catalog-pruning.csv`, per catalog, covering form, layout and window the files and bytes the
  catalog's recorded bounds admit and the files the engine's scan of the table reads;
- `soundness-res7.csv` to `soundness-res12.csv`, per trip cover and region cover the pairs of trip
  and window the exact predicate accepts, the pairs each cover admits, and its recall;
- `coarsen-12-10.csv` and `coarsen-10-7.csv`, a cover stored at the first resolution and read down
  to the second against the cover rebuilt there: the cells and rows that differ, the candidates
  and recall of each, and the time of each;
- `cost-L0-<res>.csv` and `cost-L3s-<res>.csv` at resolutions 7, 10 and 12, per window the rows the
  stored box keeps, those the stored cover then keeps, the exact predicate's seconds over each and
  the cover test's, and the cell column's size against the trajectory column's;
- `answers.csv`, the answer of each query in each window, read from L0 (`planar/72_answers.py`);
- `recall.csv`, each layout's count over L0's for the counting queries, per window, which reads
  1.0000 wherever L0's answer is not zero;
- `query-runtime-summary.csv`, per layout, window and query, the trimmed mean of five warm runs,
  its 95% interval, the peak memory, and whether the runs agree and match L0
  (`planar/71_summarize.py`);
- `query-runtime-iceberg-summary.csv` and `query-runtime-ducklake-summary.csv`, the same for the
  queries read through the Iceberg catalog and through DuckLake;
- `figures/`, the evaluation figures: the share of its bytes each layout reads, the speedup of
  each query and layout over L0, the trade-off of speedup against bytes read, and the files a
  query reads and the speedup it gains through each catalog.

The windows are the paper's (`planar/windows_25832.csv`, written by `planar/45_windows.sql`): four
regions crossed with an hour, a day and a week from 2026-01-15 08:00 UTC and the month of January.
A period that does not contain a window answers it over no data.

`STEPS=mobilitydb` answers the ten queries with MobilityDB in PostgreSQL over the run's L0, which
it copies into a table with pg_parquet, and writes `mobilitydb-answers.csv`: per window and query
the answer, L0's answer from `answers.csv`, and whether the two state the same numbers.
`STEPS=mobilityspark` answers them with MobilitySpark in Apache Spark over the run's L0, which Spark
reads in place, and writes `mobilityspark-answers.csv` in the same form.

`STEPS` selects the steps an invocation runs, for instance `STEPS="layouts check"` to rebuild the
layouts of a run whose L0 exists. `STEPS=cold` adds the timing after dropping the page cache before
every run, which needs passwordless `sudo tee /proc/sys/vm/drop_caches`.
