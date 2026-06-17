from pathlib import Path
from datetime import datetime
import pandas as pd

import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import sqlite3 

from app.core.loader import *


def filter_dates(df, min_date: datetime, max_date: datetime):
    min_idx = week_to_idx(min_date.year, min_date.isocalendar()[1])
    max_idx = week_to_idx(max_date.year, max_date.isocalendar()[1])
    return df[(df[pre['oi']] >= min_idx) & (df[pre['oi']] <= max_idx)]

def filter_lookahead(df, max_lookahead: int):
    # disqualify the results that are more than max_lookahead weeks in the future
    lookahead = df[pre['oi']] - df[pre['pi']]
    return df[lookahead <= max_lookahead]

def get_wf_cf(plant):
    plant = plant.strip().lower()+'.db'
    try:
        conn = sqlite3.connect(dirs['processed'] / plant)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            raise ValueError("Plant doesn't exist") from e
        raise e

    import sqlite3 as _sqlite3
    wf = filter_SQL(conn, table='waterfall_agg')
    cf = filter_SQL(conn, table='consumption')
    conn.close()
    return wf, cf

def merged_data(plant):
    wf, cf = get_wf_cf(plant)
    return wf.merge(cf, on=['part', pre['oi']], how='inner')


def get_coverage_counts(plant):
    """Return (wf_records, cf_records) — distinct part counts per orderidx for each table."""
    plant_db = plant.strip().lower() + '.db'
    try:
        dirs['processed'].mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(dirs['processed'] / plant_db)
        wf = pd.read_sql_query(
            "SELECT orderidx, COUNT(DISTINCT part) AS count "
            "FROM waterfall_agg GROUP BY orderidx ORDER BY orderidx",
            conn,
        )
        cf = pd.read_sql_query(
            "SELECT orderidx, COUNT(DISTINCT part) AS count "
            "FROM consumption GROUP BY orderidx ORDER BY orderidx",
            conn,
        )
        conn.close()
    except Exception:
        return [], []

    def to_records(df):
        return [{"idx": int(r["orderidx"]), "count": int(r["count"])} for _, r in df.iterrows()]

    return to_records(wf), to_records(cf)
