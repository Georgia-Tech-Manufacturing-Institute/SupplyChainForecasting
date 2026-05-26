import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, HistGradientBoostingRegressor
from sklearn.metrics import mean_pinball_loss, mean_squared_error, r2_score

all_models = {}

from sklearn.base import BaseEstimator, TransformerMixin

import pandas as pd
from sklearn.preprocessing import StandardScaler, MinMaxScaler
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer


# Utility functions

def _expanding_stats(
    df: pd.DataFrame,
    group_cols: list[str]=['Part'],
    value_col: str='PredQty',
    sort_col: str='RelDate',
    prefix: str='',
) -> pd.DataFrame:
    """
    For each row, compute expanding statistics over *strictly prior* rows
    within each group (no data leakage: excludes current observation).
    Returns a frame aligned to df's index with columns:
        {prefix}_mean, {prefix}_std, {prefix}_min, {prefix}_max,
        {prefix}_count, {prefix}_cv
    """
    df = df.sort_values(group_cols + [sort_col])
    grp = df.groupby(group_cols)[value_col]

    # shift(1) ensures the current row is excluded
    stats = pd.DataFrame(index=df.index)
    shifted = grp.shift(1)                       # prior value (for count ref)

    # Expanding on the shifted series gives "all prior values"
    exp = grp.transform(
        lambda s: s.shift(1).expanding().mean()
    ).rename(f"{prefix}_mean")

    stats[f"{prefix}_mean"]  = grp.transform(lambda s: s.shift(1).expanding().mean())
    stats[f"{prefix}_std"]   = grp.transform(lambda s: s.shift(1).expanding().std())
    stats[f"{prefix}_min"]   = grp.transform(lambda s: s.shift(1).expanding().min())
    stats[f"{prefix}_max"]   = grp.transform(lambda s: s.shift(1).expanding().max())
    stats[f"{prefix}_count"] = grp.transform(lambda s: s.shift(1).expanding().count())
    stats[f"{prefix}_cv"]    = (
        stats[f"{prefix}_std"] / stats[f"{prefix}_mean"].replace(0, np.nan)
    )
    return stats


def rolling_column(df, wks=2, func='sum', **kwargs):
    return df.rolling(wks, closed='left', **kwargs).aggregate(func).reset_index(level=-1,drop=True)

def roll_statistics(frame, target_col, group_col='Part', n=3, 
                    func=['mean','std'], colnickname='', 
                    **kwargs):
    if isinstance(func,str):
        func = [func]
    grouped = frame.groupby(group_col)[target_col]
    if colnickname != '' and isinstance(colnickname, str):
        col = colnickname
    else:
        col = target_col
    for f in func:
        frame[f'roll_{col}_{f}_{n}'] = rolling_column(grouped,  wks=n, 
                                                      func=f, **kwargs).reset_index()[target_col]
    return frame


def location_stats(frame):
    '''
    Assumes columns PredQty and numerical columns with location
    '''
    df = frame.copy()
    loc_cols = [col for col in df.columns if not str(col).replace('.', '', 1).isdigit() ]
    
    pass

def prior_prediction_features(frame):
    ''' 
    Time series features based on N weeks of prior predictions
    Only requries waterfall data
    '''
    df = frame.copy()
    df['Lookahead'] = df.Orderidx - df.Predidx
    # # For a unique part/order date, give the order in which predictions came. 
    df["Pred_CumCount"] = df.groupby(["Part", "Orderidx"]).cumcount()
    df['RelQty_lag_1wk'] = df.groupby("Part").shift(1).PredQty
    df['RelQty_lag_2wk'] = df.groupby("Part").shift(2).PredQty
    df = roll_statistics(df, target_col='PredQty', n=2, func=['sum'])
    df = roll_statistics(df, target_col='PredQty', n=3, func=['sum'])
    df["PredQty_diff_1wk"] = df.groupby(["Part", "Orderidx"])["PredQty"].diff()


def prior_consumption_features(cf):
    newcf = cf[["Part", "Orderidx", "OrderQty"]].copy()
    for i in range(3,7):
        newcf = roll_statistics(newcf, target_col='OrderQty', n=i, func=['mean', 'std'])
        df = roll_statistics(df, target_col='PredQty', n=i, func=['mean', 'std'])
    newcf['OrderQty_lag_1wk'] = newcf.groupby("Part").shift(1).OrderQty
    newcf['OrderQty_lag_2wk'] = newcf.groupby("Part").shift(2).OrderQty
    # Difference from prior order
    newcf['Orderidx_diff_1wk'] = newcf.groupby("Part").Orderidx.diff()
    newcf['Orderidx_diff_2wk'] = newcf.groupby("Part").Orderidx.diff(2)



def time_series_features(frame, cf):
    # Prior order


    # Features joined based on prediction date (ie prediction only "knows" info from the time of prediction.)
    test_merge = df.merge(newcf.drop("OrderQty",
                                    axis=1).rename({"Orderidx":"Predidx"},axis=1), 
                    on=["Part", "Predidx"],   how='left')
    # Merge consumption features onto DF
    df = pd.concat([df, 
                    test_merge.iloc[:, (len(df.columns)-len(test_merge.columns)):]], 
                    axis=1)
    # col_groups['Past_consum'] = set(df.columns) - set(all_col)
    # all_col = df.columns
    pre_lookat = df.copy()

    # Get statistics based on the 
    for i in sorted(df.Predidx.unique()):
        # Assume "present" is week i
        # Iterate through each week and update with past accuracy diagnostics
        currentweek = df.Predidx==i # Current prediction week 
        prior_data = df[df.Orderidx < i][['Part', 'Predidx', 'Orderidx', 
                                        'RelDate', 'OrderQty', 'PredQty', 
                                        "Lookahead"]] # Update current prediction week
        # Same part N week accuracy
        prior_data['Acc'] = prior_data.OrderQty - prior_data.PredQty
        # per-part agg accuracy 
        all_past_agg_accuracy = prior_data.groupby("Part").Acc.mean()
        prior_part_order_accuracy = prior_data.groupby("Part").Acc.last() # behaves as nth(-1)
        # past per-part release accuracy
        release_gp = prior_data.groupby(["Part", "RelDate"]).Acc
        release_accuracy = pd.concat([
            release_gp.mean().rename("Part_Rel_Mean_Acc"),
            release_gp.std().rename("Part_Rel_Std_Acc")
        ], axis=1).reset_index()
        #cats = []
        cats = [all_past_agg_accuracy.rename("All_Part_Mean_Acc"),
            prior_part_order_accuracy.rename("Last_Order_Part_Mean_Acc"),]
        # Rolling
        for lookback in range(1, 6):
            recent_history = prior_data[prior_data.Lookahead<=lookback]
            cats.append(recent_history.groupby("Part").Acc.mean().rename(f"roll_Part_mean_Acc_LB{lookback}"))
            cats.append(recent_history.groupby("Part").Acc.std().rename(f"roll_Part_std_Acc_LB{lookback}"))

        part_acc = pd.concat(cats, axis=1)
        
        if len(part_acc) > 0:
            local_hist_features = release_accuracy.reset_index(drop=True).merge(part_acc, how='inner', on='Part')
            newcols = local_hist_features.columns.difference(['Part','RelDate'])
            df.loc[currentweek, newcols] = (
                pre_lookat.loc[currentweek]
                .merge(local_hist_features, on=['Part','RelDate'], how='left')
                .set_index(df.loc[currentweek].index) [newcols]
            )

    # col_groups['Past_Acc'] = set(df.columns) - set(all_col)
    # all_col = df.columns

    # Part -> column per digit, plus indicators for L/M  
    df = df.dropna(subset=['Part'], axis=0)
    normal_pn = df.Part.str.len()==8
    part_no_cols = [f'pn{i}' for  i in range(8)]
    df.loc[normal_pn, part_no_cols] = df.Part[normal_pn].str.split('',expand=True).iloc[:,1:9].astype(int)
    df.loc[:, part_no_cols] = df.loc[:, part_no_cols].fillna(0)
    df.loc[df.Part.str.startswith('L'), 'L'] = 1
    df.loc[df.Part.str.startswith('M'), 'M'] = 1
    df.loc[:, ['L', 'M']] = df.loc[:, ['L', 'M']].fillna(0)
    # col_groups['PN'] = set(df.columns) - set(all_col)
    # all_col = df.columns

    # Date features - month, day of year, day of week, ordinal date
    df['ReleaseMonth'] = df.RelDate.dt.month
    df['ReleaseDay'] = df.RelDate.dt.day_of_year
    df['ReleaseWD'] = df.RelDate.dt.day_of_week
    #df['ReleaseOrd'] = df.RelDate.rank(method='dense').astype(int)
    # col_groups['Date_Features'] = set(df.columns) - set(all_col)
    # all_col = df.columns
    df = df.drop("RelDate", axis=1)

    return df