import os
import re
from app.core.processor import *
waterfall_header = ['Week#', 'RelDate', 'SO', 'Ln', 'ShipTo', 'CustomerPart', 'Part', 'PCR']

# i have a serious consistency problem with the column naming conventions and i admit that 
pp = 'Pred'     # prediction prefix
op = 'Order'    # consumption/order prefix
ys = 'Year'       # year suffix 
ws = "Week"       # week suffix
qs = "Qty"      # quantity suffix
ds = "idx"      # index suffix

pi, oi = pp+ds, op+ds
py, oy = pp+ys, op+ys
pw, ow = pp+ws, op+ws
pq, oq = pp+qs, op+qs

both = [op, pp]
time = [ys, ws]
cols = [ys,ws,qs]

ordercol = [f'{op}{i}' for i in cols]
predcol = [f'{pp}{i}' for i in cols]
qtycol = [f'{i}{qs}' for i in both]
idxcol = [f'{i}{ds}' for i in both ]

def idx_to_week(v):
    return (v // 52, v % 52)

def week_to_idx(year, week):
    return year * 52 + week

def make_week_idx(df, on=op, yearcol=ys, weekcol=ws):
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
                                var_name='PredOrder_Date', 
                                value_name=pq).dropna(subset=[pq],
                                                                        axis=0)
                all_parts = pd.concat([all_parts, melted], ignore_index=True, axis=0)
            else:
                break
    all_parts.PredOrder_Date = pd.to_datetime(all_parts.PredOrder_Date, format="%m/%d/%y")
    all_parts.RelDate = pd.to_datetime(all_parts.RelDate, format = "%m/%d/%y")
    all_parts["Lookahead"] = all_parts.PredOrder_Date - all_parts.RelDate
    # Eliminate cases where ReleaseDate/OrderDate/Qty triples are for the "past" 
    # NB: We use when lookahead is less than 2 weeks as some cases include the 
    all_parts = all_parts[all_parts.Lookahead.dt.days > -14]
    return all_parts
    #all_parts.to_csv(out, sep="|", mode='a', index=False, header=first)

def altParse(df, dates):
    # Dont use, old functionality that doesnt do anything D:
    scd = df.iloc[:, len(waterfall_header):] # ordering data
    x, y = np.where(~scd.isna()) # Identify first nonzero order based on dates 
    if len(x) > 0:
        # Infill non-null quanitites in "valid" weeks - that is, future dates (from time of release) 
        mask = np.triu(np.ones_like(scd), k=y[0]).astype(np.bool_) # Upper triangle (ie future dates asc.)
        scd = scd.where(~mask, scd.where(mask).fillna(0)) # Fill only zeros to the right. 
        cols = df.columns[len(waterfall_header):]
        df[cols] = (
            df[cols].apply(lambda s: s.str.replace(',', '')) .astype(np.float32)
        )
        # recover all ReleaseDate/OrderDate/Qty triples 
        melted = df.melt(id_vars=waterfall_header, 
                        value_vars=dates, 
                        var_name='PredOrder_Date', 
                        value_name=pq).dropna(subset=[pq], axis=0)
    return melted
    #all_parts.to_csv(out, sep="|", mode='a', index=False, header=first)


def waterfallProcess(wf):
    # Takes output of waterfallParse; makes it more beautiful. 
    wf_df = wf.copy()
    wf_df.PredOrder_Date = pd.to_datetime(wf_df.PredOrder_Date, format="%m/%d/%y")
    wf_df.RelDate = pd.to_datetime(wf_df.RelDate, format="%m/%d/%y")

    wf_df[ow] = wf_df.PredOrder_Date.dt.isocalendar().week
    wf_df[oy] = wf_df.PredOrder_Date.dt.isocalendar().year
    wf_df[py] = wf_df.RelDate.dt.isocalendar().year
    wf_df = wf_df.rename({'Week#': pw}, axis=1)
    wf_df[pi] = make_week_idx(wf_df, pp, ys, ws)
    wf_df[oi] = make_week_idx(wf_df, op, ys, ws)
    wf_df.drop([py, oy, pw, ow], axis=1, inplace=True, errors='ignore')
    wf_df.rename(columns={"PredOrder_Quantity": pq}, inplace=True)

    wf_df.drop(['CustomerPart', 'SO', 'Ln',
                "Week#","PredOrder_Date", "Lookahead"], axis=1, inplace=True, errors='ignore')
    wf_df = wf_df.drop_duplicates(keep='first')
    wf_df.Part = wf_df.Part.astype(str)
    wf_df.PCR = cast_to_into(wf.PCR)
    wf_df[pq] = cast_to_into(wf[pq])

    return wf_df

def shippingMelt(wf):
    # takes the output of WaterfallProcess in long format and pivots shipping info to wide columns
    # returns each shipto location as a ratio of total; PredQty gives the total amount
    ordering_cols = ['Part', oi, pi,'RelDate']
    pcr = wf.set_index(ordering_cols+['ShipTo']).PCR.unstack('ShipTo').reset_index()
    pcr_pivot = pcr.groupby(['Part', oi, pi]).ffill()#.reset_index(drop=False)
    pivot = wf.set_index(ordering_cols+['ShipTo'])[pq].unstack('ShipTo').reset_index()
    part_pivot = pivot.groupby(['Part', oi, pi]).ffill()#.reset_index(drop=False)

    shipping_cols = part_pivot.iloc[:,1:].columns
    part_pivot = pd.concat([pivot.iloc[:,:len(ordering_cols)], 
                            part_pivot.iloc[:,1:]], 
                            axis=1)
    part_pivot[shipping_cols] = part_pivot[shipping_cols].fillna(0).astype(int)
    # Sum all shipping groups for each prediction 
    part_pivot['PredQty'] = part_pivot[shipping_cols].sum(axis=1)
    part_pivot['PCR'] = pcr_pivot.iloc[:,1:].sum(axis=1)

    cols = shipping_cols.tolist()
    #part_pivot[cols] = part_pivot[cols].div(part_pivot[pq], axis=0)
    return part_pivot


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

def consumpParse(file, name_pattern=r'Wk_(\d+)\s+(\d{8})', 
                 cols =["Site", "Part", "Description", "UM","Type", 
                        "Type1", "Quantity1", "Type2", "Quantity2"],
                year=None, week=None):
    filename = os.path.basename(file)
    # match determines what week of the year
    if name_pattern and year:
        m = re.search(name_pattern, filename)
        if m:
            week = m.group(1) or m.group(2)
        else:
            raise( ValueError("Filename does not match expected pattern"))
    elif not year or not week:
        raise(ValueError("Please provide either a namepattern OR both year and week"))
    filedata = stateParse(file)
    df = pd.DataFrame(filedata, columns=cols)

    df.columns = [c.strip() for c in df.columns]
    df.rename({'Item Number': "Part"}, axis=1, inplace=True, errors='ignore')
    df = df.loc[:, ['Part', 'Quantity1']]
    df.loc[:, ow] = int(week)
    df[oy] = int(year)
    df.Part = df.Part.replace('', np.nan)
    df.dropna(subset=['Part'], inplace=True)
    df[oi] = make_week_idx(df)
    df.Quantity1 = df.Quantity1.str.replace(',','').astype(float).abs()
    df.drop([oy, ow], axis=1, inplace=True, errors='ignore')
    # Aggregate 
    return df

def merge_frames(wf_df, consump_df):
    merged = pd.merge(left=wf_df, 
                  right=consump_df, 
                  on=['Part', ow, oy], how='outer')
    merged.Part = merged.Part.astype(str)
    merged.rename(columns={'Quantity1':'ConsumQty'}, inplace=True)
    merged.loc[:, ["RelQty", "ConsumQty"]] =merged.loc[:, ["RelQty", "ConsumQty"]].fillna(0)
    return merged.drop(columns=['Description','SO', 'Ln', "CustomerPart"], errors='ignore')

def costPrep(cost_file, cost_outfile, save=False, overwrite=False):
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

    cost_map = cost_map.loc[cost_map.groupby('Part')['Start'].idxmax()].reset_index()
    cost_map = cost_map[['Part', 'Amt1']]
    if os.path.exists(cost_outfile) and save and not overwrite:
        print('File exists; please turn on overwrite if you wish to override existing cost file.')
        return False
    elif save:
        cost_map.to_parquet(cost_outfile)
        return True
    return cost_map


if __name__=='__main__':
    pd.set_option('future.no_silent_downcasting', True)
    pass