import pandas as pd
import sqlite3

from app.prefixe import pre
from app.core.loader import filter_SQL
from app.core.parser import week_to_idx
from app.models.features import (
    waterfall_features, consumption_features, id_group_counts, acc_features
)

pi = pre['pi']
oi = pre['oi']
oq = pre['oq']
pq = pre['pq']

def pull_data(conn):
    wf   = filter_SQL(conn, table='waterfall_agg')
    cf   = filter_SQL(conn, table='consumption_agg')
    cost = filter_SQL(conn, table='cost')

    # Currently I'm ONLY preserving the latest most cost, 
    # however, cost does actually change over time - a later approach might be to use the time appropriate cost
    latest_cost = (
        cost.sort_values('start')
        .groupby('part', as_index=False)
        .last()[['part', 'amount']]
    )

    return wf, cf, latest_cost


def merge_features(consumption, waterfall, cost):
    # ONLY provide information known at the time of 
    consumption[pi] = consumption[oi]
    real_consump = consumption.copy()[['part', oi, oq]]
    consumption.rename({oq:f'last_{oq}'}, axis=1, inplace=True)
    merged = pd.merge(left=consumption.drop(oi, axis=1), 
                      right=waterfall,
                      on=['part', pi], 
                      how='right').sort_values([pi, 'part'])
    merged = pd.merge(left=merged, right=cost,
                      on=['part'], how='left')
    
    merged = pd.merge(left=merged, right=real_consump, 
                      on=['part', oi], how='left')

    merged = acc_features(merged)
    merged = id_group_counts(merged) # per part number counts by
    return merged 




def build_training_dataset(conn, min_orderidx: int = None,
                           max_orderidx: int = None,
                           remove_nans: bool = True,
                           progress_cb=None) -> pd.DataFrame:
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
    wf, cf, cost = pull_data(conn)
    waterfall   = waterfall_features(wf, progress_cb=progress_cb)
    consumption = consumption_features(cf)
    # do NOT merge on the order date; that will give an old week access to "future" info. 
    # Instead, we give order data from when the prediction is made  
    print(consumption.columns)
    merged = merge_features(consumption, waterfall, cost)

    lo = min_orderidx if min_orderidx is not None else cf[oi].min()
    hi = max_orderidx if max_orderidx is not None else wf[oi].max()

    real_data = merged[(merged[oi] < hi) & (merged[oi] >= lo)]
    if remove_nans:
        real_data = real_data[~real_data[oq].isna()]
    else:
        real_data = real_data.fillna(0)

    # After creating features, we don't need to keep columns with no data
    # assumes that if we have consumption info in a week, we have
    week_counts = real_data.groupby(oi)[oq].count()
    real_data = real_data[real_data[oi].isin(week_counts[week_counts>0].index)
                          & ~real_data[oq].isna()]

    return real_data.reset_index(drop=True)


def build_prediction_dataset(conn, predidxs=None, aug_lookahead=0) -> pd.DataFrame:
    """
    Build a feature-ready dataset for inference since the requested
    prediction week(s), joining historical consumption features and cost.

    Rows with oi < current_predidx (the requested snapshot week, or its max
    if a range/None) are dropped since they precede what's being predicted
    from. Rows at or after it are kept regardless of whether consumption has
    since posted: weeks with known consumption let predict_from_bundle report
    performance stats, weeks without it (including any aug_lookahead
    projections) get actual predictions.

    aug_lookahead projects that many extra weeks per part beyond the waterfall
    horizon, chaining off the prior projected week each time so its predqty
    grounds the next estimate.
    """
    wf, cf, cost = pull_data(conn)
    if predidxs is None:
        current_predictions = wf[wf[pi] == wf[pi].max()]
    elif isinstance(predidxs, int):
        current_predictions = wf[wf[pi] == predidxs]
    elif isinstance(predidxs, list):
        current_predictions = wf[wf[pi].isin(predidxs)]
    else:
        raise ValueError("predidxs must be an int or list of ints")

    if len(current_predictions) == 0:
        raise ValueError("No valid predictions for the range of weeks given")

    current_predidx = current_predictions[pi].max()
    last_pred = wf[wf[pi]==current_predidx].sort_values(oi).drop_duplicates(subset='part', keep='last')

    aug_pred = []
    for _ in range(aug_lookahead):
        last_pred[oi] = last_pred[oi] + 1
        aug_pred.append(last_pred.reset_index(drop=True)) 
    aug_pred = pd.concat(aug_pred, axis=0)
    # Only keep rows where its actually a big lookahead
    aug_pred = aug_pred[(aug_pred[oi] - aug_pred[pi])>aug_lookahead]

    wf = pd.concat([wf, aug_pred], axis=0)

    waterfall   = waterfall_features(wf)
    consumption = consumption_features(cf)


    merged = merge_features(consumption, waterfall, cost)

    # Keep everything since the requested prediction week(s): known-consumption
    # weeks get scored, weeks without it (incl. aug_lookahead rows) get predicted.
    latest_wf = merged[merged[oi] >= current_predidx].copy()

    if len(latest_wf) == 0:
        raise ValueError("Date range returns no predictions")


    latest_wf = latest_wf.merge(latest_cost, how='left', on='part')

    return latest_wf.reset_index(drop=True)
