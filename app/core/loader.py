from fileinput import filename
import os
from app.core.parser import *
import pandas as pd
from datetime import date
from app.prefixe import pre, dirs, raw_dir, plant_db, Path
import sqlite3

def create_connection(plant='arlington'):
    dirs['processed'].mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(plant_db(plant))
    sql_setup(conn)
    return conn

def read_waterfall_week(year=None, week=None, idx=None, proc_dir=None):
    '''
    Loads a single week (Predidx) of processed waterfall data; uses year and week, otherwise uses idx
    '''
    if proc_dir is None:
        proc_dir = dirs['processed'] / 'Waterfall'
    if year is None or week is None:
        if idx is None:
            raise ValueError("Must provide either year and week, or idx")
        year, week = idx_to_week(idx)
    filename = f"{year}wk{week:02d}.parquet"
    filepath = proc_dir / filename
    if not filepath.exists():
        return None
    return pd.read_parquet(filepath)

def read_waterfall_span(idx_start=None, idx_end=None, proc_dir=None):
    ''' Loads a span of processed waterfall data based on prediction week (Predidx) ; 
        week is INCLUSIVE'''
    if proc_dir is None:
        proc_dir = dirs['processed'] / 'Waterfall'
    data = []
    for idx in range(idx_start, idx_end+1):
        yr, wk = idx_to_week(idx) 
        week_data = read_waterfall_week(year=yr, week=wk, proc_dir=proc_dir)
        if week_data is not None:
            data.append(week_data)
    wf = pd.concat(data, ignore_index=True)
    #wf = wf.drop_duplicates(subset=['Part', 'RelDate', 'Orderidx'])
    return wf


def filter_SQL(conn, idx_start=None, idx_end=None, table='waterfall'):        
    start_cond = f'predidx >= {idx_start}' if idx_start is not None else None
    end_cond = f'predidx <= {idx_end}' if idx_end is not None else None
    if start_cond or end_cond:
        where_clause = "WHERE " + " AND ".join(filter(None, [start_cond, end_cond]))
    else: 
        where_clause = ""
    query = f"SELECT * FROM {table} {where_clause}"
    return pd.read_sql_query(query, conn)


def check_table_row_totals(cursor,t):
    cursor.execute(f'SELECT COUNT(1) FROM "{t}";')
    return cursor.fetchone()

def list_all_tables_row_totals(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    for t in tables:
        print(t[0], check_table_row_totals(cursor, t=t[0]))

def list_column_names(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return columns

accepted_types = {'prn', 'txt'}
def parse_file_convention(filename, pattern=r'(\d+)wk(\d{2})'):
    pattern = re.compile(pattern,re.IGNORECASE)
    m = re.search(pattern, filename)
    if m:
        yr, wk = m.group(1), m.group(2)
        return yr, wk
    else:
        raise ValueError("Filename does not match expected pattern")

def validate_type(file):
    return any(str(file).endswith(at) for at in accepted_types)

def filter_disjoint(wf, consumption):
    # Filter out disjoint set of parts
    # If NOT disjoint, there is at least one case of a consumption/waterfall
    # that is, if a corresponding prediction is missing, its due to bad broadcasting
    # Should be used on big representative datasets 
    disjoint = pd.Index(wf.Part).symmetric_difference(pd.Index(consumption.Part))
    wf = wf[~wf.Part.isin(disjoint)].reset_index(drop=True)
    consumption = consumption[~consumption.Part.isin(disjoint)].reset_index(drop=True)
    return wf, consumption

def consumption_to_sql(conn, consump_dir, overwrite=False):
    # Todo check for file duplication based on loaded_files table
    consump_dir = Path(consump_dir)
    loaded = get_loaded_files(conn, filter_type='consumption')

    for filename in sorted(os.listdir(consump_dir)):    
        if not validate_type(filename): 
            print("     Skipping ", {filename}, " due to invalid data type")
            continue
        if (filename in list(loaded.filename)):
            print("     Skipping ", filename, " identical copy processed; suppress this behavior by setting overwrite=True")
            if not overwrite:
                continue
        try:
            year, week = parse_file_convention(filename)
        except ValueError as e:
            print("     Skipping ", filename)
            print("          ", e)
            continue
        idx = week_to_idx(year, week)
        if idx in list(loaded.week_idx):
            print(f"     Skipping {filename}; waterfall for {year}W{week} exists already; suppress this behavior by setting overwrite=True")
            continue        
        
        input_path = consump_dir / filename
        if validate_type(input_path):
            yr, wk = parse_file_convention(filename)
            test = consumpParse(input_path, year=yr, week=wk,
                                cols= ["Site", "Item Number", "Description", "UM",
                                        "Type", "Type1", "Quantity1", "Type2", "Quantity2"])
            print("     Processing: ", filename, f' as Yr{yr}Wk{wk}')
            filt = filter_partno(test, col='Part')
            if len(filt)>0:
                filt = filt.groupby(['Part', pre['oi']]).sum(numeric_only=True).reset_index()
                filt = filt.rename({'Quantity1': pre['oq']}, axis=1    )
                d_rows = load_to_sql(conn, 'consumption', filt)
                print(f"          Changed {d_rows} rows")
                widx = week_to_idx(yr, wk)
                update_loaded_files(conn, filename, 'consumption', widx)

def get_loaded_files(conn, filter_type=None):
    loaded = filter_SQL(conn, table='loaded_files')
    if filter_type:
        loaded = loaded[loaded['data_type']==filter_type]
    return loaded

def waterfall_to_sql(conn, waterfall_dir, overwrite=False):
    '''
    Loads raw waterfall data into processed table; naming convention 2025wk06
    skips rows that are not unique
    Todo check for file duplication based on loaded_files table
    '''
    print("Bulk waterfall processing: ")
    # Pull the 
    loaded = get_loaded_files(conn, filter_type='waterfall')

    for filename in sorted(os.listdir(waterfall_dir)):    
        if not validate_type(filename): 
            print("     Skipping ", {filename}, " due to invalid data type")
            continue
        if (filename in list(loaded.filename)):
            print("     Skipping ", filename, " identical copy processed; suppress this behavior by setting overwrite=True")
            if not overwrite:
                continue
        try:
            year, week = parse_file_convention(filename)
        except ValueError as e:
            print("     Skipping ", filename)
            print("          ", e)
            continue
        idx = week_to_idx(year, week)
        if idx in list(loaded.week_idx):
            print(f"     Skipping {filename}; waterfall for {year}W{week} exists already; suppress this behavior by setting overwrite=True")
            continue

        input_path = waterfall_dir / filename
        print("     Parsing ", filename)
        parsed = waterfallParse(input_path)
        wf = waterfallProcess(parsed)
        wf["RelDate"] = wf["RelDate"].astype(str)
        d_rows = load_to_sql(conn, 'waterfall', 
                                wf.rename({pre['pq']: 'qty'}, axis=1))
        print(f"          Changed {d_rows} rows in waterfall")
        wf_agg = wf.groupby(['Part', pre['oi'], pre['pi']], 
                            as_index=True).agg({pre['pq']: 'sum',
                                                'PCR':'sum'}).reset_index()
        widx = week_to_idx(year, week)
        update_loaded_files(conn, filename, 'waterfall', widx)
        d_rows = load_to_sql(conn, 'waterfall_agg', wf_agg)
        print(f"          Changed {d_rows} rows in waterfall_agg")

def update_loaded_files(conn, filename, ftype, widx, conflict='IGNORE'):
    ''' keeps log of week info for loaded files
    conn: sqlite3 connection
    week: week index of file loaded based on week predictions/consumption is given
    ftype: waterfall or consumption '''
    if ftype not in ['waterfall', 'consumption']:
        raise ValueError('Please give ftype as waterfall or consumption')
    if not isinstance(widx, int):
        widx = int(widx)
    conn.execute(
    f""" INSERT OR {conflict} INTO loaded_files
        (filename, data_type, week_idx, load_date) VALUES (?, ?, ?, ?)""",
    (filename, ftype, widx, str(date.today().isoformat()), ))


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


def sql_setup(conn):
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS consumption (
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


def main(plant='Arlington'):
    conn = create_connection(plant)
    waterfall_to_sql(conn, waterfall_dir=raw_dir(plant, 'Waterfall'))
    consumption_to_sql(conn, consump_dir=raw_dir(plant, 'Consumption'))
    cost_to_sql(conn, cost_dir=raw_dir(plant, 'Cost'))
    list_all_tables_row_totals(conn)
    conn.close()


if __name__ == '__main__':
    main()