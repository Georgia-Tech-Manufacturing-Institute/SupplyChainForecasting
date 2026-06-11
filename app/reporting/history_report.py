from app.core.loader import *
from app.reporting.core import *
from app.prefixe import pre, dirs

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

def errorReport(filtered, save_loc: Path='',  max_lookahead=None, since_idx=None, until_idx=None, render=False):
    latest = filtered.loc[ filtered.groupby(['part', pre['pi'], pre['oi']])[pre['rd']].idxmax()]
    latest['lookahead'] = latest[pre['oi']] - latest[pre['pi']]
    latest['error'] = (latest[pre['oq']] - latest[pre['pq']])

    # Field filtering
    if max_lookahead:
        latest = latest.loc[latest.lookahead <= max_lookahead]
    if since_idx:
        latest = latest.loc[latest[pre['oi']] >= since_idx]
    if until_idx:
        latest = latest.loc[latest[pre['oi']] <= until_idx]

    latest.loc[latest.error == 0, 'Pct'] = 0
    latest.loc[latest.error > 0, 'Pct'] = latest.error/latest[pre['oq']] # Consumption > Predcition
    latest.loc[latest.error<0, 'Pct'] = latest.error/latest[pre['pq']]

    bins = build_bins(0.1) # bin width 

    latest['bins'] =  pd.cut(latest["Pct"], bins=bins, include_lowest=True)
    bin_counts = latest.groupby("Part").value_counts(["bins"])

    report = pd.pivot_table(bin_counts.reset_index(), index="Part", columns="bins",  fill_value=0) 
    report = report.droplevel(0,axis=1)
    report.rename({report.columns[0]: '<-100%', report.columns[-1]: '>100%'}, axis=1, inplace=True)
    pct_report = report.div(report.sum(axis=1),axis=0).round(2)

    if save_loc.exists():
        min_date = latest[pre['oi']].min()
        max_date = latest[pre['oi']].max()
        minyr, minwk = idx_to_week(min_date)
        maxyr, maxwk = idx_to_week(max_date)
        daterange = f"{minyr}Wk{minwk:02d} - {maxyr}Wk{maxwk:02d}"
        report.to_excel(save_loc / f'OrderAccuracy_{daterange}.xlsx')
        pct_report.to_excel(save_loc / f'OrderAccuracy_Pct_{daterange}.xlsx') 
        print("Saved to ", save_loc)
    if render:
        return pct_report


def main():
    filter_nans = True
    conn = sqlite3.connect(dirs['sql'])

    hist_wf = filter_SQL(conn, table='waterfall') # with releases/dates
    wf = filter_SQL(conn, table='waterfall_agg') # prediction-wise rows  
    cf = filter_SQL(conn, table='consumption') # 

    conn.close() 

    #errorReport(filtered, save_loc=Path(Path.cwd() /'reports'))


if __name__ == '__main__':
    main()