import pandas as pd
import numpy as np
import pandas as pd
from app.prefixe import pre

pi = pre['pi']
oi = pre['oi']
oq = pre['oq']
pq = pre['pq']

# Utility functions -----------------------------------------------------------------------------

def grouped_column_statistics(
    df: pd.DataFrame,
    group_cols: list[str]=['part'],
    value_col: str=pq,
    sort_col: str='RelDate',
    prefix: str='',
    unit: str='qty'
) -> pd.DataFrame:
    df = df.sort_values(group_cols + [sort_col])
    grp = df.groupby(group_cols)[value_col]

    df[f"{prefix}_mean_{unit}"]  = grp.transform(lambda s: s.expanding().mean())
    df[f"{prefix}_std_{unit}"]   = grp.transform(lambda s: s.expanding().std())
    df[f"{prefix}_cv"]    = (  df[f"{prefix}_std_{unit}"] / df[f"{prefix}_mean_{unit}"].replace(0, np.nan) )
    return df

def rolling_column(grouped, wks=2, func='sum', **kwargs):
    # groupby().rolling() prepends the group keys to the index, leaving
    # (*group_keys, original_index).  Drop the group levels -- NOT the last one --
    # so the result still carries the caller's row labels and realigns row-for-row.
    rolled = grouped.rolling(wks, closed='left', **kwargs).agg(func)
    n_group_levels = rolled.index.nlevels - 1
    if n_group_levels > 0:
        rolled = rolled.droplevel(list(range(n_group_levels)))
    return rolled

def roll_statistics(frame, target_col, group_col='part', n=3,
                    func=['mean','std'], colnickname='', unit='',
                    **kwargs):
    if isinstance(func,str):
        func = [func]
    if colnickname != '' and isinstance(colnickname, str):
        col = colnickname
    else:
        col = target_col
    suffix = f'_{unit}' if unit else ''

    # Roll over a copy with a clean RangeIndex so the rolled values come back in
    # frame-row order; a duplicated/shuffled index would silently misalign them.
    work = frame.reset_index(drop=True)
    grouped = work.groupby(group_col)[target_col]
    for f in func:
        rolled = rolling_column(grouped, wks=n, func=f, **kwargs)
        frame[f'roll{n}{f}_{col}{suffix}'] = rolled.reindex(work.index).to_numpy()
    return frame

def latest_release_per_week(df, groups:list=['part', oi, pi],
                            date_col:str='RelDate'):
    ''' 
    in the processed form, rows are unique by part/orderidx/reldate where there may be many releases in a given week.
    fxn give a strict "last release" based estimate for unique part/predidx/orderidx
    '''
    return df.loc[df.groupby(groups)[date_col].idxmax()]


# ADD FEATURES --------------------------------------------------------------------------------
# Feature naming convention
# originating column _ operator _ unit - 
# Alternatively summarizor _ unit ->  eg. lookahead

### Simpler lightweight features
def release_gp_variation(agg_frame, col, unit=None):
    # agg_frame: df/cf merged frame
    # aggregated release accuracy at the time of a given prediction 
    release_gp = agg_frame.groupby(["reldate"])[col]
    part_release_gp = agg_frame.groupby(["part", "reldate"])[col]
    release_accuracy = pd.concat([
        release_gp.mean().rename(f"rel_mean_{col}"),
        release_gp.std().rename(f"rel_std_{col}"),
        part_release_gp.mean().rename(f"part_rel_mean_{col}"),
        part_release_gp.std().rename(f"part_rel_std_{col}")
        ], axis=1).reset_index()
    return release_accuracy

def waterfall_features(frame, progress_cb=None):
    """
    progress_cb(step, total, message) — called during the walk-forward loop so
    callers can report progress to a UI.  step is 0-indexed, total is loop length.
    """
    df = frame.copy()
    df = df.sort_values(['part', oi, pi]).reset_index(drop=True)
    
    df['lookahead_wk'] = df[oi] - df[pi]

    # Last prediction for the same part
    for i in np.arange(1, 48, step=2):
        df = demand_lag_features(df, i)
    # Shape of the horizon curve within one snapshot: rolled over orderidx inside
    # each (part, predidx), so it only ever reads the snapshot published at predidx.
    df = roll_statistics(df, target_col=pq, group_col=['part', pi], n=2, unit='qty')
    df = roll_statistics(df, target_col=pq, group_col=['part', pi], n=4, unit='qty')
    df = roll_statistics(df, target_col=pq, group_col=['part', pi], n=6, unit='qty')

    # Expanding history 
    df = grouped_column_statistics(df, value_col=pq, sort_col=pi,
                                   prefix=pq, unit='qty')
    df[f'{pq}_max_qty'] = df.groupby("part")[pq].cummax()
    # Engineered features
    df = demand_share_features(df)
    # df = prediction_local_summation(df, 3)
    df = prediction_maturity_features(df)
    df = relative_magnitude_features(df)
    df = seasonal_index_features(df, oi)
    df = seasonal_index_features(df, pi)
    # Long-lookahead signal: the plan's own shape and the shutdowns it marks.
    # Both read only the snapshot published at predidx, so they stay populated
    # for future order weeks at inference -- see the note in plan_shape_features.
    df = plan_shape_features(df)
    df = shutdown_proximity_features(df)
    return df

def normalize_qty_features(frame, div_col = f'{pq}_max_qty'):
    qty_cols = [c for c in frame.columns if c.endswith('_qty')]
    frame[qty_cols] = frame[qty_cols] / (frame[div_col]+1)

def demand_lag_features(ndf, shift=1):
    df = ndf.copy()
    df[f'{pq}_lag_qty'] = df.groupby("part").shift(shift)[pq]
    g = df.groupby("part")[pq]
    df[f"{pq}_lag_diff"] = df[pq] - df[f"{pq}_lag_qty"]
    df['lag_ratio'] = df[f'{pq}_lag_qty']/(df[pq]+1)
    df[f"{pq}_momentum_{shift}"] = df[pq] - g.shift(shift)
    return df


def consumption_lag_features(newcf, lags=(1, 2, 3, 4)):
    """
    Calendar-aligned consumption lags: orderqty in the order week k weeks back.

    consumption_agg is sparse -- median 3 weeks present per part across a ~53
    week span, and 57% of parts have gaps inside their own span -- so a
    groupby().shift(k) is the k-th prior *order event*, which may sit a year
    earlier. These join on the week index instead and stay NaN when the part has
    no row for that week.

    Safe at any join lag: week oi-k has closed by the time week oi has.
    """
    base = newcf[['part', oi, oq]]
    for k in lags:
        shifted = base.rename(columns={oq: f'{oq}_lag{k}_qty'}).copy()
        shifted[oi] = shifted[oi] + k
        newcf = newcf.merge(shifted, on=['part', oi], how='left')
    return newcf


def consumption_features(frame):
    # Consumption data has one row per part per order week
    # Consumption is merged with waterfall based on the waterfall predidx and consumption orderidx;
    # ie consumption info available at the time of prediction ONLY 
    cf = frame.copy()

    # Ignore parts that are never consumed
    maxcon = cf.groupby('part')[oq].max()
    cf = cf[cf.part.isin(maxcon[maxcon>0].index)]
    cf = cf.reset_index()
    
    # Create features based on consumption data - for each order date, what was the context of prior ordering behavior
    newcf = cf[["part", oi, oq]].sort_values(["part", oi])
    part_group = newcf.groupby("part")
    newcf[f'{oq}_1wkdiff_qty'] = part_group[oq].diff()
    newcf[f'{oq}_4wkdiff_qty'] = part_group[oq].diff(4)

    # Prior weeks' consumption alongside the merged-in current week
    newcf = consumption_lag_features(newcf, lags=np.arange(0, 12))

    for i in range(2,5): # Local history for ordering - how past 
        newcf = roll_statistics(newcf, target_col=oq, n=i, func=['mean', 'std'], unit='qty')
    newcf = roll_statistics(newcf, target_col=oq, n=i, func=['sum'], unit='qty')
    # Overall 
    newcf = grouped_column_statistics(newcf, value_col=oq, prefix=oq, unit='qty', sort_col=oi)

    # Time since last orders: ordering regularity
    # (regroup: the helpers above return re-sorted frames, so part_group is stale)
    newcf[f'{oq}_gap_wk'] = newcf.groupby("part")[oi].diff()
    newcf = grouped_column_statistics(newcf, value_col=f'{oq}_gap_wk', prefix=f'{oq}_gap', unit='wk', sort_col=oi)
    return newcf

def prediction_local_summation(pf, lookback_wks=3):
    df = pf.copy()
    df = df.sort_values(['part', oi, pi])
    pivot = df.pivot_table(index=['part', oi], columns=pi, values=pq, aggfunc='last')
    pivot = pivot.sort_index(axis=1)
    pivot = pivot.ffill(axis=1)
    rolled = (
        pivot.groupby(level='part', group_keys=False)
            .apply(lambda df: df.rolling(lookback_wks, min_periods=1).sum())
    )
    rolled = rolled.reset_index().melt(
        id_vars=['part', oi], var_name=pi, value_name=f'prior_sum_{lookback_wks}wk_qty'
    )
    return df.merge(rolled, on=['part', oi, pi], how='left')


def prediction_maturity_features(df):
    """How far along in the prediction lifecycle is each part/order pair?"""
    df = df.copy()
    ## Order specific info
    part_order_group = df.groupby(['part', oi])
    # Number of prior predictions issued for this order (0-indexed: 0 = first prediction)'
    df['n_prior_preds'] = part_order_group.cumcount()
    # Order earliest predqty 
    first_predqty = part_order_group[pq].transform('first')
    df[f'first_{pq}'] = first_predqty
    # Change since last order for the same date
    df[f'{pq}_1wkdiff_qty'] = part_order_group[pq].diff()
    # Total drift from the initial prediction
    df[f'{pq}_total_drift'] = df[pq] - first_predqty
    # Ratio of current prediction to the first prediction (1.0 = unchanged)
    df['first_to_current_ratio'] = df[pq] / (first_predqty + 1)
    return df


def relative_magnitude_features(df):
    """Predqty relative to this part's historical distribution (requires expanding stats already computed)."""
    df = df.copy()
    df[f'{pq}_vs_mean_ratio'] = df[pq] / (df[f'{pq}_mean_qty'] + 1)
    df[f'{pq}_vs_max_ratio'] = df[pq] / (df[f'{pq}_max_qty'] + 1)
    return df


def seasonal_index_features(df, idx_col):
    """Week-of-year and fiscal quarter derived from a week-index column."""
    df = df.copy()
    week_of_year = df[idx_col] % 52
    df[f'{idx_col}_woy'] = week_of_year
    df[f'{idx_col}_quarter'] = week_of_year // 13
    df[f'{idx_col}_woq'] = week_of_year - df[f'{idx_col}_quarter']*13
    return df


def plan_shape_features(df):
    """Where the target week sits on the plan curve the snapshot published.

    The roll2/4/6 predqty features above are horizon-*local* windows; these are
    snapshot-wide, so they say whether this week is a peak or a trough of the
    whole published plan rather than of its immediate neighbours.  That
    distinction is what carries at long lookahead: the neighbourhood of a week
    28 weeks out is just as speculative as the week itself, while the plan's
    overall shape still encodes the customer's intent.

    Plan-side only, so unlike anything derived from consumption these are
    populated for every future order week at inference time.
    """
    df = df.copy()
    g = df.groupby(['part', pi])[pq]
    snap_med   = g.transform('median')
    snap_first = g.transform('first')   # nearest-horizon week of this snapshot
    snap_mean  = g.transform('mean')
    snap_n     = g.transform('size')

    df[f'{pq}_vs_snapmed_ratio']  = df[pq] / (snap_med + 1)
    df[f'{pq}_vs_snapnear_ratio'] = df[pq] / (snap_first + 1)
    # normalized rise/fall across the whole published horizon
    df[f'{pq}_snapslope'] = (g.transform('last') - snap_first) / (snap_n * (snap_mean + 1))
    df['plan_horizon_wk'] = snap_n
    return df


def shutdown_proximity_features(df, low_frac=0.5):
    """Weeks to / since the nearest week the plan itself marks as a shutdown.

    Plant-level planned volume relative to the snapshot's own median; weeks below
    low_frac are treated as shutdowns.  Demand pulls ahead before a shutdown and
    catches up after, and because the plan flags those weeks in advance this
    works at long lookahead -- unlike a week-of-year index, which would need
    several years of actuals to estimate and only ~1.5 are available.
    """
    df = df.copy()
    tot = df.groupby([pi, oi], as_index=False)[pq].sum()
    tot = tot.sort_values([pi, oi])
    tot['plant_plan_rel'] = tot[pq] / (tot.groupby(pi)[pq].transform('median') + 1)
    tot['is_low'] = tot['plant_plan_rel'] < low_frac

    out = []
    for _, grp in tot.groupby(pi, sort=False):
        grp = grp.reset_index(drop=True)
        low = grp[oi][grp.is_low].to_numpy()
        weeks = grp[oi].to_numpy()
        if len(low) == 0:
            grp['shutdown_next_wk'] = np.nan
            grp['shutdown_prev_wk'] = np.nan
        else:
            nxt = np.searchsorted(low, weeks, side='left')
            grp['shutdown_next_wk'] = np.where(
                nxt < len(low), low[np.clip(nxt, 0, len(low) - 1)] - weeks, np.nan)
            prv = nxt - 1
            grp['shutdown_prev_wk'] = np.where(
                prv >= 0, weeks - low[np.clip(prv, 0, len(low) - 1)], np.nan)
        out.append(grp[[pi, oi, 'plant_plan_rel', 'shutdown_next_wk', 'shutdown_prev_wk']])

    return df.merge(pd.concat(out, ignore_index=True), on=[pi, oi], how='left')


def demand_share_features(df):
    """Part's predicted qty as a fraction of total demand in the same order week.
    Computed walk-forward so only info available at prediction time is used."""
    df = df.copy()
    df['demand_share'] = np.nan
    df['total_orderidx_demand'] = np.nan

    for idx in sorted(df[pi].unique()):
        known = df[df[pi] <= idx]
        latest = known.loc[known.groupby(['part', oi])[pi].idxmax()]
        mask = df[pi] == idx

        total = latest.groupby(oi)[pq].sum().rename('total')
        per_part = latest.set_index([oi, 'part'])[pq]
        total_mapped = df.loc[mask, oi].map(total)

        df.loc[mask, 'total_orderidx_demand'] = total_mapped.values
        df.loc[mask, 'demand_share'] = (
            df.loc[mask, pq].values / (total_mapped.values + 1)
        )
    return df

def acc_features(real_data):
    df = real_data.copy()
    part_acc_history = []
    for i in sorted(df[pi].unique()):
        # Assume "present" is week i; Iterate through each week and update with past accuracy diagnostics
        # Retrieve known ordering up to "present" week
        prior_data = df[df[oi] < i][['part', pi, oi, oq, pq, 'lookahead_wk']] # Update current prediction week
        if prior_data.shape[0] == 0:
            continue
        # Same part N week accuracy
        error_name = f'{oq}_error_qty'
        prior_data[error_name] = prior_data[oq] - prior_data[pq]
        # per-part agg accuracy 
        all_past_agg_accuracy = prior_data.groupby("part")[error_name].mean()
        # past per-part release accuracy
        cats = [all_past_agg_accuracy.rename("part_acc_mean_qty")
                ]
        # Rolling
        for lookback in range(3, 6):
            recent_history = prior_data[prior_data['lookahead_wk']<=lookback]
            cats.append(recent_history.groupby("part")[error_name].mean().rename(f"part_acc_rollmean{lookback}_qty"))
            cats.append(recent_history.groupby("part")[error_name].std().rename(f"part_acc_rollstd{lookback}_qty"))
        
        part_acc = pd.concat(cats, axis=1).reset_index(drop=False)
        part_acc[pre['pi']] = i 
        part_acc_history.append(part_acc)
    part_acc_history = pd.concat(part_acc_history)
    return df.merge(part_acc_history, how='left', on=['part', pre['pi']])


def id_features(col: pd.Series):
    # Decompose the part number or other identifier into characteristics
    # part -> column per digit, plus indicators for L/M  
    # Normalize lengths to 8 identifying spaces
    part_col = col.copy().str.replace(r'[ABEUWHG-]', '', regex=True)
    # abnorm = part_col.str.len()>exp_length
    # starter_dummy = pd.get_dummies(part_col[abnorm].str[0]).astype(bool).astype(int)
    # part_col[abnorm] = part_col[abnorm].str[1:exp_length//2+2] + part_col[abnorm].str[-exp_length//2:]
    # pn_data = part_col.str.strip().str.split('',expand=True).iloc[:, 1:exp_length+1]

    # pn_data.columns = [f'pn{i}' for  i in range(8)]
    pn_data = pd.DataFrame([])
    pn_data[f"digit_1"] = col.str[0]

    for k in range(2, 8):
        pn_data[f"prefix_{k-1}"] = col.str[1:k]
    pn_data = pd.get_dummies(pn_data, columns=["digit_1"])
    # pn_data = pn_data.fillna(0).astype(np.int8)
    return pn_data

def id_group_counts(df):
    dff = df.copy()
    id_df = id_features(dff.part)
    dff = pd.concat([dff, id_df], axis=1)
    # isss = None
    # for c in range(1,3):
    #     prefix_level = (dff.groupby(pi)[f'prefix_{c}']
    #                     .value_counts().unstack()
    #                     .reset_index(col_level=0))
    #     name = prefix_level.columns.name
    #     prefix_level.columns = [pre['pi']]+[f'{name}x{s}' for s in prefix_level.columns[1:]]
    #     if isss is not None:
    #         isss = pd.merge(isss, prefix_level, on=pi)
    #     else: 
    #         isss=prefix_level
    # dff = dff.merge(isss, on=pi, how='left')
    return dff