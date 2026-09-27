import os
import re
from app.core.processor import *
import datetime
from app.prefixe import plant_key, pre
from app.core.common import *
waterfall_header = ['Week#', 'RelDate', 'SO', 'Ln', 'ShipTo', 'CustomerPart', 'Part', 'PCR']


pre = {
    'pp': 'pred',
    'op': 'order',
    'rd': 'reldate',
    'ys': 'year',
    'ws': 'week',
    'qs': 'qty',
    'ds': 'idx'
}
pre.update({
    'pi': pre['pp'] + pre['ds'],
    'oi': pre['op'] + pre['ds'],
    'py': pre['pp'] + pre['ys'],
    'oy': pre['op'] + pre['ys'],
    'pw': pre['pp'] + pre['ws'],
    'ow': pre['op'] + pre['ws'],
    'pq': pre['pp'] + pre['qs'],
    'oq': pre['op'] + pre['qs'],
})
date_format = "%m/%d/%y"


def idx_to_week(v):
    return (v // 52, v % 52)

def preformat_plant(s):
    return s.strip().lower()

def week_to_idx(year, week):
    return int(year) * 52 + int(week)

def make_week_idx(df, on=pre['op'], yearcol=pre['ys'], weekcol=pre['ws']):
    return df[f"{on}{yearcol}"].astype(int) * 52 + df[f"{on}{weekcol}"].astype(int)

def cast_to_into(series, to_int=True):
    # give a column or pandas series
    floated =  series.str.replace(',', '').astype(float)
    return floated.astype(int) if to_int else floated

# ============ PARSE WATERFALL FILES ============ 


def custDemandParse(plant, folder):
    # plant 
    plant = preformat_plant(plant)
    # folder; posixpath 
    df = pd.read_csv(folder / f'{plant_key[plant]}_custdemand.txt', 
                     sep='|')
    df.columns = [col.replace(' ', '') for col in df.columns]

    dates = [col for col in df.columns if '/' in col]

    all_parts = (
        df.melt(
            id_vars=waterfall_header,
            value_vars=dates,
            var_name='OrderDate',
            value_name=pre['pq']
        )
        .dropna(subset=[pre['pq']])
    )
    all_parts.OrderDate = pd.to_datetime(all_parts.OrderDate, format=date_format)
    all_parts.RelDate = pd.to_datetime(all_parts.RelDate, format = "%m/%d/%y")
    return waterfallProcess(all_parts)

def waterfallProcess(wf):
    # Takes output of waterfallParse; makes it more beautiful. 
    wf_df = wf.copy()
    wf_df["Lookahead"] = wf_df.OrderDate - wf_df.RelDate
    # Eliminate cases where ReleaseDate/OrderDate/Qty triples are for the "past" 
    wf_df = wf_df[wf_df.Lookahead.dt.days > -14]
    wf_df.OrderDate = pd.to_datetime(wf_df.OrderDate, format="%m/%d/%y")
    wf_df.RelDate = pd.to_datetime(wf_df.RelDate, format="%m/%d/%y")
    wf_df[pre['ow']] = wf_df.OrderDate.dt.isocalendar().week
    wf_df[pre['oy']] = wf_df.OrderDate.dt.isocalendar().year
    wf_df[pre['py']] = wf_df.RelDate.dt.isocalendar().year
    wf_df = wf_df.rename({'Week#': pre['pw']}, axis=1)
    wf_df[pre['pi']] = make_week_idx(wf_df, pre['pp'], pre['ys'], pre['ws'])
    wf_df[pre['oi']] = make_week_idx(wf_df, pre['op'], pre['ys'], pre['ws'])
    wf_df.drop([pre['py'],pre['oy'], pre['pw'], pre['ow']], axis=1, inplace=True, errors='ignore')
    wf_df.rename(columns={"PredOrder_Quantity": pre['pq']}, inplace=True)

    wf_df.drop(['CustomerPart', 'SO', 'Ln',
                "Week#","OrderDate", "Lookahead"], axis=1, inplace=True, errors='ignore')
    wf_df = wf_df.drop_duplicates(keep='first')

    wf_df.Part = wf_df.Part.astype(str)
    if wf_df.PCR.dtype == str:
        wf_df.PCR = cast_to_into(wf.PCR)
    if wf_df[pre['pq']].dtype == str:
        wf_df[pre['pq']] = cast_to_into(wf[pre['pq']])
    return wf_df

def process_ictrans(folder, plant):
    oq = pre['oq']
    part_id = 'Item Number'
    consump = pd.read_csv(folder / f'{plant_key[plant]}_ictrans.txt', sep='|')
    consump = consump[consump['Trans Type'] == 'ISS-SO']
    consump = consump.rename({part_id: 'part', 
                              'Trans Date': 'orderdate', 
                              'Trans Qty': oq}, axis=1)
    part_id = 'part'
    consump = consump.loc[:, [part_id, 'Trans Number', 'orderdate', oq]]
    consump[part_id] = consump[part_id].str.strip()
    consump = consump[(~consump[part_id].str.contains(' '))
                      &(consump[part_id].str.len() >= 8)]
    
    consump['orderdate'] = consump['orderdate'].apply(lambda f: datetime.strptime(f, '%m/%d/%y'))
    consump[oq] = consump[oq].abs().astype(int)
    consump['week'] = pd.to_datetime(consump['orderdate']).apply(lambda x: x.week)
    #consump[oi] = consump['orderdate'].apply(lambda x: week_to_idx(x.year, x.week))
   #consump = consump.drop('orderdate', axis=1)

    return consump



def filter_partno(df, col='Part'):
    # Set filters to decrease the amount 
    df_filtered = df[
        df[col].str.contains(r'\d', na=False) 
        & df[col].str.startswith(('L', 'M', '2', '4', '8'), 
                                na=False)
        & ~df[col].str.strip().str.contains(r'\s', na=False)
        & ~df[col].str.contains(r'_', na=False)
        & ~df[col].str.contains(r'\.', na=False)
                                ]
    
    return df_filtered



def costPrep(cost_file):
    costdata = stateParse(cost_file)
    cols = ['Line', "LineDesc","List", "Name","Part", "UM", "ItemDesc", "PO", "UM1", "Start", "Expire", 
    "T", "Cur", "MinQty1", "Amt1", "MinQty2", "Amt2", "MinQty3", "Amt3", "MinQty4", "Amt4", "MinQty5", "Amt5",

    "BaseCurr", "TotalCost", "Remarks"]
    cost_map = pd.DataFrame(costdata, columns=cols).drop([f'MinQty{n}' for n in range(1,6)],axis=1)

    # Filters
    cost_map = cost_map[cost_map.UM1!= "--"]
    cost_map = cost_map[cost_map.Amt1 != '']
    cost_map = cost_map[cost_map.Part!='Item Number']

    cost_map.Amt1 = cost_map.Amt1.astype(float)
    cost_map.Part = cost_map.Part.replace('', np.nan).astype(str)
    cost_map.Start = pd.to_datetime(cost_map.Start, format='%m/%d/%y')
    cost_map = cost_map[['Part', 'Start', 'Amt1']]

    cost_map = cost_map.loc[cost_map.groupby('Part')['Start'].idxmax()].reset_index(drop=True)
    #cost_map = cost_map[['Part', 'Amt1']]

    return cost_map

if __name__=='__main__':
    pd.set_option('future.no_silent_downcasting', True)
