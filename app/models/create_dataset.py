import pandas as pd
import sqlite3

from app.prefixe import pre
from app.core.loader import filter_SQL
from app.core.parser import week_to_idx
from app.models.features import (
    waterfall_features, consumption_features, id_features
)

oi = pre['oi']
oq = pre['oq']
pq = pre['pq']


def build_training_dataset(conn, min_orderidx: int = None,
                           max_orderidx: int = None,
                           remove_nans: bool = True) -> pd.DataFrame:
    """
    Pull waterfall, consumption, and cost from conn and return a
    feature-ready DataFrame suitable for model_train().

    Parameters
    ----------
    conn : sqlite3.Connection
    min_orderidx : earliest order week index to include (default: earliest w/ consumption)
    max_orderidx : latest order week index to include (default: current week)
    remove_nans  : drop rows where orderqty is NaN (True); otherwise fill 0
    """
    wf   = filter_SQL(conn, table='waterfall_agg')
    cf   = filter_SQL(conn, table='consumption')
    cost = filter_SQL(conn, table='cost')

    waterfall   = waterfall_features(wf)
    consumption = consumption_features(cf)

    merged = (
        waterfall
        .merge(consumption, on=['part', oi], how='left')
        .sort_values(['predidx', 'part', oi])
    )

    lo = min_orderidx if min_orderidx is not None else cf[oi].min()
    hi = max_orderidx if max_orderidx is not None else wf[oi].max()

    real_data = merged[(merged[oi] < hi) & (merged[oi] >= lo)]

    if remove_nans:
        real_data = real_data[~real_data[oq].isna()]
    else:
        real_data = real_data.fillna(0)

    real_data = pd.concat(
        [real_data, id_features(real_data['part'], 8)], axis=1
    )

    # Use the most recent cost per part (cost table may have multiple start dates)
    latest_cost = (
        cost.sort_values('start')
        .groupby('part', as_index=False)
        .last()[['part', 'amount']]
    )
    real_data = real_data.merge(latest_cost, how='left', on='part')

    return real_data.reset_index(drop=True)


def build_prediction_dataset(conn) -> pd.DataFrame:
    """
    Build a feature-ready dataset for inference on the current waterfall.

    Uses the latest prediction week available, joins historical consumption
    features (last known per part), and cost.  orderqty will NOT be present
    since these are future orders.
    """
    wf   = filter_SQL(conn, table='waterfall_agg')
    cf   = filter_SQL(conn, table='consumption')
    cost = filter_SQL(conn, table='cost')

    waterfall   = waterfall_features(wf)
    consumption = consumption_features(cf)

    # For future orders, use the last known consumption stats per part
    # (there is no actual orderqty for orders not yet filled)
    consumption_no_qty = consumption.drop(columns=[oq], errors='ignore')
    last_consump = (
        consumption_no_qty
        .sort_values(oi)
        .groupby('part', as_index=False)
        .last()
        .drop(columns=[oi], errors='ignore')
    )

    # Latest prediction week only (most current waterfall snapshot)
    current_predidx = waterfall['predidx'].max()
    latest_wf = waterfall[waterfall['predidx'] == current_predidx].copy()

    latest_wf = latest_wf.merge(last_consump, on='part', how='left')
    latest_wf = pd.concat(
        [latest_wf, id_features(latest_wf['part'], 8)], axis=1
    )

    latest_cost = (
        cost.sort_values('start')
        .groupby('part', as_index=False)
        .last()[['part', 'amount']]
    )
    latest_wf = latest_wf.merge(latest_cost, how='left', on='part')

    return latest_wf.reset_index(drop=True)
