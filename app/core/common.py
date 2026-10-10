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

def parse_file_convention(filename, pattern=r'(\d+)wk(\d{2})'):
    # parses file convention expecting YYYYwkWW as 2026wk01
    pattern = re.compile(pattern,re.IGNORECASE)
    m = re.search(pattern, filename)
    if m:
        yr, wk = m.group(1), m.group(2)
        return yr, wk
    else:
        raise ValueError("Filename does not match expected pattern")


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
