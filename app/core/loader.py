import os
from app.core.parser import *
import pandas as pd
from datetime import date
from app.core.common import *
from app.prefixe import pre, dirs, raw_dir, plant_db, Path
import sqlite3


LOADED_FILE_TYPES = {'waterfall', 'consumption', 'cost'}

# Tail of the pipe-delimited extract each data type arrives in. Matched on the
# suffix alone so the plant's key prefix (AT_, CU3_, 485_, ...) does not matter.
EXTRACT_SUFFIX = {'consumption': '_ictrans.txt',
                  'waterfall':   '_custdemand.txt'}

oi = pre['oi']
pi = pre['pi']


def create_connection(plant='arlington'):
    dirs['processed'].mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(plant_db(plant))
    sql_setup(conn)
    return conn


def resolve_sources(source):
    '''
    Expands `source` into the list of files to load. `source` may be either a
    single file or a directory -- this is what lets the one-off loaders serve
    both a hand-picked file and a whole drop folder.

    A directory is filtered down to the accepted extensions; a file named
    outright is only checked for its extension, so an explicit single-file
    load is never silently skipped.
    '''
    source = Path(source)
    if source.is_file():
        if not validate_type(source.name):
            raise ValueError(f"{source.name}: expected one of "
                             f"{sorted(accepted_types)}")
        return [source]
    if source.is_dir():
        return sorted(p for p in source.iterdir()
                      if p.is_file() and validate_type(p.name))
    raise FileNotFoundError(f"No such file or directory: {source}")


def classify_source(path, data_type):
    '''
    Which of the two accepted shapes `path` is:

      'extract' -- the pipe-delimited {key}_ictrans/_custdemand extract, which
                   carries its own dates and can span many weeks.
      'weekly'  -- a legacy YYYYwkWW report, one week per file, the week taken
                   from the filename.
      None      -- the name matches neither and the caller should skip it.
    '''
    name = Path(path).name.lower()
    if name.endswith(EXTRACT_SUFFIX[data_type]):
        return 'extract'
    try:
        parse_file_convention(name)
    except ValueError:
        return None
    return 'weekly'


def closed_weeks(conn, data_type):
    '''Week indices already recorded as loaded for `data_type`.'''
    loaded = get_loaded_files(conn, filter_type=data_type)
    return {int(w) for w in loaded.week_idx}


def filter_disjoint(wf, consumption):
    # Filter out disjoint set of parts
    # If NOT disjoint, there is at least one case of a consumption/waterfall
    # that is, if a corresponding prediction is missing, its due to bad broadcasting
    # Should be used on big representative datasets 
    disjoint = pd.Index(wf.Part).symmetric_difference(pd.Index(consumption.Part))
    wf = wf[~wf.Part.isin(disjoint)].reset_index(drop=True)
    consumption = consumption[~consumption.Part.isin(disjoint)].reset_index(drop=True)
    return wf, consumption

def land_waterfall(conn, wf, source_filename, overwrite=False):
    '''
    Folds already-parsed waterfall rows into the waterfall tables, skipping
    prediction weeks that loaded_files already records.

    Re-loading is harmless either way -- both waterfall tables are keyed
    UNIQUE and inserted with OR IGNORE -- so the skip is there to save work,
    and overwrite=True turns it off.

    Returns the number of rows written to waterfall_agg.
    '''
    if len(wf) == 0:
        print("     No demand found")
        return 0

    closed = closed_weeks(conn, 'waterfall')
    if closed and not overwrite:
        already = wf[wf[pi].isin(closed)]
        if len(already) > 0:
            weeks = sorted(already[pi].unique())
            print(f"     Skipping {len(already)} rows from {len(weeks)} already-loaded "
                  f"prediction week(s) {weeks[0]}-{weeks[-1]}; suppress with overwrite=True")
            wf = wf[~wf[pi].isin(closed)]

    if len(wf) == 0:
        print("     Nothing new to load")
        return 0
    return aggregate_waterfall(conn, wf, source_filename)


def load_custdemand(conn, file, overwrite=False):
    '''Loads one pipe-delimited customer-demand extract. Shared by the weekly
    routine (autoWaterfall) and the one-off loader (waterfall_to_sql).'''
    file = Path(file)
    print(f"Waterfall ingest: {file.name}")
    return land_waterfall(conn, custDemandParse(file=file), file.name, overwrite)


def load_waterfall_prn(conn, file, overwrite=False):
    '''Loads one legacy diamond report (2026wk09.prn) into the waterfall
    tables. The report carries its own release dates, so unlike the legacy
    consumption report the week is read from the rows, not the filename.'''
    file = Path(file)
    print(f"     Parsing {file.name}")
    wf = waterfallProcess(waterfallParse(file))
    return land_waterfall(conn, wf, file.name, overwrite)


def autoWaterfall(conn, folder, plant, overwrite=False):
    '''
    Weekly ingest of the raw customer-demand extract: parses
    {plant}_custdemand.txt out of `folder` and lands it in waterfall /
    waterfall_agg.

    Returns (source_filename, rows_written), or (None, 0) when the plant has
    no extract in `folder`.
    '''
    folder = Path(folder)
    plant_file = custDemand_filename(plant)
    if not (folder / plant_file).exists():
        print(f"     Skipping waterfall; no {plant_file} in {folder}")
        return None, 0
    return plant_file, load_custdemand(conn, folder / plant_file, overwrite)


def load_ictrans(conn, file, overwrite=False):
    '''
    Lands one transaction-level extract:

    1. Parse the extract with process_ictrans.
    2. Drop any week already recorded as loaded in loaded_files -- those weeks
       are closed and their totals are already in consumption_agg.
    3. Insert what remains into the `consumption` landing table. Duplicate
       transactions are rejected by the trans_number primary key, so an extract
       that overlaps last week's is harmless even if step 2 lets it through.
    4. Aggregate whichever weeks are now complete (see aggregate_consumption).

    Shared by the weekly routine (autoConsumption) and the one-off loader
    (consumption_to_sql). Returns (inserted_rows, aggregated_week_indices).
    '''
    oq = pre['oq']
    file = Path(file)
    print(f"Consumption ingest: {file.name}")
    raw = process_ictrans(file=file)
    if len(raw) == 0:
        print("     No ISS-SO transactions found")
        return 0, []

    closed = closed_weeks(conn, 'consumption')
    if closed and not overwrite:
        already = raw[raw[oi].isin(closed)]
        if len(already) > 0:
            weeks = sorted(already[oi].unique())
            print(f"     Skipping {len(already)} rows from {len(weeks)} already-aggregated "
                  f"week(s) {weeks[0]}-{weeks[-1]}; suppress with overwrite=True")
            raw = raw[~raw[oi].isin(closed)]

    inserted = 0
    if len(raw) > 0:
        rows = raw[['trans_number', 'part', 'orderdate', oq, oi]].copy()
        rows['orderdate'] = rows['orderdate'].dt.strftime('%Y-%m-%d')
        inserted = load_to_sql(conn, 'consumption', rows)
        print(f"     Landed {inserted} new transaction rows "
              f"({len(rows) - inserted} already present)")

    aggregated = aggregate_consumption(conn, file.name)
    return inserted, aggregated


def autoConsumption(conn, folder, plant, overwrite=False):
    '''
    Weekly ingest of the raw transaction extract: parses {plant}_ictrans.txt
    out of `folder` and lands it through the consumption landing table.

    Returns (source_filename, inserted_rows, aggregated_week_indices), or
    (None, 0, []) when the plant has no extract in `folder`.
    '''
    folder = Path(folder)
    plant_file = ictrans_filename(plant)
    if not (folder / plant_file).exists():
        print(f"     Skipping consumption; no {plant_file} in {folder}")
        return None, 0, []

    inserted, aggregated = load_ictrans(conn, folder / plant_file, overwrite)
    return plant_file, inserted, aggregated

def complete_weeks(conn):
    '''Week indices in the landing table whose every calendar day is
    represented. An ISO week has exactly DAYS_IN_ISO_WEEK dates, so a full
    count of distinct orderdates means the week can no longer grow.'''
    rows = conn.execute(
        """SELECT orderidx FROM consumption
           GROUP BY orderidx
           HAVING COUNT(DISTINCT orderdate) >= ?
           ORDER BY orderidx""", (DAYS_IN_ISO_WEEK,)).fetchall()
    return [int(r[0]) for r in rows]

def aggregate_consumption(conn, source_filename):
    '''
    Sums each complete week out of the landing table into consumption_agg,
    clears those rows, and records the week in loaded_files so the next
    extract does not re-land them.

    Incomplete weeks are left alone: they stay in `consumption` and are topped
    up by subsequent extracts until all seven days are present.
    '''
    weeks = complete_weeks(conn)
    if not weeks:
        print("     No complete weeks ready to aggregate")
        return []

    for widx in weeks:
        # Additive on conflict: rows are deleted from the landing table once
        # summed, so anything arriving for an already-aggregated week is new
        # volume rather than a re-count of what was already totalled.
        conn.execute("""
            INSERT INTO consumption_agg (part, orderidx, orderqty)
            SELECT part, orderidx, SUM(orderqty)
            FROM consumption WHERE orderidx = ?
            GROUP BY part, orderidx
            ON CONFLICT(part, orderidx)
            DO UPDATE SET orderqty = orderqty + excluded.orderqty """, (widx,))
        conn.execute("DELETE FROM consumption WHERE orderidx = ?", (widx,))
        update_loaded_files(conn, source_filename, 'consumption', widx)
    conn.commit()
    print(f"     Aggregated {len(weeks)} complete week(s): {weeks}")
    return weeks

def consumption_to_sql(conn, consump_dir, plant, overwrite=False):
    '''
    Legacy loader for the pre-aggregated weekly .prn reports (2025wk06.prn).
    These files are already one row per part per week, so they bypass the
    landing table and go straight into consumption_agg.
    '''
    consump_dir = Path(consump_dir)
    loaded = get_loaded_files(conn, filter_type='consumption')

    try: 
        raw = process_ictrans(consump_dir, preformat_plant(plant))
    except: 
        error

    idx = week_to_idx(year, week)
    if idx in list(loaded.week_idx):
        print(f"     Skipping {filename}; waterfall for {year}W{week} exists already; suppress this behavior by setting overwrite=True")
        continue        
    
    input_path = consump_dir / filename
    yr, wk = parse_file_convention(filename)
    test = consumpParse(input_path, year=yr, week=wk,
                        cols= ["Site", "Item Number", "Description", "UM",
                                "Type", "Type1", "Quantity1", "Type2", "Quantity2"])
    print("     Processing: ", filename, f' as Yr{yr}Wk{wk}')
    # consumpParse hands back the raw report columns; name the part column
    # and derive the week index from the filename before filtering.
    test = test.rename({'Item Number': 'Part'}, axis=1)
    test = test[test['Quantity1'].astype(str).str.strip() != '']
    filt = filter_partno(test, col='Part')
    if len(filt)>0:
        filt = filt.copy()
        filt[pre['oq']] = cast_to_into(filt['Quantity1']).abs()
        filt[pre['oi']] = idx
        filt = filt.groupby(['Part', pre['oi']], as_index=False)[pre['oq']].sum()
        d_rows = load_to_sql(conn, 'consumption_agg', filt)
        print(f"          Changed {d_rows} rows")
        update_loaded_files(conn, filename, 'consumption', idx)
        conn.commit()

def get_loaded_files(conn, filter_type=None):
    loaded = filter_SQL(conn, table='loaded_files')
    if filter_type:
        loaded = loaded[loaded['data_type']==filter_type]
    return loaded

def aggregate_waterfall(conn, wf, source_filename=None):
    wf["RelDate"] = wf["RelDate"].astype(str)
    d_rows = load_to_sql(conn, 'waterfall', wf)
    print(f"          Changed {d_rows} rows in waterfall")
    wf_agg = wf.groupby(['part', pre['oi'], pre['pi']],
                        as_index=True).agg({pre['pq']: 'sum',
                                            'PCR':'sum'}).reset_index()
    if source_filename is not None:
        widx = int(wf[pi].max())
        update_loaded_files(conn, source_filename, 'waterfall', widx)
    d_rows = load_to_sql(conn, 'waterfall_agg', wf_agg)
    conn.commit()
    return d_rows

def custDemand_to_sql(conn, plant, datadir):
    demand = custDemandParse(plant, datadir)
    d_rows = aggregate_waterfall(conn, demand,
                    source_filename=f'{plant_key[preformat_plant(plant)]}_custdemand.txt')
    print(f"          Changed {d_rows} rows in waterfall_agg")


def waterfall_to_sql(conn, filename, overwrite=False):
    '''
    Loads raw waterfall data into processed table; naming convention 2025wk06
    skips rows that are not unique
    Todo check for file duplication based on loaded_files table
    '''
    print("Bulk waterfall processing: ")
    # Pull the 
    loaded = get_loaded_files(conn, filter_type='waterfall')
    wf = custDemandParse(file=filename)
    wf["RelDate"] = wf["RelDate"].astype(str)
    d_rows = load_to_sql(conn, 'waterfall', wf.rename({pre['pq']: 'qty'}, axis=1))
    return d_rows

def update_loaded_files(conn, filename, ftype, widx, conflict='IGNORE'):
    ''' keeps log of week info for loaded files
    conn: sqlite3 connection
    week: week index of file loaded based on week predictions/consumption is given
    ftype: waterfall or consumption '''
    if ftype not in LOADED_FILE_TYPES:
        raise ValueError(f'Please give ftype as one of {sorted(LOADED_FILE_TYPES)}')
    if not isinstance(widx, int):
        widx = int(widx)
    conn.execute(
    f""" INSERT OR {conflict} INTO loaded_files
        (filename, data_type, week_idx, load_date) VALUES (?, ?, ?, ?)""",
    (filename, ftype, widx, str(date.today().isoformat()), ))
    conn.commit()


def cost_to_sql(conn, cost_dir):
    if cost_dir.exists():
        cost_map = costPrep(cost_dir)
        cost_map = cost_map.rename({'Amt1':'Amount'}, axis=1)
        cost_map.Start = cost_map.Start.astype(str)
        load_to_sql(conn, 'cost', cost_map)
    

def load_to_sql(conn, table, df):
    cur = conn.cursor()
    columns = [c.lower() for c in df.columns]
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({"?,"*(len(columns)-1) + "?" })"
    before = conn.total_changes
    cur.executemany(sql, df.itertuples(index=False, name=None))
    conn.commit()
    inserted = conn.total_changes - before
    return inserted


# Bump this whenever a migration is appended to MIGRATIONS below.
SCHEMA_VERSION = 1

# Number of distinct calendar dates an ISO week must show in the landing table
# before it is considered closed and safe to aggregate.
DAYS_IN_ISO_WEEK = 7


def table_exists(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _migrate_v1_consumption_landing(conn):
    '''
    v0 -> v1. `consumption` used to hold one pre-aggregated row per
    (part, orderidx). That is now `consumption_agg`, and `consumption` becomes
    the transaction-level landing zone fed by autoConsumption().

    Existing rows are moved, not dropped -- an untouched arlington.db carries
    ~119k of them and every report and model reads from them.
    '''
    if not table_exists(conn, 'consumption'):
        return  # fresh database; the CREATE statements below do the work
    if 'trans_number' in list_column_names(conn.cursor(), 'consumption'):
        return  # already the landing table

    if table_exists(conn, 'consumption_agg'):
        # Half-migrated (e.g. an earlier run died between the two statements):
        # fold the leftovers in rather than clobbering what is already there.
        conn.execute("""
            INSERT OR IGNORE INTO consumption_agg (part, orderidx, orderqty)
            SELECT part, orderidx, orderqty FROM consumption """)
        conn.execute("DROP TABLE consumption")
    else:
        conn.execute("ALTER TABLE consumption RENAME TO consumption_agg")


# (version_introduced, migration) -- appended to, never reordered.
MIGRATIONS = [
    (1, _migrate_v1_consumption_landing),
]


def migrate_schema(conn):
    '''Brings an existing database up to SCHEMA_VERSION before any
    CREATE TABLE IF NOT EXISTS runs, so that reshaped tables are actually
    reshaped instead of being silently left at their old definition.'''
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    for target, migration in MIGRATIONS:
        if version < target:
            migration(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def sql_setup(conn):
    #migrate_schema(conn)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS waterfall (
        part TEXT,
        orderidx INT,
        reldate DATE,
        shipto TEXT,
        predidx INT, 
        qty INT,
        pcr INT,
        UNIQUE(part, orderidx, reldate, shipto)
    );  """) 
    conn.commit()
    # Landing zone for raw transaction rows. One row per source transaction;
    # trans_number is the source system's unique id, so re-loading an
    # overlapping extract is a no-op under INSERT OR IGNORE.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS consumption (
        trans_number TEXT PRIMARY KEY,
        part TEXT NOT NULL,
        orderdate DATE NOT NULL,
        orderqty INT,
        orderidx INT NOT NULL
    );  """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_consumption_orderidx
        ON consumption(orderidx); """)
    conn.commit()
    # Weekly totals. Rows land here only once their week is complete; this is
    # what every downstream reader (features, reports, datasets) consumes.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS consumption_agg (
        part TEXT,
        orderidx INT,
        orderqty INT,
        UNIQUE(part, orderidx)
    );  """)
    conn.commit()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cost (
        part TEXT,
        amount REAL,
        start DATE, 
        UNIQUE(part, start)
    );   """) 
    conn.commit()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS waterfall_agg (
        part TEXT,
        orderidx INT,
        predidx INT, 
        predqty INT,
        augmented BOOLEAN NOT NULL DEFAULT 0,
        pcr INT,
        UNIQUE(part, orderidx, predidx)
    );  """)
    conn.commit()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS loaded_files (
        filename TEXT,
        data_type TEXT,
        week_idx INT, 
        load_date date,
        UNIQUE(data_type, week_idx)
    ); """)
    conn.commit()

oi = pre['oi']
pi = pre['pi']

def augment_waterfall(wf, min_part_lookahead_to_pad=4, lookahead_padding=4):
    # wf: waterfall dataset. 
    df = wf.copy()
    df['lookahead_wks'] = df[oi] - df[pi]

    max_lookahead_by_part = df.groupby('part')['lookahead_wks'].max()
    qualifying_parts = max_lookahead_by_part[max_lookahead_by_part >= min_part_lookahead_to_pad].index
    subdf = df[df['part'].isin(qualifying_parts)]
    # Also ignore weeks where really last minute changes happened
    mask = (
        subdf.groupby(['part', oi])['lookahead_wks']
            .transform('max')  > min_part_lookahead_to_pad
    )

    subdf = subdf[mask]
    # Per part/order, determine the lookahead range to fill
    order_min_la = subdf.groupby(['part', oi])['lookahead_wks'].min().rename('order_min_la')
    order_min_la = np.maximum(order_min_la, min_part_lookahead_to_pad)
    part_target_max_la = (max_lookahead_by_part + lookahead_padding).rename('target_max_la')

    order_info = order_min_la.reset_index().join(part_target_max_la, on='part')

    order_info['la_range'] = order_info.apply(
        lambda r: list(range(int(r['order_min_la']) + 1, int(r['target_max_la']) + 1)), axis=1
    )
    expanded = order_info.explode('la_range').dropna(subset=['la_range'])
    expanded = expanded.rename(columns={'la_range': 'lookahead_wks'})
    expanded['lookahead_wks'] = expanded['lookahead_wks'].astype(int)
    expanded[pi] = expanded[oi] - expanded['lookahead_wks']
    expanded = expanded[expanded[pi] >= 0].copy()

    # Drop combos that already exist
    existing_keys = df.set_index(['part', oi, 'lookahead_wks']).index
    new_keys = expanded.set_index(['part', oi, 'lookahead_wks']).index
    expanded = expanded[~new_keys.isin(existing_keys)]

    # For each new row, find the most recent prior prediction:
    # i.e. the row in subdf with the same (part, oi) where subdf[pi] < new row's pi,
    # taking the one with the largest such pi (smallest lookahead).
    # We do this with a merge + filter + pick-closest.
    meta_cols = [c for c in subdf.columns if c not in [pi, 'lookahead_wks']]
    candidates = subdf[meta_cols + [pi, 'lookahead_wks']].copy()
    candidates = candidates.rename(columns={pi: 'src_pi', 'lookahead_wks': 'src_la'})

    # Cross-join on part + oi, then filter to prior predictions only
    aug = expanded.merge(candidates, on=['part', oi], how='left')
    aug = aug[aug['src_pi'] < aug[pi]]  # only predictions made before the new synthetic pi

    # Keep the most recent prior prediction (largest src_pi) per new row
    aug = (
        aug.sort_values('src_pi')
        .groupby(['part', oi, 'lookahead_wks'], as_index=False)
        .last() )

    # For (part, oi, lookahead) combos with no prior prediction, fall back to 0
    no_prior = expanded[
        ~expanded.set_index(['part', oi, 'lookahead_wks']).index.isin(
            aug.set_index(['part', oi, 'lookahead_wks']).index
        ) ].copy()
    qty_cols = [c for c in subdf.columns if c not in ['part', oi, pi, 'lookahead_wks']]
    for c in qty_cols:
        no_prior[c] = 0

    aug = pd.concat([aug, no_prior], ignore_index=True)
    aug = aug.drop(columns=['order_min_la', 'target_max_la', 'src_pi', 'src_la'], errors='ignore')
    aug = aug.reset_index(drop=True).drop('lookahead_wks',axis=1)
    return aug

def main(plant='Arlington'):
    conn = create_connection(plant)
    waterfall_to_sql(conn, waterfall_dir=raw_dir(plant, 'Waterfall'))
    # New transaction-level extract, then the legacy pre-aggregated .prn files.
    autoConsumption(conn, raw_dir(plant, 'Consumption'), plant)
    consumption_to_sql(conn, consump_dir=raw_dir(plant, 'Consumption'))
    cost_to_sql(conn, cost_dir=raw_dir(plant, 'Cost'))
    list_all_tables_row_totals(conn)
    conn.close()


if __name__ == '__main__':
    main()