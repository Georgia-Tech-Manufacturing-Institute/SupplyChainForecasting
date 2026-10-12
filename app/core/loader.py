import os
import re
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


# Columns a pipe-delimited extract must carry to be read as that data type, and
# which of them names the originating plant site (e.g. AT00).
EXTRACT_HEADER = {
    'consumption': ['Site', 'Item Number', 'Trans Date', 'Trans Number',
                    'Trans Type', 'Trans Qty'],
    'waterfall':   ['Ship-From', 'Rel Date', 'ShipTo', 'Part', 'PCR'],
}
EXTRACT_SITE_COLUMN = {'consumption': 'Site', 'waterfall': 'Ship-From'}


def read_extract_header(path):
    '''Column names on the first line of a pipe-delimited file, or None when
    the file is not text or carries no pipes.'''
    try:
        with open(path, encoding='utf-8-sig', errors='strict') as f:
            first = f.readline()
    except (OSError, UnicodeDecodeError):
        return None
    if '|' not in first:
        return None
    return [c.strip() for c in first.rstrip('\r\n').split('|')]


def is_extract(path, data_type):
    '''Whether `path` carries the header of a `data_type` extract. The header,
    not the filename, decides: drops are often renamed or date-stamped.'''
    header = read_extract_header(path)
    return header is not None and set(EXTRACT_HEADER[data_type]) <= set(header)


def check_extract_site(path, data_type, plant):
    '''
    Raises ValueError unless every row's site column (Site for ictrans,
    Ship-From for custdemand) contains `plant`'s key -- e.g. AT00 for AT, ICU3 for CU3.
    Without this, one plant's extract could quietly land in another plant's
    database now that the filename prefix is no longer enforced.

    Skipped when the plant has no plant_key entry, since there is nothing to
    compare against.
    '''
    key = plant_extract_key(plant) if plant is not None else None
    if key is None:
        return
    col = EXTRACT_SITE_COLUMN[data_type]
    sites = pd.read_csv(path, sep='|', usecols=lambda c: c.strip() == col,
                        dtype=str).iloc[:, 0].dropna().str.strip()
    foreign = sorted(s for s in sites.unique() if key.upper() not in s.upper())
    if foreign:
        raise ValueError(f"{col} {foreign} does not match plant key {key!r} "
                         f"for {plant}")


def find_extract(folder, data_type, plant=None):
    '''
    The `data_type` extract in `folder`, recognised by header rather than name.
    The conventional {key}_ictrans/_custdemand.txt wins when it is present and
    well-formed; otherwise the most recently modified file with the right
    header is used. None when the folder holds no such file.
    '''
    folder = Path(folder)
    if not folder.is_dir():
        return None
    try:
        conventional = folder / extract_filename(data_type, plant)
        if conventional.is_file() and is_extract(conventional, data_type):
            return conventional
    except KeyError:
        pass  # no plant_key entry, so no conventional name to prefer
    candidates = [p for p in folder.iterdir()
                  if p.is_file() and is_extract(p, data_type)]
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def classify_source(path, data_type, plant=None):
    '''
    Which of the two accepted shapes `path` is:

      'extract' -- a pipe-delimited ictrans/custdemand extract, recognised by
                   its header rather than its name. It carries its own dates
                   and can span many weeks. Its site column is checked against
                   the plant separately (check_extract_site).
      'weekly'  -- a legacy YYYYwkWW report, one week per file, the week taken
                   from the filename.
      None      -- neither, and the caller should skip it.
    '''
    if is_extract(path, data_type):
        return 'extract'
    try:
        parse_file_convention(Path(path).name.lower())
    except ValueError:
        return None
    return 'weekly'


def describe_expected(data_type, plant):
    '''The shapes a load accepts, for skip/error messages.'''
    header = '|'.join(EXTRACT_HEADER[data_type])
    return f'a pipe-delimited extract with columns {header}, or the YYYYwkWW convention'


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
    path = find_extract(folder, 'waterfall', plant)
    if path is None:
        print(f"     Skipping waterfall; no custdemand extract in {folder}")
        return None, 0
    check_extract_site(path, 'waterfall', plant)
    return path.name, load_custdemand(conn, path)


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
    (consumption_to_sql). Returns (inserted_rows, aggregated_week_indices,
    landed_part_count).
    '''
    oq = pre['oq']
    file = Path(file)
    print(f"Consumption ingest: {file.name}")
    raw = process_ictrans(file=file)
    if len(raw) == 0:
        print("     No ISS-SO transactions found")
        return 0, [], 0

    closed = closed_weeks(conn, 'consumption')
    if closed and not overwrite:
        already = raw[raw[oi].isin(closed)]
        if len(already) > 0:
            weeks = sorted(already[oi].unique())
            print(f"     Skipping {len(already)} rows from {len(weeks)} already-aggregated "
                  f"week(s) {weeks[0]}-{weeks[-1]}; suppress with overwrite=True")
            raw = raw[~raw[oi].isin(closed)]

    inserted = 0
    parts = raw['part'].nunique()
    if len(raw) > 0:
        rows = raw[['trans_number', 'part', 'orderdate', oq, oi]].copy()
        rows['orderdate'] = rows['orderdate'].dt.strftime('%Y-%m-%d')
        inserted = load_to_sql(conn, 'consumption', rows)
        print(f"     Landed {inserted} new transaction rows "
              f"({len(rows) - inserted} already present)")

    aggregated = aggregate_consumption(conn, file.name)
    return inserted, aggregated, parts


def autoConsumption(conn, folder, plant, overwrite=False):
    '''
    Weekly ingest of the raw transaction extract: parses {plant}_ictrans.txt
    out of `folder` and lands it through the consumption landing table.

    Returns (source_filename, inserted_rows, aggregated_week_indices), or
    (None, 0, []) when the plant has no extract in `folder`.
    '''
    path = find_extract(folder, 'consumption', plant)
    if path is None:
        print(f"     Skipping consumption; no ictrans extract in {folder}")
        return None, 0, []

    check_extract_site(path, 'consumption', plant)
    inserted, aggregated, _ = load_ictrans(conn, path, overwrite)
    return path.name, inserted, aggregated

def complete_weeks(conn):
    '''Week indices in the landing table that can no longer grow: either every
    calendar day is represented, or data exists for a later week. The second
    rule matters because a plant that does not ship on Sundays (or holidays)
    never produces all DAYS_IN_ISO_WEEK dates -- once the extracts have moved
    past a week, its missing days are genuinely zero, not still to come.'''
    latest = conn.execute("SELECT MAX(orderidx) FROM consumption").fetchone()[0]
    if latest is None:
        return []
    latest = max([int(latest), *closed_weeks(conn, 'consumption')])
    rows = conn.execute(
        """SELECT orderidx FROM consumption
           GROUP BY orderidx
           HAVING COUNT(DISTINCT orderdate) >= ? OR orderidx < ?
           ORDER BY orderidx""", (DAYS_IN_ISO_WEEK, latest)).fetchall()
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

def file_result(name, loaded, detail, parts=0):
    '''One file's outcome from a bulk load: whether it landed, why or why not,
    and how many distinct parts it carried. The Load page lists these.'''
    return {'file': name, 'loaded': loaded, 'detail': detail, 'parts': int(parts)}


def consumption_to_sql(conn, source, plant='arlington', overwrite=False):
    '''
    One-off consumption load, for a single file or a whole directory.

    `source` may point at either shape, and a directory may mix them:

      * an ictrans extract (any name, recognised by its header) -- the
        transaction-level extract. Goes through the
        landing table exactly as the weekly routine does, so a week is only
        totalled once all seven of its days are present.
      * YYYYwkWW.prn -- the legacy pre-aggregated weekly report. Already one
        row per part per week, so it bypasses the landing table and goes
        straight into consumption_agg, with its week read from the filename.

    Returns one file_result per file considered.
    '''
    results = []
    closed = closed_weeks(conn, 'consumption')

    for path in resolve_sources(source):
        shape = classify_source(path, 'consumption', plant)
        if shape is None:
            reason = f"name not recognised; expected {describe_expected('consumption', plant)}"
            print(f"     Skipping {path.name}; {reason}")
            results.append(file_result(path.name, False, reason))
            continue

        try:
            if shape == 'extract':
                check_extract_site(path, 'consumption', plant)
                inserted, aggregated, parts = load_ictrans(conn, path, overwrite)
                closed.update(aggregated)
                if parts == 0:
                    results.append(file_result(path.name, False,
                        'no new ISS-SO transactions (none in file, or all its weeks already loaded)'))
                    continue
                detail = f'{inserted} new transaction rows'
                if aggregated:
                    detail += f'; {len(aggregated)} complete week(s) aggregated'
                results.append(file_result(path.name, True, detail, parts))
                continue

            yr, wk = parse_file_convention(path.name)
            idx = week_to_idx(yr, wk)
            if idx in closed and not overwrite:
                reason = f'consumption for {yr}W{wk} already loaded'
                print(f"     Skipping {path.name}; {reason}; suppress this "
                      "behavior by setting overwrite=True")
                results.append(file_result(path.name, False, reason))
                continue
            agg = process_consumption_prn(path, yr, wk)
            print(f"     Processing: {path.name} as Yr{yr}Wk{wk}")

            if len(agg) == 0:
                print("     No usable part rows found")
                results.append(file_result(path.name, False, 'no usable part rows found'))
                continue

            d_rows = load_to_sql(conn, 'consumption_agg', agg)
            print(f"          Changed {d_rows} rows in consumption_agg")
            update_loaded_files(conn, path.name, 'consumption', idx)
            conn.commit()
            closed.add(idx)
            results.append(file_result(path.name, True, f'{yr}W{wk}; {d_rows} rows written',
                                       agg['part'].nunique()))
        except Exception as e:
            print(f"     Failed {path.name}: {e}")
            results.append(file_result(path.name, False, f'error: {e}'))

    return results

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
    path = find_extract(datadir, 'waterfall', plant)
    if path is None:
        raise FileNotFoundError(f"No custdemand extract in {datadir}")
    check_extract_site(path, 'waterfall', plant)
    return load_custdemand(conn, path)


def waterfall_to_sql(conn, source, plant='arlington', overwrite=False):
    '''
    One-off waterfall load, for a single file or a whole directory.

    `source` may point at either shape, and a directory may mix them:

      * a custdemand extract (any name, recognised by its header) -- the
        pipe-delimited customer-demand extract,
        which carries release dates for several weeks at once.
      * YYYYwkWW.prn -- a legacy diamond report. Its rows carry their own
        release dates too, so the filename only identifies the file.

    Returns one file_result per file considered.
    '''
    print("Bulk waterfall processing: ")
    results = []

    for path in resolve_sources(source):
        shape = classify_source(path, 'waterfall', plant)
        if shape is None:
            reason = f"name not recognised; expected {describe_expected('waterfall', plant)}"
            print(f"     Skipping {path.name}; {reason}")
            results.append(file_result(path.name, False, reason))
            continue

        try:
            # Parsed here rather than through load_custdemand/load_waterfall_prn
            # so the part count is on hand for the report.
            if shape == 'extract':
                check_extract_site(path, 'waterfall', plant)
                print(f"Waterfall ingest: {path.name}")
                wf = custDemandParse(file=path)
            else:
                if not overwrite and already_loaded(conn, path.name, 'waterfall'):
                    print(f"     Skipping {path.name}; already loaded")
                    results.append(file_result(path.name, False, 'already loaded'))
                    continue
                print(f"     Parsing {path.name}")
                wf = waterfallProcess(waterfallParse(path))

            if len(wf) == 0:
                print("     No demand found")
                results.append(file_result(path.name, False, 'no demand rows found'))
                continue
            rows = land_waterfall(conn, wf, path.name)
            detail = (f'{rows} new waterfall rows' if rows
                      else 'no new rows (all already loaded)')
            results.append(file_result(path.name, True, detail, wf['part'].nunique()))
        except Exception as e:
            print(f"     Failed {path.name}: {e}")
            results.append(file_result(path.name, False, f'error: {e}'))

    return results

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

    Returns one file_result per file considered.
    '''
    results = []
    for path in resolve_sources(source):
        try:
            cost_map = costPrep(path)
            cost_map = cost_map.rename({'Amt1': 'Amount'}, axis=1)
            cost_map.Start = cost_map.Start.astype(str)
            rows = load_to_sql(conn, 'cost', cost_map)
        except Exception as e:
            print(f"     Failed {path.name}: {e}")
            results.append(file_result(path.name, False, f'error: {e}'))
            continue
        print(f"     Loaded {path.name}; changed {rows} rows in cost")
        results.append(file_result(path.name, True, f'{rows} new cost rows',
                                   cost_map['Part'].nunique()))
    return results



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
SCHEMA_VERSION = 2

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


# Every stored week-index column, as (table, column).
WEEK_IDX_COLUMNS = [('waterfall', 'orderidx'), ('waterfall', 'predidx'),
                    ('waterfall_agg', 'orderidx'), ('waterfall_agg', 'predidx'),
                    ('consumption', 'orderidx'), ('consumption_agg', 'orderidx'),
                    ('loaded_files', 'week_idx')]


def v1_idx_to_week(old):
    '''Decodes a pre-v2 week index (year*52 + week, week 1..52) to ISO
    (year, week). A 53-week year's W53 encoded to the same value as the next
    year's W01, so those values decode as W01 -- see v1_ambiguous_idx.'''
    year = (old - 1) // 52
    return year, old - 52 * year


def v1_ambiguous_idx(years):
    '''Pre-v2 values that stood for two different ISO weeks (year-W53 and
    next-year-W01), for each 53-week ISO year in `years`.'''
    return {y * 52 + 53 for y in years if date(y, 12, 28).isocalendar()[1] == 53}


def _migrate_v2_week_index(conn):
    '''
    v1 -> v2. Week indices change from year*52+week to ISO weeks since
    common.WEEK_EPOCH, which stays one-to-one across 53-week ISO years.

    Rows stored under an ambiguous v1 value (2026-W53 and 2027-W01 both became
    105405) are deleted, not remapped: the landing tables kept whichever week
    arrived first and waterfall_agg summed the two, so neither week can be
    recovered from them. Later extracts re-land those weeks correctly.
    '''
    cols = [(t, c) for t, c in WEEK_IDX_COLUMNS if table_exists(conn, t)]
    olds = set()
    for t, c in cols:
        olds |= {r[0] for r in conn.execute(
            f"SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL")}
    if not olds:
        return  # fresh or empty database
    if max(olds) < 50_000:
        return  # already v2 numbering (v1 values are all > 2000*52)

    years = range(v1_idx_to_week(min(olds))[0] - 1, v1_idx_to_week(max(olds))[0] + 1)
    ambiguous = v1_ambiguous_idx(years) & olds
    for t, c in cols:
        for old in ambiguous:
            n = conn.execute(f"DELETE FROM {t} WHERE {c} = ?", (old,)).rowcount
            if n:
                print(f"     v2 migration: dropped {n} {t} rows with ambiguous "
                      f"{c} {old} ({v1_idx_to_week(old)[0] - 1}-W53 / "
                      f"{v1_idx_to_week(old)[0]}-W01)")

    # Every v2 value is far below every v1 value, so updating in place can
    # never trip a UNIQUE key on a half-converted row.
    conn.execute("CREATE TEMP TABLE week_idx_map (old INT PRIMARY KEY, new INT)")
    conn.executemany("INSERT INTO week_idx_map VALUES (?, ?)",
                     [(o, week_to_idx(*v1_idx_to_week(o))) for o in olds - ambiguous])
    for t, c in cols:
        conn.execute(f"""UPDATE {t} SET {c} =
                         (SELECT new FROM week_idx_map WHERE old = {t}.{c})
                         WHERE {c} IS NOT NULL""")
    conn.execute("DROP TABLE week_idx_map")


# (version_introduced, migration) -- appended to, never reordered.
MIGRATIONS = [
    (1, _migrate_v1_consumption_landing),
    (2, _migrate_v2_week_index),
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