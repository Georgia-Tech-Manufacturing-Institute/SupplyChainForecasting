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
    return (
        grouped.rolling(wks, closed='left', **kwargs)
               .agg(func)
               .reset_index(level=-1, drop=True)
    )

def roll_statistics(frame, target_col, group_col='part', n=3, 
                    func=['mean','std'], colnickname='', unit='',
                    **kwargs):
    if isinstance(func,str):
        func = [func]
    grouped = frame.groupby(group_col)[target_col]
    if colnickname != '' and isinstance(colnickname, str):
        col = colnickname
    else:
        col = target_col
    for f in func:
        frame[f'roll{n}{f}_{col}_{unit}'] = rolling_column(grouped,  wks=n, 
                                                      func=f, **kwargs).reset_index()[target_col]
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
    df[f'{pq}_lag_qty'] = df.groupby("part").shift(1)[pq]
    df['lag_ratio'] = df[f'{pq}_lag_qty']/(df[pq]+1)

    df = grouped_column_statistics(df, value_col=pq, sort_col=pi,
                                   prefix=pq, unit='qty')
    
    # Part overall history 
    df[f'{pq}_max_qty'] = df.groupby("part")[pq].cummax()

    df = demand_share_features(df)
    df = prediction_maturity_features(df)
    df = relative_magnitude_features(df)
    df = seasonal_index_features(df, oi) 
    df = seasonal_index_features(df, pi)
    return df

def normalize_qty_features(frame, div_col = f'{pq}_max_qty'):
    qty_cols = [c for c in frame.columns if c.endswith('_qty')]
    frame[qty_cols] = frame[qty_cols] / (frame[div_col]+1)

def consumption_features(frame):
    # Consumption data has one row per part per order week
    cf = frame.copy()

    # Ignore parts that are never consumed
    maxcon = cf.groupby('part')[oq].max()
    cf[cf.part.isin(maxcon[maxcon>0].index)]
    
    # Create features based on consumption data - for each order date, what was the context of prior ordering behavior
    newcf = cf[["part", oi, oq]].sort_values(["part", oi])
    part_group = newcf.groupby("part")
    newcf[f'{oq}_1wkdiff_qty'] = part_group[oq].diff()
    newcf[f'{oq}_4wkdiff_qty'] = part_group[oq].diff(4)

    for i in range(2,5): # Local history for ordering - how past 
        newcf = roll_statistics(newcf, target_col=oq, n=i, func=['mean', 'std'], unit='qty')
    # Overall 
    newcf = grouped_column_statistics(newcf, value_col=oq, prefix=oq, unit='qty', sort_col=oq)

    # Time since last orders: ordering regularity
    newcf[f'{oq}_gap_wk'] = part_group[oi].diff()
    newcf = grouped_column_statistics(newcf, value_col=f'{oq}_gap_wk', prefix=f'{oq}_gap', unit='wk', sort_col=oq)
    return newcf


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
    return df


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

def date_features(frame):
    df = frame.copy()
    # Date features - month, day of year, day of week, ordinal date
    df['ReleaseMonth'] = df.RelDate.dt.month
    df['ReleaseDay'] = df.RelDate.dt.day_of_year
    df['ReleaseWD'] = df.RelDate.dt.day_of_week
    df['ReleaseOrd'] = df.RelDate.rank(method='dense').astype(int)
    return df


def id_features(col: pd.Series, exp_length: int=8):
    # Decompose the part number or other identifier into characteristics
    # part -> column per digit, plus indicators for L/M  
    # Normalize lengths to 8 identifying spaces
    part_col = col.copy().str.replace(r'[ABEUWHG-]', '', regex=True)
    abnorm = part_col.str.len()>exp_length
    starter_dummy = pd.get_dummies(part_col[abnorm].str[0]).astype(bool).astype(int)
    part_col[abnorm] = part_col[abnorm].str[1:exp_length//2+2] + part_col[abnorm].str[-exp_length//2:]
    pn_data = part_col.str.strip().str.split('',expand=True).iloc[:, 1:exp_length+1]

    pn_data.columns = [f'pn{i}' for  i in range(8)]
    pn_data = pd.concat([pn_data, starter_dummy], axis=1)
    pn_data = pn_data.fillna(0).astype(np.int8)
    return pn_data