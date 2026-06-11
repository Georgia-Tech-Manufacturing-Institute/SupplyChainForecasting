from app.core.loader import *
from app.reporting.core import * 
def coverage_report(wf, cf):
    # PNs that are in consumption but NEVER found in a waterfall report
    # no_existing_wf = cf_pns - wf_pns
    # # PNs that are in waterfall but never consumed in 
    # no_existing_cf = wf_pns - cf_pns

    #real_data[real_data.orderqty.isna()&]
    # cf = cf[~cf.part.isin(no_existing_wf)]

    # cf_set = set(cf[['part', oi]].itertuples(index=False, name=None))
    # wf_set = set(wf[['part', oi]].itertuples(index=False, name=None))

    existing_pred = wf[['part', 'orderidx']].drop_duplicates()
    existing_pred['value'] = -1
    existing_orders = cf[['part', 'orderidx']].drop_duplicates()
    existing_orders['value'] = 1

    result = (
        pd.concat([existing_pred, existing_orders], ignore_index=True)
        .groupby(['part', 'orderidx'], as_index=False)['value']
        .sum()
    )

    pivot = result.pivot(index='part', columns='orderidx')
    import plotly.graph_objects as go

    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=pivot.index,
            colorscale='RdBu',
            zmid=0
        )
    )

    fig.update_layout(
        title="Part vs IDX alignment heatmap",
        yaxis_scaleanchor="x",
        height=800,
    )
    # fig.show(renderer='browser')