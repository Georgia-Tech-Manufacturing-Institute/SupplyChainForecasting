import re
from app.prefixe import pre, dirs
from datetime import datetime, date, timedelta
import numpy as np
import pandas as pd

# File extensions a raw data drop is allowed to use.
accepted_types = {'prn', 'txt'}

# Week indices count whole ISO weeks since this Monday (ISO 2000-W01), so
# consecutive weeks always differ by exactly 1 -- including across 53-week ISO
# years, which the old year*52+week scheme collapsed (2026-W53 == 2027-W01).
# Mirrored in the Load and Train templates' JS; change all three together.
WEEK_EPOCH = date(2000, 1, 3)
_EPOCH_DAY = (WEEK_EPOCH - date(1970, 1, 1)).days


def _iso_week1_monday(year):
    '''Days since 1970-01-01 of the Monday of ISO week 1 of `year`
    (the Monday on or before 4 January).'''
    jan4 = (np.asarray(year, dtype='int64') - 1970).astype('datetime64[Y]') \
               .astype('datetime64[D]').astype('int64') + 3
    return jan4 - (jan4 + 3) % 7            # 1970-01-01 was a Thursday


def week_to_idx(year, week):
    '''ISO (year, week) -> week index. Scalars give an int; arrays or Series
    give the same shape back. Raises on a week the ISO year does not have.'''
    y = np.asarray(year, dtype='int64')
    w = np.asarray(week, dtype='int64')
    start = _iso_week1_monday(y)
    n_weeks = (_iso_week1_monday(y + 1) - start) // 7
    if np.any((w < 1) | (w > n_weeks)):
        raise ValueError(f"ISO week out of range for its year: {year!r}-W{week!r}")
    idx = (start - _EPOCH_DAY) // 7 + w - 1
    for src in (year, week):
        if isinstance(src, pd.Series):
            return pd.Series(idx, index=src.index)
    return int(idx) if idx.ndim == 0 else idx


def idx_to_week(v):
    '''Week index -> ISO (year, week).'''
    iso = (WEEK_EPOCH + timedelta(weeks=int(v))).isocalendar()
    return (iso[0], iso[1])


def idx_iso_week(idx):
    '''Vectorised ISO week number (1-53) of each week index.'''
    thursday = _EPOCH_DAY + 7 * np.asarray(idx, dtype='int64') + 3
    jan1 = thursday.astype('datetime64[D]').astype('datetime64[Y]') \
                   .astype('datetime64[D]').astype('int64')
    woy = (thursday - jan1) // 7 + 1
    return pd.Series(woy, index=idx.index) if isinstance(idx, pd.Series) else woy


def preformat_plant(s):
    return s.strip().lower()


def make_week_idx(df, on=pre['op'], yearcol=pre['ys'], weekcol=pre['ws']):
    return week_to_idx(df[f"{on}{yearcol}"], df[f"{on}{weekcol}"])

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
