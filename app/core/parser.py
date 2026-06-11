import os
import re
from app.core.processor import *
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
def idx_to_week(v):
    return (v // 52-1, v % 52+1)

def week_to_idx(year, week):
    return int(year) * 52 + int(week)

def make_week_idx(df, on=pre['op'], yearcol=pre['ys'], weekcol=pre['ws']):
    return df[f"{on}{yearcol}"].astype(int) * 52 + df[f"{on}{weekcol}"].astype(int)

def cast_to_into(series, to_int=True):
    # give a column or pandas series
    floated =  series.str.replace(',', '').astype(float)
    return floated.astype(int) if to_int else floated

# ============ PARSE WATERFALL FILES ============ 
def readWaterfallGroup(f):
    dates = []
    partGroup = True # Group all sharing a single PN
    line = ''
    group_text = []
    while partGroup:
        if 'Week#' in line:
            _, datestr = line.rsplit('PCR')
            dates = datestr[1:-1].split(' ')
            partGroup = False
        else:
            line = f.readline()
            if not line:
                break
            group_text.append(line)
    active_data = True
    group_data = []
    ruler = f.readline() # Fixed field delimiters
    if not line:
        return None, None
    widths = [len(group) for group in ruler.split()]
    
    while active_data:
        line = f.readline()
        if not line:
            break
        if line == '\n' or '+' in line:
            active_data = False
        else:
            group_data.append(parse_fixed_width(line, widths))
    df = pd.DataFrame(group_data, columns=waterfall_header + dates)
    return df.replace('', np.nan), dates

def waterfallParse(file):
    all_parts = pd.DataFrame([])
    with open(file) as f:
        while True:
            df, dates  = readWaterfallGroup(f)
            if (df is not None) and (len(df) > 0):
    
                melted = df.melt(id_vars=waterfall_header, 
                                value_vars=dates, 
                                var_name='OrderDate', 
                                value_name=pre['pq']).dropna(subset=[pre['pq']],
                                                                        axis=0)
                all_parts = pd.concat([all_parts, melted], ignore_index=True, axis=0)
            else:
                break
    all_parts.OrderDate = pd.to_datetime(all_parts.OrderDate, format="%m/%d/%y")
    all_parts.RelDate = pd.to_datetime(all_parts.RelDate, format = "%m/%d/%y")
    all_parts["Lookahead"] = all_parts.OrderDate - all_parts.RelDate
    # Eliminate cases where ReleaseDate/OrderDate/Qty triples are for the "past" 
    # NB: We use when lookahead is less than 2 weeks as some cases include the 
    all_parts = all_parts[all_parts.Lookahead.dt.days > -14]
    return all_parts
    #all_parts.to_csv(out, sep="|", mode='a', index=False, header=first)

def waterfallProcess(wf):
    # Takes output of waterfallParse; makes it more beautiful. 
    wf_df = wf.copy()
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
    wf_df.PCR = cast_to_into(wf.PCR)
    wf_df[pre['pq']] = cast_to_into(wf[pre['pq']])

    return wf_df

def _shippingMelt(wf):
    # takes the output of WaterfallProcess in long format and pivots shipping info to wide columns
    # returns each shipto location as a ratio of total; PredQty gives the total amount
    ordering_cols = ['Part', pre['oi'], pre['pi'],'RelDate']
    pcr = wf.set_index(ordering_cols+['ShipTo']).PCR.unstack('ShipTo').reset_index()
    pcr_pivot = pcr.groupby(['Part', pre['oi'], pre['pi']]).ffill()#.reset_index(drop=False)
    pivot = wf.set_index(ordering_cols+['ShipTo'])[pre['pq']].unstack('ShipTo').reset_index()
    part_pivot = pivot.groupby(['Part', pre['oi'], pre['pi']]).ffill()#.reset_index(drop=False)

    shipping_cols = part_pivot.iloc[:,1:].columns
    part_pivot = pd.concat([pivot.iloc[:,:len(ordering_cols)], 
                            part_pivot.iloc[:,1:]], 
                            axis=1)
    part_pivot[shipping_cols] = part_pivot[shipping_cols].fillna(0).astype(int)
    # Sum all shipping groups for each prediction 
    part_pivot['PredQty'] = part_pivot[shipping_cols].sum(axis=1)
    part_pivot['PCR'] = pcr_pivot.iloc[:,1:].sum(axis=1)

    cols = shipping_cols.tolist()
    #part_pivot[cols] = part_pivot[cols].div(part_pivot[pre['pq']], axis=0)
    return part_pivot



def _bulk_waterfall_parse(waterfall_dir,  wf_proc_dir,  overwrite=False):
    '''
    Loads raw waterfall data into processed 
    Assumes naming convention 2025wk06
    '''
    print("Bulk waterfall processing: ")
    # for filename in sorted(os.listdir(waterfall_dir)):      
    #     if validate_type(filename):  
    #         input_path = waterfall_dir / filename
    #         year, week = parse_file_convention(filename)
    #         output_path = wf_proc_dir / f"{int(year)}wk{int(week):02d}.parquet"
    #         if output_path.exists() and not overwrite:
    #             continue #print(f"Skipping existing: {filename}")
    #         parsed = waterfallParse(input_path)
    #         wf = waterfallProcess(parsed)


def _bulk_consumption_parse(consump_dir,   proc_consump_dir, 
                           overwrite=False):
    '''
    Bulk loads data from raw -> processed. Loads data from allof /data/raw/Consumption into 
    '''
    pass
    # consump_dir = Path(consump_dir)
    # proc_consump_dir = Path(proc_consump_dir)
    # proc_consump_dir.mkdir(parents=True, exist_ok=True)
    # for file in sorted(os.listdir(consump_dir)):
    #     input_path = consump_dir / file
    #     if validate_type(input_path):
    #         yr, wk = parse_file_convention(file)
    #         test = consumpParse(input_path, year=yr, week=wk,
    #                             cols= ["Site", "Item Number", "Description", "UM",
    #                                     "Type", "Type1", "Quantity1", "Type2", "Quantity2"])
    #         print("     Processing: ", file, f' as Yr{yr}Wk{wk}')
    #         output_path = proc_consump_dir / f"{int(yr)}wk{int(wk):02d}.parquet"
    #         if output_path.exists() and not overwrite:
    #             print(f"        Skipping existing: {output_path}")
    #             continue

    #         filt = filter_partno(test, col='Part')
    #         if len(filt)>0:
    #             filt = filt.groupby(['Part', 'Orderidx']).sum(numeric_only=True).reset_index()
    #        #     filt.to_parquet(output_path, index=False)


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

def consumpParse(file, 
                 cols =["Site", "Part", "Description", "UM","Type", 
                        "Type1", "Quantity1", "Type2", "Quantity2"],
                year=None, week=None):
    filename = os.path.basename(file)
    # match determines what week of the year
    if not year or not week:
        raise(ValueError("Please provide either a namepattern OR both year and week"))
    filedata = stateParse(file)
    df = pd.DataFrame(filedata, columns=cols)

    df.columns = [c.strip() for c in df.columns]
    df.rename({'Item Number': "Part"}, axis=1, inplace=True, errors='ignore')
    df = df.loc[:, ['Part', 'Quantity1']]
    # df.loc[:, ow] = int(week)
    # df[pre['py']] = int(year)
    df.Part = df.Part.replace('', np.nan)
    df.dropna(subset=['Part'], inplace=True)
    df[pre['oi']] = week_to_idx(int(year), int(week))
    df.Quantity1 = df.Quantity1.str.replace(',','').astype(float).abs()
    # Aggregate 
    return df

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
