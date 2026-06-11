import pandas as pd
import numpy as np
import pandas as pd
from app.prefixe import pre
print(type(pre))
print(pre)
pi = pre['pi']
oi = pre['oi']
oq = pre['oq']
pq = pre['pq']

# Metadata tracking guy
class column_units:
    def __init__(self, unit_map:dict={}):
        self.unit_map = unit_map
    def add_column(self, column_name, column_unit):
        if column_unit in self.unit_map:
            self.unit_map[column_unit] = column_name
        else:
            self.unit_map[column_unit] = [column_name]
    def add_columns(self, names: list[str], units:list[str]):
        for name, unit in zip(names, units):
            self.add_column(name,unit)
    def add_last_df_column(self, df, column_unit):
        if isinstance(column_unit, str):
            column_unit = [column_unit]
        elif isinstance(column_unit, list):
            pass
        else:
            raise TypeError("Please pass a single unit or a list of units where each unit maps to a column at the end of the df.")
        # Take advantage of cases where a Dataframe column assignment uses the -1 column index to assign 
        n = len(column_unit)
        self.add_columns(df.columns[-n], column_unit)
    

# Filter Functions -----------------------------------------------------------------------------


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

# Data aaugmentation -----------------------------------------------------------------------------


def extend_predictions(df, n=1):
    # extend predictions by n=1 week by creating an earlier null prediction (ie predqty=0)
    # per part/order week, creates n prior predictions n, n-1... weeks earlier than the first prediction
    # TODO provide production number subset
    earliest_pred_per_part_order = df.sort_values(["part",oi]).groupby(['part', oi])[pi].min().reset_index()
    all_new_rows = []
    for i in range(n):
        earliest_pred = earliest_pred_per_part_order.copy()
        earliest_pred[pi] = earliest_pred[pi] - i
        # filter only if lookahead > 2 
        earliest_pred = earliest_pred[(earliest_pred[oi] - earliest_pred[pi]) > 2]
        all_new_rows.append(earliest_pred)
    all_new_rows = pd.concat(all_new_rows, axis=0)
    all_new_rows  = all_new_rows.reset_index(drop=True)
    all_new_rows[pq] = 0
    return all_new_rows

def materialize_zeros(df):
    """ 
    Given a waterfall set, materialize data points where data is zero
    Only performed between "relevant points" reasonable range of 
    """
    # Per group, determine known horizons 
    pass


def merge_confusing():
    # Features joined based on prediction date (ie prediction only "knows" info from the time of prediction.)
    test_merge = df.merge(cf.drop("OrderQty", axis=1).rename({"Orderidx":"Predidx"}, axis=1), 
                    on=["part", "Predidx"],   how='left')
    # Merge consumption features onto DF
    df = pd.concat([df, 
                    test_merge.iloc[:, (len(df.columns)-len(test_merge.columns)):]], 
                    axis=1)

# ADD FEATURES --------------------------------------------------------------------------------
# Feature naming convention
# originating column _ operator _ unit - 
# Alternatively summarizor _ unit ->  eg. lookahead

def accuracy_features(aligned_frame):
    df = aligned_frame.copy()
    df = pd.concat()
    # Get statistics based on the 
    for i in sorted(df.Predidx.unique()):
        # Assume "present" is week i
        # Iterate through each week and update with past accuracy diagnostics
        currentweek = df.Predidx==i # Current prediction week 
        prior_data = df[df.Orderidx < i][['part', pre['pi'], oi, 
                                        'RelDate', oq, pq, 
                                        "Lookahead"]] # Update current prediction week
        # Same part N week accuracy
        prior_data['Acc'] = prior_data.OrderQty - prior_data.PredQty
        # per-part agg accuracy 
        all_past_agg_accuracy = prior_data.groupby("part").Acc.mean()
        prior_part_order_accuracy = prior_data.groupby("part").Acc.last() # behaves as nth(-1)
        # past per-part release accuracy
        #cats = []
        cats = [all_past_agg_accuracy.rename("All_part_Mean_Acc"),
            prior_part_order_accuracy.rename("Last_Order_part_Mean_Acc"),]
        # Rolling
        for lookback in range(1, 6):
            recent_history = prior_data[prior_data.Lookahead<=lookback]
            cats.append(recent_history.groupby("part").Acc.mean().rename(f"roll_part_mean_Acc_LB{lookback}"))
            cats.append(recent_history.groupby("part").Acc.std().rename(f"roll_part_std_Acc_LB{lookback}"))

        part_acc = pd.concat(cats, axis=1)
        
        if len(part_acc) > 0:
            local_hist_features = release_accuracy.reset_index(drop=True).merge(part_acc, how='inner', on='part')
            newcols = local_hist_features.columns.difference(['part','RelDate'])
            df.loc[currentweek, newcols] = (
                pre_lookat.loc[currentweek]
                .merge(local_hist_features, on=['part','RelDate'], how='left')
                .set_index(df.loc[currentweek].index) [newcols]
            )

    return df

def part_history(agg_frame):
    # agg_frame: df/cf merged frame 
    pass


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

def waterfall_features(frame, extend=True):
    df = frame.copy()
    df = df.sort_values(['part', oi, pi]).reset_index(drop=True)
    # Prior guess (nonzero, based on waterfall)
    df['lookahead_wk'] = df.orderidx - df.predidx
    df['predqty_lag_qty'] = df.groupby("part").shift(1).predqty
    df['lag_ratio'] = df.predqty_lag_qty/(df.predqty+1)
    if extend:
        df = pd.concat([df, extend_predictions(df)],axis=0)
        df = df.sort_values(['part', oi, pi])
    df = grouped_column_statistics(df, value_col=pq, sort_col=pi,
                                   prefix=pq, unit='qty' )
    
    # PN Specific history
    # highest ever predicted value (for a given week) for that 
    df['predqqty_max_qty'] = df.groupby("part")["predqty"].cummax()

    # Simultaneous demand / walk forward stats
    for idx in sorted(df.predidx.unique()):
        known_info = df[df[pi]<=idx] # get the most recent order info per date
        latest_info = known_info.loc[known_info.groupby(['part', oi])[pi].idxmax()]
        mask = df[pi] == idx
        simultaneous_demand = latest_info.groupby([oi])[pq].count()
        df.loc[mask, 'orderidx_part_count'] = df.loc[mask, oi].map(simultaneous_demand)

    # Rolling statistic - compute after adding in zeros
    # df = roll_statistics(df, target_col='predqty', n=2, func=['sum'])
    # df = roll_statistics(df, target_col='predqty', n=3, func=['sum'])

    # qty change from prior_order
    #df = pd.concat([df, id_features(df.part, 8)],axis=1)

    df["predqty_1wkdiff_qty"] = df.groupby(["part", oi])[pq].diff()
    return df

def normalize_qty_features(frame, div_col = 'predqqty_max_qty'):
    qty_cols = [c for c in frame.columns if c.endswith('_qty')]
    frame[qty_cols] = frame[qty_cols] / (frame[div_col]+1)

def n_prior_pred(wf, n_weeks):
    temp = wf.copy()
    temp['predidx'] = wf.predidx - n_weeks
    suf =  f'_{n_weeks}wk_prior'
    mrged = wf.merge( temp,  how='left', 
                     on=['part', 'orderidx',  'predidx'], 
                     suffixes=('', suf)).fillna(0)
    return (mrged[[c for c in mrged.columns if c.endswith(suf)]]).astype(int)

def consumption_features(frame):
    cf = frame.copy()
    # Create features based on consumption data - for each order date, what was the context of prior ordering behavior
    newcf = cf[["part", oi, oq]]
    for i in range(2,4): # rolling is okay since we have all data every map. 
        newcf = roll_statistics(newcf, target_col=oq, n=i, func=['mean', 'std'], unit='qty')
    # Prior order 
    newcf[f'part_lag1_qty'] = newcf.groupby("part").shift(1)[oq]
    newcf[f'part_lag2_qty'] = newcf.groupby("part").shift(2)[oq]
    # # Difference from prior order
    # newcf['='] = newcf.groupby("part").Orderidx.diff()
    return newcf

def acc_features(real_data):
    # merged: ordering aligned dataframe
    df = real_data.copy()
    # TODO : FIX THIS !!!! 
    for i in sorted(df[pi].unique()):
        # Assume "present" is week i; Iterate through each week and update with past accuracy diagnostics
        currentweek = df[pi]==i # Current prediction week 

        # Retrieve known ordering up to "present" week
        prior_data = df[df[oi] < i][['part', pi, oi, oq, pq, 'lookahead_wk']] # Update current prediction week
        print(prior_data.shape)
        # Same part N week accuracy
        error_name = f'{oq}_error_qty'
        prior_data[error_name] = prior_data[oq] - prior_data[pq]
        # per-part agg accuracy 
        all_past_agg_accuracy = prior_data.groupby("part")[error_name].mean()
        # past per-part release accuracy
        cats = [all_past_agg_accuracy.rename("part_acc_mean_qty")
                ]
        # Rolling
        for lookback in range(1, 4):
            recent_history = prior_data[prior_data['lookahead_wk']<=lookback]
            cats.append(recent_history.groupby("part")[error_name].mean().rename(f"part_acc_rollmean{lookback}_qty"))
            cats.append(recent_history.groupby("part")[error_name].std().rename(f"part_acc_rollstd{lookback}_qty"))
        part_acc = pd.concat(cats, axis=1)

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