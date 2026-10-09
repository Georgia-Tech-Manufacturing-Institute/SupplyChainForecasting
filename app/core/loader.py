import os
from app.core.parser import *
import pandas as pd
from datetime import date
from app.core.common import *
from app.prefixe import pre, dirs, raw_dir, plant_db, Path
import sqlite3


LOADED_FILE_TYPES = {'waterfall', 'consumption', 'cost'}

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


def classify_source(path, data_type, plant=None):
    '''
    Which of the two accepted shapes `path` is:

      'extract' -- the pipe-delimited {plant_key}_ictrans/_custdemand extract,
                   which carries its own dates and can span many weeks. It is
                   named for one plant, so when that plant's key is known the
                   prefix has to match it; otherwise one plant's extract could
                   quietly land in another plant's database.
      'weekly'  -- a legacy YYYYwkWW report, one week per file, the week taken
                   from the filename.
      None      -- the name matches neither and the caller should skip it.
    '''
    name = Path(path).name.lower()
    key = plant_extract_key(plant) if plant is not None else None
    if key is not None:
        if name == f'{key}{EXTRACT_SUFFIX[data_type]}'.lower():
            return 'extract'
    # No key configured for this plant, so there is nothing to match the prefix
    # against; accept any extract carrying the right suffix.
    elif name.endswith(EXTRACT_SUFFIX[data_type]):
        return 'extract'
    try:
        parse_file_convention(name)
    except ValueError:
        return None
    return 'weekly'


def describe_expected(data_type, plant):
    '''The filename shapes a load accepts, for skip/error messages.'''
    try:
        extract = extract_filename(data_type, plant)
    except KeyError:
        extract = f'*{EXTRACT_SUFFIX[data_type]}'
    return f'{extract} or the YYYYwkWW convention'


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

def already_loaded(conn, filename, data_type):
    '''Whether a file of this exact name is already recorded in loaded_files.'''
    loaded = get_loaded_files(conn, filter_type=data_type)
    return filename in set(loaded.filename)


def land_waterfall(conn, wf, source_filename):
    '''
    Folds already-parsed waterfall rows into the waterfall tables.

    Rows a previous report already carried are dropped by the database rather
    than filtered here: both waterfall tables are keyed UNIQUE and inserted
    with OR IGNORE, and two reports legitimately overlap on release weeks, so
    filtering by week would throw away whatever the newer report adds.

    Returns the number of rows written to waterfall_agg.
    '''
    if len(wf) == 0:
        print("     No demand found")
        return 0
    return aggregate_waterfall(conn, wf, source_filename)


def load_custdemand(conn, file):
    '''
    Loads one pipe-delimited customer-demand extract. Shared by the weekly
    routine (autoWaterfall) and the one-off loader (waterfall_to_sql).

    The extract carries the same name every week, so there is nothing in the
    name to skip on -- it is always re-read, and the UNIQUE keys absorb the
    overlap with last week's.
    '''
    file = Path(file)
    print(f"Waterfall ingest: {file.name}")
    return land_waterfall(conn, custDemandParse(file=file), file.name)


def load_waterfall_prn(conn, file, overwrite=False):
    '''
    Loads one legacy diamond report (2026wk09.prn) into the waterfall tables.
    The report carries its own release dates, so unlike the legacy consumption
    report the weeks are read from the rows, not the filename.

    Here the filename does identify the contents, so a file already recorded
    in loaded_files is skipped unless overwrite=True.
    '''
    file = Path(file)
    if not overwrite and already_loaded(conn, file.name, 'waterfall'):
        print(f"     Skipping {file.name}; already loaded; suppress this "
              "behavior by setting overwrite=True")
        return 0
    print(f"     Parsing {file.name}")
    return land_waterfall(conn, waterfallProcess(waterfallParse(file)), file.name)


def autoWaterfall(conn, folder, plant):
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
    return plant_file, load_custdemand(conn, folder / plant_file)


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

def consumption_to_sql(conn, source, plant='arlington', overwrite=False):
    '''
    One-off consumption load, for a single file or a whole directory.

    `source` may point at either shape, and a directory may mix them:

      * {key}_ictrans.txt -- the transaction-level extract. Goes through the
        landing table exactly as the weekly routine does, so a week is only
        totalled once all seven of its days are present.
      * YYYYwkWW.prn -- the legacy pre-aggregated weekly report. Already one
        row per part per week, so it bypasses the landing table and goes
        straight into consumption_agg, with its week read from the filename.

    Returns the list of filenames that were loaded.
    '''
    done = []
    closed = closed_weeks(conn, 'consumption')

    for path in resolve_sources(source):
        shape = classify_source(path, 'consumption', plant)
        if shape is None:
            print(f"     Skipping {path.name}; expected "
                  f"{describe_expected('consumption', plant)}")
            continue

        if shape == 'extract':
            _, aggregated = load_ictrans(conn, path, overwrite)
            closed.update(aggregated)
            done.append(path.name)
            continue
        else:
            yr, wk = parse_file_convention(path.name)
            idx = week_to_idx(yr, wk)
            if idx in closed and not overwrite:
                print(f"     Skipping {path.name}; consumption for {yr}W{wk} exists "
                    "already; suppress this behavior by setting overwrite=True")
                continue
            agg = process_consumption_prn(path, yr, wk)
        print(f"     Processing: {path.name} as Yr{yr}Wk{wk}")
        
        if len(agg) == 0:
            print("     No usable part rows found")
            continue

        d_rows = load_to_sql(conn, 'consumption_agg', agg)
        print(f"          Changed {d_rows} rows in consumption_agg")
        update_loaded_files(conn, path.name, 'consumption', idx)
        conn.commit()
        closed.add(idx)
        done.append(path.name)

    return done

def get_loaded_files(conn, filter_type=None):
    loaded = filter_SQL(conn, table='loaded_files')
    if filter_type:
        loaded = loaded[loaded['data_type']==filter_type]
    return loaded

def aggregate_waterfall(conn, wf, source_filename=None):
    '''
    Writes parsed waterfall rows to `waterfall`, then their per
    (part, orderidx, predidx) totals to `waterfall_agg`.

    Every prediction week present is recorded in loaded_files, so a later
    extract covering the same release weeks can skip them.

    Returns the number of rows written to waterfall_agg.
    '''
    wf = wf.copy()
    wf['reldate'] = wf['reldate'].astype(str)
    d_rows = load_to_sql(conn, 'waterfall',
                         wf.rename({pre['pq']: 'qty'}, axis=1))
    print(f"          Changed {d_rows} rows in waterfall")

    wf_agg = wf.groupby(['part', pre['oi'], pre['pi']],
                        as_index=False).agg({pre['pq']: 'sum',
                                            'pcr': 'sum'})
    a_rows = load_to_sql(conn, 'waterfall_agg', wf_agg)
    print(f"          Changed {a_rows} rows in waterfall_agg")

    if source_filename is not None:
        for widx in sorted(wf[pi].unique()):
            update_loaded_files(conn, source_filename, 'waterfall', int(widx))
    conn.commit()
    return a_rows

def custDemand_to_sql(conn, plant, datadir):
    '''Loads the customer-demand extract for `plant` out of `datadir`.
    Thin alias kept for callers that name the plant rather than the file.'''
    return load_custdemand(conn, Path(datadir) / custDemand_filename(plant))


def waterfall_to_sql(conn, source, plant='arlington', overwrite=False):
    '''
    One-off waterfall load, for a single file or a whole directory.

    `source` may point at either shape, and a directory may mix them:

      * {key}_custdemand.txt -- the pipe-delimited customer-demand extract,
        which carries release dates for several weeks at once.
      * YYYYwkWW.prn -- a legacy diamond report. Its rows carry their own
        release dates too, so the filename only identifies the file.

    Returns the list of filenames that were loaded.
    '''
    print("Bulk waterfall processing: ")
    done = []

    for path in resolve_sources(source):
        shape = classify_source(path, 'waterfall', plant)
        if shape is None:
            print(f"     Skipping {path.name}; expected "
                  f"{describe_expected('waterfall', plant)}")
            continue

        if shape == 'extract':
            load_custdemand(conn, path)
        else:
            load_waterfall_prn(conn, path, overwrite)
        done.append(path.name)

    return done

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


def cost_to_sql(conn, source):
    '''
    Loads price lists from a single file or a directory of them.

    Cost files are standalone lists rather than weekly reports -- costPrep
    already keeps only the latest Start per part -- so there is no week to
    record in loaded_files and no naming convention to satisfy.
    '''
    rows = 0
    for path in resolve_sources(source):
        cost_map = costPrep(path)
        cost_map = cost_map.rename({'Amt1': 'Amount'}, axis=1)
        cost_map.Start = cost_map.Start.astype(str)
        rows += load_to_sql(conn, 'cost', cost_map)
        print(f"     Loaded {path.name}")
    print(f"          Changed {rows} rows in cost")
    return rows



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
    migrate_schema(conn)
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
    '''Loads everything sitting in this plant's raw folders. The one-off
    loaders take both the transaction extracts and the legacy .prn reports,
    so each folder only needs one pass.'''
    conn = create_connection(plant)
    try:
        for data_type, loader in (('Waterfall', waterfall_to_sql),
                                  ('Consumption', consumption_to_sql)):
            folder = raw_dir(plant, data_type)
            if folder.is_dir():
                loader(conn, folder, plant)
            else:
                print(f"     Skipping {data_type}; no {folder}")

        cost_folder = raw_dir(plant, 'Cost')
        if cost_folder.is_dir():
            cost_to_sql(conn, cost_folder)

        list_all_tables_row_totals(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Load every raw file already sitting in a plant's data folders.")
    parser.add_argument('plant', nargs='?', default='Arlington',
                        help="Plant source, e.g. Arlington or Hermasillo.")
    main(parser.parse_args().plant)