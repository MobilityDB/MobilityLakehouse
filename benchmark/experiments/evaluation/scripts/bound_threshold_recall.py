# Re-run the count-query recall vs L0 for the rebuilt L3 variants
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

CLI = Path(os.getenv("LAKEHOUSE_ROOT")
           or Path(__file__).resolve().parents[3])
SCRATCH = Path(os.getenv("LAKEHOUSE_SCRATCH", tempfile.gettempdir()))
OLD_EXT = os.environ.get("MOBILITYDUCK_EXT_BASELINE", "")
DAY = '2026-01-15'

def v(name):
    return (SCRATCH / f'l3_variants/{name}/*.parquet').as_posix()

FAMILIES = {
    'unsynced': {
        'L0':          (CLI / f'data/trips/L0/L0/year=2026/month=01/day={DAY}.parquet').as_posix(),
        'L3_shipped':  (CLI / f'data/trips/layouts_daily/L3/day={DAY}/**/*.parquet').as_posix(),
        'L3_cur':      v('cur'),
        'L3_relax':    v('relax'),
        'L3_noguard':  v('noguard'),
        'L3_excl':     v('excl'),
    },
    'synced': {
        'L0':             (SCRATCH / 'l0_sync.parquet').as_posix(),
        'L3_sync':        v('sync'),
        'L3_sync_nomin':  v('sync_nomin'),
    },
}

# Both windows stay inside 2026-01-15 so the single rebuilt day is complete.
WINDOWS = {
    'hour':  ('2026-01-15 08:00:00', '2026-01-15 09:00:00', '2026-01-15', '2026-01-15'),
    'day15': ('2026-01-15 00:00:00', '2026-01-16 00:00:00', '2026-01-15', '2026-01-15'),
}

QUERIES = ['both_ports', 'clip_to_region', 'collision',
           'encounter_zone', 'fleet_summary', 'harbour_entry']

def statements(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(';') if s.strip()]

def main():
    os.chdir(CLI)
    con = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
    con.execute('INSTALL spatial; LOAD spatial;')
    con.execute(f"LOAD '{OLD_EXT}';")
    con.execute("SET TimeZone='UTC';")

    qs = {q: (CLI / f'queries/parquet/{q}.sql').read_text() for q in QUERIES}

    rows = []
    for fam, layouts in FAMILIES.items():
        for wname, (t0, t1, d0, d1) in WINDOWS.items():
            con.execute(f"SET VARIABLE t0 = TIMESTAMP '{t0}'")
            con.execute(f"SET VARIABLE t1 = TIMESTAMP '{t1}'")
            con.execute(f"SET VARIABLE d0 = DATE '{d0}'")
            con.execute(f"SET VARIABLE d1 = DATE '{d1}'")
            for lname, glob in layouts.items():
                con.execute(f"SET VARIABLE trips_glob = '{glob}'")
                for q, raw in qs.items():
                    ans = None
                    try:
                        for s in statements(raw):
                            r = con.execute(s).fetchall()
                            if r:
                                ans = r[0][0]
                    except Exception as e:
                        ans = f'ERR: {str(e)[:60]}'
                    rows.append({'family': fam, 'window': wname,
                                 'layout': lname, 'query': q, 'answer': ans})
                    print(f'  {fam:<9} {wname:<6} {lname:<15} {q:<16} {ans}', flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(SCRATCH / 'l3_recall_raw.csv', index=False)

    base = (df[df.layout == 'L0'][['family', 'window', 'query', 'answer']]
            .rename(columns={'answer': 'ans_L0'}))
    m = df[df.layout != 'L0'].merge(base, on=['family', 'window', 'query'])
    m['ans'] = pd.to_numeric(m['answer'], errors='coerce')
    m['ans_L0'] = pd.to_numeric(m['ans_L0'], errors='coerce')
    m['recall_pct'] = (100 * m['ans'] / m['ans_L0']).round(1)

    for fam in FAMILIES:
        d = df[df.family == fam]
        print(f'\n=== {fam}: answers (L0 = ground truth) ===')
        print(d.pivot_table(index=['window', 'query'], columns='layout',
                            values='answer', aggfunc='first').to_string())
        e = m[m.family == fam]
        print(f'\n=== {fam}: recall vs L0 (%) ===')
        print(e.pivot_table(index=['window', 'query'], columns='layout',
                            values='recall_pct', aggfunc='first').to_string())

    print('\n=== mean recall per layout (%) ===')
    print(m.groupby(['family', 'layout'])['recall_pct'].mean().round(2).to_string())
    m.to_csv(SCRATCH / 'l3_recall_table.csv', index=False)

if __name__ == '__main__':
    main()
