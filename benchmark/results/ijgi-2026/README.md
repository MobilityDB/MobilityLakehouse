# The runs behind Section 7 of the IJGI paper

Every timed run of the evaluation of *An Open Lakehouse for Maritime Mobility Analytics over AIS
Data* (IJGI, revised version), as the benchmark scripts of this repository recorded it, with the summaries the paper's
tables and figures are read from. No run is discarded: each file holds every run of its campaign,
the untimed first run of a combination excepted.

The runs come from one build, MobilityDuck commit `a3c4632` on DuckDB 1.5.6, on one machine: an
AMD Ryzen 9 5900HX laptop (8 cores, 16 hardware threads, 32 GB) running Ubuntu 24.04 under WSL2,
capped at 24 GB of memory, the data on an ext4 virtual disk on a Kingston NV3 NVMe SSD (Section
7.1). They read each layout's Parquet files from local disk, except the catalog runs, which read
them through Iceberg or DuckLake over a local MinIO store. Within a campaign the layouts are the
innermost loop: every layout answers a query at a window before the next query starts.

The AIS data themselves are not here: the Danish Maritime Authority does not allow their
redistribution, and `../../reproduce.sh` rebuilds everything from its archives.

## The run files

Each row of a run file is one run.

| Column | Meaning |
|---|---|
| `source` | `lake` (Parquet files read directly), `iceberg` or `ducklake` |
| `bounds` | `flat`: the queries filter on the top-level bounding columns |
| `layout` | the layout of Section 5 (`L3cr` is the paper's L3c'; `GP` the point table of Section 7.11) |
| `window` | `1h`, `1day`, `1week`, `1month`; in the diversity runs, `<condition>/<window>` |
| `query` | `q01` to `q10`, the ten queries of Section 6 |
| `mode` | `warm` (one engine, after one untimed run) or `cold` (page cache dropped, fresh engine per run) |
| `iter` | the run's number within its combination |
| `seconds` | the run's time, the sum of DuckDB's timer over the run's statements |
| `answer` | the query's answer: a count or an aggregate, never a vessel identifier |
| `peak_kb` | the engine's peak resident memory |
| `load1` | the one-minute load average when the combination started |
| `started` | when the combination started |
| `error` | empty for a run that completed |

| File | Campaign | Runs | Paper |
|---|---|---:|---|
| `runs/warm-base.csv` | warm, January, 23 layouts (the 12 of Section 5 and the 11 ablation layouts), 4 windows, 10 queries, 10 runs each | 9,200 | 7.2, 7.3 (Figure 13), 7.6 (Table 12, Figure 15), 7.7 (Table 13) |
| `runs/cold-base.csv` | cold, January, the 12 layouts, 10 runs each | 4,800 | 7.3.1 (cold runs) |
| `runs/warm-iceberg.csv`, `runs/warm-ducklake.csv` | warm, the 6 key layouts through each catalog | 2,400 each | 7.5 (Figure 14b) |
| `runs/warm-sens-*.csv` | warm, each sensitivity variant beside its own L0, 1 day and 1 week (`bin1h` and `bin6h` also 1 hour), 10 runs each | 400 to 600 each | 7.8 (Table 14) |
| `runs/diversity/<condition>.csv` | warm, the 24 conditions of `conditions/conditions.csv`, L0 L1s L2s L3s L3cr, 3 windows, 3 runs each | 450 each | 7.9 (Table 15) |
| `runs/diversity.csv` | the 24 condition files merged, windows named `<condition>/<window>` | 10,800 | 7.9 (Table 15, the Wilcoxon tests) |
| `runs/year-cold.csv` | cold, November 2025 to October 2026, L0 L0H L3 L1s L2s L3s L3cr, 10 runs each | 2,800 | 7.10 |
| `runs/warm-points.csv`, `runs/warm-points-week.csv`, `runs/warm-points-month.csv` | warm, L0, L3s and the point table GP | 830, 18, 290 | 7.11 (Table 16) |
| `runs/cleaning/warm-<setting>.csv` | warm, L0 and L3s rebuilt under each cleaning setting, 3 runs each | 240 each | 7.12 (Table 19) |
| `runs/checks/cold-check-*.csv` | short cold passes, 3 runs each, with the layouts interleaved, against which the ratios of a pass run layout by layout are compared | 240, 720 | none: a check, not a result |

The 49 run files hold 40,378 runs (`runs/diversity.csv` repeats the condition files and is not
counted). Every run completes except 20 runs of `runs/warm-points-month.csv`: the point table's
Queries 8 and 9 at the month window, stopped at the limit of 3,600 s and recorded with the error
`stopped at the limit of 3600 s`, which Table 16 reports as taking longer than that. No run takes
zero or negative time. In every combination the runs agree with one another, and every layout's
answer equals L0's, except the point table GP in 16 combinations, whose answers are smaller by 0.2
to 9.4% as Section 7.11 states.

## The summaries

`summaries/<run file>.csv` is the output of `planar/71_summarize.py` on the run file: per
combination, the number of runs, their median and mean, the standard deviation and the 95%
confidence interval of the mean (Student-t), the fastest and slowest run, the answer, whether the
runs agree, and whether the answer matches L0's. `summaries/<run file>-speedups.csv` is
`71_summarize.py --speedups`: the speedup of each layout over L0 (the ratio of the means) with a
95% percentile bootstrap interval (10,000 resamples of the runs of both layouts, fixed seed), per
(window, query), per query, per window and over all. The `all` rows are the paper's headline
speedups: L3s 4.15 and L3cr 4.96 in `warm-base-speedups.csv`, L3s 3.85 in
`cold-base-speedups.csv`, L3cr 1.98 and L3s 1.82 in `year-cold-speedups.csv`.
`summaries/warm-iceberg-against-warm-base.csv` and `warm-ducklake-against-warm-base.csv` give each
layout through a catalog over the same layout read directly (Figure 14b), and
`cold-base-against-warm-base.csv` the cold run over the warm one.

Every summary is reproduced from the run files alone, for example

    python3 benchmark/planar/71_summarize.py benchmark/results/ijgi-2026/runs/warm-base.csv --speedups

and the exact Wilcoxon test of two layouts by `71_summarize.py <runs> --wilcoxon A B`.

## The measures

| File | Content | Paper |
|---|---|---|
| `measures/january-pruning.csv`, `measures/january-storage.csv` | bytes and files each query reads per layout, and each layout's storage | 7.3 (Figure 12), 7.6 (Table 12) |
| `measures/catalog-pruning.csv` | files each catalog admits per query | 7.5 (Figure 14a) |
| `measures/sens-*-pruning.csv`, `measures/sens-*-storage.csv` | the same for each sensitivity variant | 7.8 (Table 14) |
| `measures/year-pruning.csv`, `measures/year-storage.csv` | the same over the year | 7.10 |
| `measures/points-pruning.csv`, `measures/points-storage.csv` | the same for the point table | 7.11 (Table 16) |
| `measures/cleaning/audit-stages-*.csv`, `audit-removed-*.csv` | the reports and vessels each cleaning stage removes, and the removed vessels by ship type and number of reports | 7.12 (Table 18) |
| `measures/cleaning/segments-*.csv`, `storage-*.csv` | the segments and storage of each cleaning setting | 7.12 (Table 19) |
| `conditions/conditions.csv`, `conditions/<condition>.csv` | the 24 regions and anchor times of Section 7.9, and the windows each condition runs | 7.9 |

## What is not here

The answer-set comparisons of Section 7.6.1, which list the vessels each layout returns, are left
out, since vessel identifiers come from the AIS data; `planar/56_answer_sets.py` recomputes them.
Passes that a later pass replaces are left out too: passes run layout by layout, whose ratios
carry the drift of the machine's speed over hours, and a pass of the four seasons whose timer
reads a system clock stepped under it (`planar/70_queries.py` runs an engine again when the clock
steps).
