#!/usr/bin/env python3
"""The ten benchmark queries answered by MobilitySpark in Apache Spark over the L0 files of a run.

Spark reads the L0 day files into the view `trips` as they are, the trajectory its EWKB bytes,
which each query hands to MobilitySpark as hex text (`hex(trip)`), the form its generated functions
read a temporal value in. The functions are MobilitySpark's generated surface, registered on one
local session in UTC by spark/SparkQueries.java, which runs every query of every window in that
session. Each query of queries_spark/ is the Spark SQL text of the query of the same name in
queries/: the same prune on the covering columns followed by the same MEOS functions, its `:name`
parameters bound as 70_queries.py binds them, a timestamp-with-zone literal handed to MEOS as text,
which MobilitySpark parses with MEOS's own timestamptz_in. Each answer is compared with L0's in the
answers table of 72_answers.py number by number, as 75_mobilitydb.py compares its own.

A Spark name holds one function. MobilitySpark's surface lacks atStbox, xMin and xMax; its span
binds bigint_to_span, its valueAtTimestamp binds tbool_value_at_timestamptz, its atTime hands a
span in hex to timestamptz_in, and its round binds trgeometry_round in place of Spark's own. The
queries therefore name the MEOS function the SQL name binds for a temporal point: tgeo_at_stbox
(trip, box, true) for atStbox, tstzspan_make for the span of two timestamps, temporal_at_tstzspan
for atTime over it, temporal_at_timestamptz for the instant valueAtTimestamp reads, and
stbox_xmin .. stbox_ymax over tspatial_to_stbox for a trajectory's extent; and they round a number
by a cast to DECIMAL.

Each answer is kept the moment Spark prints it, in the table's `.part` file beside it, so a session
cut short (an out-of-memory stop, a restart of the machine) loses only the query it was running: the
next run over the same files hands Spark only the blocks the `.part` file does not answer. The file
names the run by a digest of the L0 files, the runner and the MobilitySpark build, and each answer
by a digest of its block's text, so an answer is reused only for the same query over the same data
on the same build; the file is removed once every block is answered. Spark's own log goes to
spark.err beside the compiled runner.

  RUN=<run> python3 planar/76_mobilityspark.py --answers answers.csv [--windows 1day ...]

Environment: RUN (required); ROOT (the repository's data/); MOBILITYSPARK, a MobilitySpark checkout
built by its tools/refresh-from-master.sh, whose target/classes, Maven runtime classpath and
.meos-chain/prefix/lib the session runs on; JAVA (java); SPARK_MEMORY (16g); MOBILITYSPARK_OUT (the
table written, $ROOT/results/planar/mobilityspark-answers.csv).
"""
import argparse
import csv
import glob
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('ROOT') or HERE.parents[1] / 'data')
JAVA = os.environ.get('JAVA', 'java')
MEMORY = os.environ.get('SPARK_MEMORY', '16g')


def module(name, file):
    """A sibling step loaded as a module, for what this step shares with it"""
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The binding of 70_queries.py, reading the query text from queries_spark/ instead of queries/,
# and the number-by-number comparison of 75_mobilitydb.py
q70 = module('queries70', '70_queries.py')
q70.QDIR = HERE / 'queries_spark'
same = module('mobilitydb75', '75_mobilitydb.py').same


def spark_text(statement):
    """A bound statement in Spark SQL: a timestamp with zone reaches MEOS as its text"""
    return statement.replace("TIMESTAMPTZ '", "'")


def session(ms, gen):
    """The runner compiled against MobilitySpark, and the java command and environment that start it"""
    cpfile = ms / 'target' / 'lakehouse-runtime.cp'
    subprocess.run(['mvn', '-q', '-f', str(ms / 'pom.xml'), 'dependency:build-classpath',
                    f'-Dmdep.outputFile={cpfile}'], check=True)
    cp = f"{ms / 'target' / 'classes'}:{cpfile.read_text().strip()}"
    gen.mkdir(parents=True, exist_ok=True)
    subprocess.run(['javac', '-proc:none', '-cp', cp, '-d', str(gen),
                    str(HERE / 'spark' / 'SparkQueries.java')], check=True)
    lib = ms / '.meos-chain' / 'prefix' / 'lib'
    if not (lib / 'libmeos.so').exists():
        sys.exit(f'no libmeos under {lib}: build MobilitySpark with its tools/refresh-from-master.sh')
    # The JVM options Spark needs on Java 17 and later, as MobilitySpark's own suite runs it
    opens = re.findall(r'--add-opens=[^\s<]+', (ms / 'pom.xml').read_text())
    cmd = [JAVA, *opens, f'-Xmx{MEMORY}', f'-Djava.library.path={lib}',
           f'-Djnr.ffi.library.path={lib}', '-cp', f'{gen}:{cp}', 'SparkQueries']
    env = dict(os.environ, LD_LIBRARY_PATH=f"{lib}:{os.environ.get('LD_LIBRARY_PATH', '')}")
    return cmd, env


def digest(*parts):
    """A short digest of the given texts"""
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode())
        h.update(b'\0')
    return h.hexdigest()[:16]


def run_digest(files, ms):
    """The run an answer belongs to: the L0 files, the runner, and the MobilitySpark build"""
    head = subprocess.run(['git', '-C', str(ms), 'rev-parse', 'HEAD'], capture_output=True,
                          text=True).stdout.strip()
    meos = ms / '.meos-chain' / 'prefix' / 'lib' / 'libmeos.so'
    stamp = lambda p: f'{p} {p.stat().st_size} {p.stat().st_mtime_ns}'
    return digest(*(stamp(Path(f)) for f in files), (HERE / 'spark' / 'SparkQueries.java').read_text(),
                  str(ms.resolve()), head, stamp(meos))


def kept_answers(part, run_id):
    """The answers the `.part` file keeps for this run, by label, with the digest of each block"""
    kept = {}
    if part.exists():
        lines = part.read_text().splitlines()
        if lines and lines[0] == f'@@RUN {run_id}':
            for line in lines[1:]:
                label, sep, rest = line.partition('\t')
                block, sep2, value = rest.partition('\t')
                if sep and sep2:
                    kept[label] = (block, value)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--windows', nargs='+', default=['1h', '1day', '1week', '1month'])
    ap.add_argument('--queries', nargs='+', default=list(q70.QUERIES))
    ap.add_argument('--answers', help="72_answers.py's table of L0's answers, compared with")
    a = ap.parse_args()
    run = Path(os.environ.get('RUN') or sys.exit('RUN is required'))
    ms = Path(os.environ.get('MOBILITYSPARK') or sys.exit('MOBILITYSPARK is required'))
    out = Path(os.environ.get('MOBILITYSPARK_OUT', ROOT / 'results/planar/mobilityspark-answers.csv'))
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(glob.glob(f'{run.resolve()}/L0/year=*/month=*/day-*.parquet'))
    if not files:
        sys.exit(f'no L0 under {run}')

    truth = {}
    if a.answers:
        with open(a.answers) as f:
            for r in csv.DictReader(f):
                for w in a.windows:
                    truth[(r['query'], w)] = r.get(w) or ''

    regions, windows = q70.read_windows()
    blocks = {}
    for win in a.windows:
        t0, t1 = windows[win]
        for q in a.queries:
            stmts = q70.statements(q, regions, t0, t1, 'flat')
            blocks[f'{win} {q}'] = '\n'.join(spark_text(s) for s in stmts) + '\n'
    block_id = {label: digest(text) for label, text in blocks.items()}

    # The answers an earlier session of this run kept, for the blocks whose text is unchanged
    part = out.with_name(out.name + '.part')
    run_id = run_digest(files, ms)
    answers, errors = {}, {}
    for label, (block, value) in kept_answers(part, run_id).items():
        if block_id.get(label) == block:
            answers[label] = value
    with open(part, 'w') as f:
        f.write(f'@@RUN {run_id}\n')
        for label, value in answers.items():
            f.write(f'{label}\t{block_id[label]}\t{value}\n')
    todo = [label for label in blocks if label not in answers]
    if answers:
        print(f'{len(answers)} answers kept from an earlier session, {len(todo)} to answer',
              flush=True)

    if todo:
        gen = run / 'gen' / 'spark'
        cmd, env = session(ms, gen)
        feed = gen / 'blocks.txt'
        feed.write_text(''.join(f'@@QUERY {label}\n{blocks[label]}' for label in todo))
        started = time.monotonic()
        with open(feed) as fin, open(gen / 'spark.err', 'w') as ferr, open(part, 'a') as fpart:
            proc = subprocess.Popen(cmd + files, stdin=fin, stdout=subprocess.PIPE, stderr=ferr,
                                    env=env, text=True)
            for line in proc.stdout:
                kind, _, rest = line.rstrip('\n').partition(' ')
                label, _, value = rest.partition('\t')
                if kind == '@@ANSWER':
                    answers[label] = value.strip()
                    fpart.write(f'{label}\t{block_id[label]}\t{answers[label]}\n')
                    fpart.flush()
                    os.fsync(fpart.fileno())
                elif kind == '@@ERROR':
                    errors[label] = value.strip()
                else:
                    continue
                print(f'{label} {kind[2:].lower()} at {time.monotonic() - started:.0f} s',
                      flush=True)
            status = proc.wait()
        if status != 0:
            # SLF4J's notices about a missing logger binding are not the failure; the rest is
            err = '\n'.join(l for l in (gen / 'spark.err').read_text().splitlines()
                            if not l.startswith('SLF4J'))
            print(f'Spark exited with status {status}: {err[-1500:]}', file=sys.stderr)

    agree = total = 0
    with open(out, 'w', newline='') as f:
        wr = csv.writer(f, lineterminator='\n')
        wr.writerow(['window', 'query', 'answer', 'l0_answer', 'matches_l0', 'error'])
        for win in a.windows:
            for q in a.queries:
                label = f'{win} {q}'
                answer = answers.get(label, '')
                ref = truth.get((q, win), '')
                match = str(same(answer, ref)) if ref and label in answers else ''
                total += 1
                agree += match == 'True'
                wr.writerow([win, q, answer, ref, match, errors.get(label, '')])
                shown = answer if label in answers else 'ERROR ' + errors.get(label, '')[:200]
                print(f'{win} {q}: {shown}   L0 {ref or "-"}', flush=True)
    if a.answers:
        print(f'{agree} of {total} answers equal L0\'s', flush=True)
    if all(label in answers for label in blocks):
        part.unlink()


if __name__ == '__main__':
    main()
