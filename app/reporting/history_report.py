# history_report.py

'''
Given a scope of time, creates reports for the past ordering accuracy

'''

from app.reporting.core import *
from app.prefixe import pre, dirs


def build_bins(b, xmin=-1, xmax=1):
    edges = []
    x = 0
    while x < xmax:
        x += b
        edges.append(x)

    x = 0
    neg = []
    while x > xmin:
        x -= b
        neg.append(x)

    bins = sorted(set(neg + edges))
    bins = np.array(bins).round(2)
    return bins[1:-1]

def error_report(save_to_file=True, save_loc=None, max_lookahead=None, since_idx=None, until_idx=None, part_prefix=None, render=False):
    latest = merged_data()
    latest['lookahead'] = latest[pre['oi']] - latest[pre['pi']]
    latest['error'] = (latest[pre['oq']] - latest[pre['pq']])

    if max_lookahead:
        latest = latest.loc[latest.lookahead <= max_lookahead]
    if since_idx:
        latest = latest.loc[latest[pre['oi']] >= since_idx]
    if until_idx:
        latest = latest.loc[latest[pre['oi']] <= until_idx]
    if part_prefix:
        latest = latest.loc[latest['part'].str.startswith(part_prefix)]

    latest.loc[latest.error == 0, 'Pct'] = 0
    latest.loc[latest.error > 0, 'Pct'] = latest.error/latest[pre['oq']] # Consumption > Predcition
    latest.loc[latest.error<0, 'Pct'] = latest.error/latest[pre['pq']]

    bins = build_bins(0.1) # bin width 

    latest['bins'] =  pd.cut(latest["Pct"], bins=bins, include_lowest=True)
    bin_counts = latest.groupby("part").value_counts(["bins"])

    report = pd.pivot_table(bin_counts.reset_index(), index="part", columns="bins",  fill_value=0) 
    report = report.droplevel(0,axis=1)
    report.rename({report.columns[0]: '<-100%', report.columns[-1]: '>100%'}, axis=1, inplace=True)
    pct_report = report.div(report.sum(axis=1),axis=0).round(2)

    save_dir = dirs['reports']
    min_date = latest[pre['oi']].min()
    max_date = latest[pre['oi']].max()
    minyr, minwk = idx_to_week(min_date)
    maxyr, maxwk = idx_to_week(max_date)
    daterange = f"{minyr}Wk{minwk:02d} - {maxyr}Wk{maxwk:02d}"
    if save_to_file:
        name = save_loc if save_loc else 'OrderAccuracy_'
        report.to_excel(save_dir / f'{name}_{daterange}.xlsx')
        pct_report.to_excel(save_dir / f'{name}_Pct_{daterange}.xlsx') 
    if render:
        fig = go.Figure(
            data=go.Heatmap(
                z=pct_report.values,
                x=pct_report.columns.astype(str),
                y=pct_report.index.astype(str),
                customdata=report.values,
                colorscale="Reds",
                showscale=True,
                hovertemplate=(
                    "Consumption: %{x}<br>"
                    "Part: %{y}<br>"
                    "% of predictions: %{z}<br>"
                    "No. of predictions:%{customdata}"
                    "<extra></extra>"
                ),
                colorbar=dict(
                    orientation="h", len=0.8,
                    x=0.5, xanchor="center",
                    y=1.01, yanchor="bottom",
                    title='% of predictions'
                )
            )
        )
        fig.update_layout(
            width=800,
            margin=dict(t=30),
            height=5000
        )
        fig.update_xaxes(
            fixedrange=True,
            ticklabelposition="outside top",
            side="top"
        )
        annot_kwargs = {'xref': "x domain", 'yref': "y domain", "showarrow": False, "font": dict(size=14)}
        fig.add_annotation(x=0.48, y=1.0, text="← Underconsuming ---", xanchor="right", **annot_kwargs)
        fig.add_annotation(x=0.52, y=1.0, text="--- Overconsuming →", xanchor="left", **annot_kwargs)
        return pio.to_html(fig, full_html=False, include_plotlyjs="cdn")


def main():
    pass

    #errorReport(filtered, save_loc=Path(Path.cwd() /'reports'))


if __name__ == '__main__':
    main()