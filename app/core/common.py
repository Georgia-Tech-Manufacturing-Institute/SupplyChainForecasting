import re
from app.prefixe import pre, dirs
from datetime import datetime
import pandas as pd

# File extensions a raw data drop is allowed to use.
accepted_types = {'prn', 'txt'}

def idx_to_week(v):
    return (v // 52, v % 52)

def preformat_plant(s):
    return s.strip().lower()

def week_to_idx(year, week):
    try:
        return year.astype(int) * 52 + week.astype(int)
    except AttributeError:
        return int(year) * 52 + int(week)


def make_week_idx(df, on=pre['op'], yearcol=pre['ys'], weekcol=pre['ws']):
    return df[f"{on}{yearcol}"].astype(int) * 52 + df[f"{on}{weekcol}"].astype(int)

def cast_to_into(series, to_int=True):
    # give a column or pandas series
    floated =  series.str.replace(',', '').astype(float)
    return floated.astype(int) if to_int else floated


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

def parse_file_convention(filename, pattern=r'(\d+)wk(\d{2})'):
    pattern = re.compile(pattern,re.IGNORECASE)
    m = re.search(pattern, filename)
    if m:
        yr, wk = m.group(1), m.group(2)
        return yr, wk
    else:
        raise ValueError("Filename does not match expected pattern")


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

def filter_SQL(conn, idx_start=None, idx_end=None, table='waterfall_agg', aug=False):     
    # aug:  whether augmented rows only
    #   aug=True only augmented / aug=False only real / aug=None all data   
    conditions = []
    if idx_start is not None:
        conditions.append(f'predidx >= {idx_start}')
    if idx_end is not None:
        conditions.append(f'predidx <= {idx_end}')
    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions else ""
    )
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

def validate_type(file):
    '''True when `file` carries one of the accepted extensions (.prn/.txt).'''
    return str(file).rsplit('.', 1)[-1].lower() in accepted_types
