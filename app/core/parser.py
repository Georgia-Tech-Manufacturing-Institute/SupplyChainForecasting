import os
import re
from app.core.processor import *
import datetime
from app.prefixe import plant_key, pre, Path
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



# ============ PARSE WATERFALL FILES ============ 


# Tail of the pipe-delimited extract each data type arrives in, after the
# plant's source-system key (AT_, CU3_, 485_, ...).
EXTRACT_SUFFIX = {'consumption': '_ictrans.txt',
                  'waterfall':   '_custdemand.txt'}


def plant_extract_key(plant):
    '''The source-system key prefixing this plant's extract files, or None when
    the plant has no plant_key entry -- a plant added through the UI, or one
    whose spelling differs from prefixe.plant_key.'''
    return plant_key.get(preformat_plant(plant))


def extract_filename(data_type, plant):
    '''Conventional name of the pipe-delimited extract, e.g. AT_ictrans.txt.'''
    key = plant_extract_key(plant)
    if key is None:
        raise KeyError(f"No plant_key entry for {plant!r}; add one in "
                       f"app/prefixe.py to name its {data_type} extract")
    return f'{key}{EXTRACT_SUFFIX[data_type]}'


def custDemand_filename(plant):
    '''Name of the pipe-delimited customer-demand extract for `plant`.'''
    return extract_filename('waterfall', plant)


# Columns the waterfall tables keep; everything else the reports carry
# (order/line numbers, customer part, the report's own week counter) is dropped.
WATERFALL_KEEP = ['part', 'reldate', 'shipto', 'pcr',
                  pre['pq'], pre['pi'], pre['oi']]


def normalize_columns(df):
    '''Lowercases and de-spaces column labels so the legacy .prn reports
    (Week#/RelDate/ShipTo/...) and the pipe-delimited extracts (already
    lowercased) present the same names to the rest of the pipeline.'''
    df.columns = [str(col).replace(' ', '').lower() for col in df.columns]
    return df


def _as_int(series):
    '''Quantities arrive as text from the fixed-width reports and as numbers
    from the pipe extracts; normalise both to int.'''
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0).astype(int)
    return cast_to_into(series.astype(str).str.strip().replace('', '0'))


def custDemandParse(folder=None, plant='arlington', file=None):
    # plant
    if file:
        df = pd.read_csv(file, sep='|')
    elif folder and plant:
        # folder; posixpath
        df = pd.read_csv(Path(folder) / custDemand_filename(plant), sep='|')
    else:
        raise NameError(f'No file found for folder {folder} or {file}')
    df.columns = [col.replace(' ', '').lower() for col in df.columns]

    dates = [col for col in df.columns if '/' in col]

    all_parts = (
        df.melt(
            id_vars=[s.lower() for s in waterfall_header],
            value_vars=dates,
            var_name='orderdate',
            value_name=pre['pq']
        )
        .dropna(subset=[pre['pq']])
    )
    all_parts.orderdate = pd.to_datetime(all_parts.orderdate, format=date_format)
    all_parts.reldate = pd.to_datetime(all_parts.reldate, format = "%m/%d/%y")
    return waterfallProcess(all_parts)

def waterfallProcess(wf):
    # Takes output of waterfallParse/custDemandParse; makes it more beautiful.
    wf_df = normalize_columns(wf.copy())
    wf_df = wf_df.rename(columns={"predorder_quantity": pre['pq'],
                                  "quantity": pre['pq']})
    # The fixed-width reports hand dates back as mm/dd/yy strings; the pipe
    # extracts have already been converted, and to_datetime leaves those alone.
    wf_df.orderdate = pd.to_datetime(wf_df.orderdate, format=date_format)
    wf_df.reldate = pd.to_datetime(wf_df.reldate, format=date_format)
    wf_df["lookahead"] = wf_df.orderdate - wf_df.reldate
    # Eliminate cases where ReleaseDate/orderdate/Qty triples are for the "past"
    wf_df = wf_df[wf_df.lookahead.dt.days > -14].copy()
    wf_df[pre['ow']] = wf_df.orderdate.dt.isocalendar().week.values
    wf_df[pre['oy']] = wf_df.orderdate.dt.isocalendar().year.values
    wf_df[pre['pw']] = wf_df.reldate.dt.isocalendar().week.values
    wf_df[pre['py']] = wf_df.reldate.dt.isocalendar().year.values
    wf_df[pre['pi']] = make_week_idx(wf_df, pre['pp'], pre['ys'], pre['ws'])
    wf_df[pre['oi']] = make_week_idx(wf_df, pre['op'], pre['ys'], pre['ws'])

    wf_df.part = wf_df.part.astype(str).str.strip()
    wf_df.shipto = wf_df.shipto.astype(str).str.strip()
    wf_df.pcr = _as_int(wf_df.pcr)
    wf_df[pre['pq']] = _as_int(wf_df[pre['pq']])

    # Keep only what the waterfall tables declare, in a stable order.
    wf_df = wf_df[[c for c in WATERFALL_KEEP if c in wf_df.columns]]
    return wf_df.drop_duplicates(keep='first').reset_index(drop=True)


# ============ PARSE CONSUMPTION/TRANSACTION FILES ============ 

def ictrans_filename(plant):
    '''Name of the pipe-delimited transaction extract for `plant`.'''
    return extract_filename('consumption', plant)


def process_ictrans(folder=None, plant='arlington', file=None):
    '''
    Reads the raw transaction extract for `plant` and returns one row per
    ISS-SO transaction: trans_number / part / orderdate / orderqty / orderidx.

    Either `file` (a single extract) or `folder` + `plant` (the extract is
    looked up by its conventional name inside the folder) may be given.

    `trans_number` is the source system's unique transaction id and is carried
    through so the landing table can reject duplicate transactions on re-ingest
    (see loader.autoConsumption).
    '''
    oq = pre['oq']
    part_id = 'Item Number'
    if file is None:
        if folder is None:
            raise NameError('Give process_ictrans either a file or a folder')
        file = Path(folder) / ictrans_filename(plant)
    consump = pd.read_csv(file, sep='|')
    consump.columns = [c.strip() for c in consump.columns]

    consump = consump[consump['Trans Type'] == 'ISS-SO']
    consump = consump.rename({part_id: 'part',
                              'Trans Date': 'orderdate',
                              'Trans Number': 'trans_number',
                              'Trans Qty': oq}, axis=1)
    part_id = 'part'
    consump = consump.loc[:, ['trans_number', part_id,  'orderdate', oq]]
    consump['trans_number'] = consump['trans_number'].astype(str).str.strip()
    consump[part_id] = consump[part_id].astype(str).str.strip()
    consump = consump[(~consump[part_id].str.contains(' '))
                      &(consump[part_id].str.len() >= 8)]

    consump['orderdate'] = pd.to_datetime(consump['orderdate'], format=date_format)
    consump[oq] = consump[oq].abs().astype(int)
    # ISO year, not calendar year: on a year boundary 12/31 can belong to week 1
    # of the following ISO year, and mixing the two splits one week across two
    # indices -- which would leave both halves permanently short of 7 days.
    iso = consump.orderdate.dt.isocalendar()
    consump[pre['oi']] = week_to_idx(iso.year, iso.week)
    consump.part = consump.part.replace('', np.nan)
    consump.dropna(subset=['part'], inplace=True)
    consump = consump.drop_duplicates(subset=['trans_number'])
    return consump


# Column layout of the legacy "Transactions By Item Summary" .prn report.
LEGACY_CONSUMPTION_COLS = ["Site", "Item Number", "Description", "UM",
                           "Type", "Type1", "Quantity1", "Type2", "Quantity2"]

def process_consumption_prn(file, year, week):
    '''
    Legacy pre-aggregated weekly report (2026wk09.prn) -> one row per
    (part, orderidx), i.e. already in consumption_agg shape.

    The report carries no transaction dates, so the week it belongs to comes
    from the filename rather than from the rows.
    '''
    oq, oi = pre['oq'], pre['oi']
    df = consumpParse(file, cols=LEGACY_CONSUMPTION_COLS, year=year, week=week)
    df = df.rename({'Item Number': 'part'}, axis=1)
    df = df[df['Quantity1'].astype(str).str.strip() != '']
    filt = filter_partno(df, col='part')
    if len(filt) == 0:
        return pd.DataFrame(columns=['part', oi, oq])
    filt = filt.copy()
    filt[oq] = cast_to_into(filt['Quantity1']).abs()
    filt[oi] = week_to_idx(year, week)
    return filt.groupby(['part', oi], as_index=False)[oq].sum()


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


# ============ PARSE COST FILES ============ 
## Need an udpated version 
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
