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
    db_path = plant_db(plant)
    if not db_path.exists():
        raise ValueError(f"No database found for plant '{plant}'")
    conn = sqlite3.connect(db_path)
    wf = filter_SQL(conn, table='waterfall_agg')
    cf = filter_SQL(conn, table='consumption_agg')
    conn.close()
    return wf, cf

def merged_data(plant):
    wf, cf = get_wf_cf(plant)
    return wf.merge(cf, on=['part', pre['oi']], how='inner')


def plant_stats(plant):
    """Return summary stats for a plant's database, or None if it has no
    database yet: weeks of data loaded for waterfall/consumption, unique
    parts seen in each, and how many of those parts have cost data.

    Consumption figures come from `consumption_agg` — the closed, fully
    aggregated weeks. `weeks_pending` counts weeks still sitting in the
    `consumption` landing table, i.e. partial weeks waiting on the rest of
    their transactions before they can be totalled."""
    db_path = plant_db(plant)
    if not db_path.exists():
        return None

    conn = sqlite3.connect(db_path)
    try:
        def scalar(query):
            row = conn.execute(query).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        return {
            # Read weeks directly off the data tables rather than loaded_files,
            # so the counts hold for databases built by the legacy .prn loaders
            # too (those predate per-week loaded_files bookkeeping).
            'weeks_waterfall':   scalar("SELECT COUNT(DISTINCT predidx) FROM waterfall_agg"),
            'weeks_consumption': scalar("SELECT COUNT(DISTINCT orderidx) FROM consumption_agg"),
            'weeks_pending':     scalar("SELECT COUNT(DISTINCT orderidx) FROM consumption"),
            'parts_waterfall':   scalar("SELECT COUNT(DISTINCT part) FROM waterfall_agg"),
            'parts_consumption': scalar("SELECT COUNT(DISTINCT part) FROM consumption_agg"),
            'parts_with_cost':   scalar("""
                SELECT COUNT(DISTINCT part) FROM (
                    SELECT part FROM waterfall_agg
                    UNION
                    SELECT part FROM consumption_agg
                ) AS all_parts
                WHERE part IN (SELECT DISTINCT part FROM cost)
            """),
        }
    finally:
        conn.close()


def get_coverage_counts(plant, wf_week='orderidx'):
    """Return (wf_records, cf_records) — distinct part counts per week for each table.

    Consumption is always counted per orderidx. Waterfall defaults to orderidx too
    (the Load mosaic); pass wf_week='predidx' to count per snapshot instead, which
    is how waterfall weeks are excluded from training.
    """
    if wf_week not in ('orderidx', 'predidx'):
        raise ValueError(f"wf_week must be 'orderidx' or 'predidx', not {wf_week!r}")
    plant_db = plant.strip().lower() + '.db'
    try:
        #dirs['processed'].mkdir(parents=True, exist_ok=True)
        print(dirs['processed'] / plant_db)
        conn = sqlite3.connect(dirs['processed'] / plant_db)
        wf = pd.read_sql_query(
            f"SELECT {wf_week} AS orderidx, COUNT(DISTINCT part) AS count "
            f"FROM waterfall_agg GROUP BY {wf_week} ORDER BY {wf_week}",
            conn,
        )
        cf = pd.read_sql_query(
            "SELECT orderidx, COUNT(DISTINCT part) AS count "
            "FROM consumption_agg GROUP BY orderidx ORDER BY orderidx",
            conn,
        )
        conn.close()
    except Exception:
        return [], []

    def to_records(df):
        return [{"idx": int(r["orderidx"]), "count": int(r["count"])} for _, r in df.iterrows()]

    return to_records(wf), to_records(cf)
