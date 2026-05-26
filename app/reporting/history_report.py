from app.core.loader import *
from app.reporting.core import *

def filter_dates(df, min_date:datetime, max_date:datetime):
    pass

def prep_data():
    pass

def build_bins(b):
    edges = [0]
    x = 0
    while x < 1:
        x += b
        edges.append(x)

    x = 0
    neg = []
    while x > -1:
        x -= b
        neg.append(x)

    bins = sorted(set(neg + edges))
    bins = np.array(bins).round(2)
    return bins

def errorReport(filtered, save_loc: Path='',  max_lookahead=None, since_idx=None, until_idx=None):
    latest = filtered.loc[ filtered.groupby(['Part', 'Predidx', 'Orderidx'])['RelDate'].idxmax()]
    latest['lookahead'] = latest.Orderidx - latest.Predidx
    latest['error'] = (latest.OrderQty - latest.PredQty)

    # Field filtering
    if max_lookahead:
        latest = latest.loc[latest.lookahead <= max_lookahead]
    if since_idx:
        latest = latest.loc[latest.Orderidx >= since_idx]
    if until_idx:
        latest = latest.loc[latest.Orderidx <= until_idx]

    latest.loc[latest.error == 0, 'Pct'] = 0
    latest.loc[latest.error > 0, 'Pct'] = latest.error/latest.OrderQty # Consumption > Predcition
    latest.loc[latest.error<0, 'Pct'] = latest.error/latest.PredQty

    bins = build_bins(0.1) # bin width 

    latest['bins'] =  pd.cut(latest["Pct"], bins=bins, include_lowest=True)
    bin_counts = latest.groupby("Part").value_counts(["bins"])

    report = pd.pivot_table(bin_counts.reset_index(), index="Part", columns="bins",  fill_value=0) 
    report = report.droplevel(0,axis=1)
    report.rename({report.columns[0]: '<-100%', report.columns[-1]: '>100%'}, axis=1, inplace=True)
    pct_report = report.div(report.sum(axis=1),axis=0).round(2)

    if save_loc.exists():
        min_date = latest.Orderidx.min()
        max_date = latest.Orderidx.max()
        minyr, minwk = idx_to_week(min_date)
        maxyr, maxwk = idx_to_week(max_date)
        daterange = f"{minyr}Wk{minwk:02d} - {maxyr}Wk{maxwk:02d}"
        report.to_excel(save_loc / f'OrderAccuracy_{daterange}.xlsx')
        pct_report.to_excel(save_loc / f'OrderAccuracy_Pct_{daterange}.xlsx')
    print("Saved to ", save_loc)


def main():
    pd.set_option('future.no_silent_downcasting', True)

    #parent_dir = Path(__file__).resolve().parent
    parent_dir = Path.cwd()
    data = parent_dir / 'app' / 'data'

    raw_dir = data / 'raw'
    waterfall_dir =  raw_dir/ 'Waterfall'
    consump_dir = raw_dir /  'Consumption'
    proc_dir = data /  "Processed"
    proc_cost = proc_dir / 'Cost.parquet'
    cost_map = pd.read_parquet(proc_cost)


    proc_consum = proc_dir / 'Consumption'
    consumption = all_consumption_load(proc_consum)

    proc_wf = proc_dir / 'Waterfall'
    minidx, maxidx = consumption.Orderidx.min(), consumption.Orderidx.max()
    earliest_prediction = week_to_idx(2024, 18)
    latest_prediction = week_to_idx(2026,10)
    earliest_order = consumption.Orderidx.min()
    latest_order = consumption.Orderidx.max()

    wf = load_waterfall_span(earliest_prediction, 
                            latest_prediction,
                            proc_wf)
    wf = wf[[col for col in wf.columns if not str(col).replace('.', '', 1).isdigit() ]]
    wf = wf[(wf.Orderidx<=latest_order)&(wf.Orderidx>=earliest_order)]

    consumption = consumption[consumption.Orderidx<=latest_order]
    wf, consumption = filter_disjoint(wf, consumption)
    joint = wf.merge(consumption, on=["Part", 'Orderidx'], how="outer")
    # Filter out instances where PredQty is never non-NaN; ie consumed parts that are not waterfall parts. 
    filtered = joint.groupby('Part').filter(lambda g: g['PredQty'].notna().any())
    filtered.Quantity1 = filtered.Quantity1.fillna(0)
    filtered.rename({"Quantity1":"OrderQty"}, axis=1, inplace=True)
    consumption.rename({"Quantity1":"OrderQty"}, axis=1, inplace=True)

    filtered.PredQty = filtered.PredQty.fillna(0)

    #report.to_csv(parent_dir / 'HistoryReport.csv')
    errorReport(filtered, save_loc=Path(Path.cwd() /'reports'))


if __name__ == '__main__':
    main()