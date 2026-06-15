import seaborn as sns
import matplotlib.pyplot as plt
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
