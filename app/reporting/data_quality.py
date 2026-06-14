from app.core.loader import *
from app.reporting.core import * 
from app.prefixe import pre, dirs

def coverage_report(since_idx=None, until_idx=None, part_prefix=None):
    wf, cf = get_wf_cf()
    if since_idx is not None:
        wf = wf[wf['orderidx'] >= since_idx]
        cf = cf[cf['orderidx'] >= since_idx]
    if until_idx is not None:
        wf = wf[wf['orderidx'] <= until_idx]
        cf = cf[cf['orderidx'] <= until_idx]
    if part_prefix:
        wf = wf[wf['part'].str.startswith(part_prefix)]
        cf = cf[cf['part'].str.startswith(part_prefix)]
    # PNs that are in consumption but NEVER found in a waterfall report
    # no_existing_wf = cf_pns - wf_pns
    # # PNs that are in waterfall but never consumed in 
    # no_existing_cf = wf_pns - cf_pns

    #real_data[real_data.orderqty.isna()&]
    # cf = cf[~cf.part.isin(no_existing_wf)]

    # cf_set = set(cf[['part', oi]].itertuples(index=False, name=None))
    # wf_set = set(wf[['part', oi]].itertuples(index=False, name=None))

    existing_pred = wf[['part', 'orderidx']].drop_duplicates()
    existing_pred = existing_pred[existing_pred!=0]
    existing_pred['value'] = -1
    existing_orders = cf[['part', 'orderidx']].drop_duplicates()
    existing_orders = existing_orders[existing_orders!=0]
    existing_orders['value'] = 1

    result = (
        pd.concat([existing_pred, existing_orders], ignore_index=True)
        .groupby(['part', 'orderidx'], as_index=False)['value']
        .sum()
    )
    pivot = result.pivot(index='part', columns='orderidx')
    print(pivot.columns)
    z = pivot.values.copy()
    z[z == 1] = 2
    z[z == 0] = 1
    z[z == -1] = 0
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=pivot.columns,
            y=pivot.index,
            zmin=0, zmax=2,
            colorscale=[
                [0.0, "#2166ac"],
                [0.3333, "#2166ac"],

                [0.3333, "#2de05a"],
                [0.6666, "#2de05a"],

                [0.6666, "#b2182b"],
                [1.0, "#b2182b"],
            ],
            colorbar=dict(
                orientation='h',
                title='',
                tickvals=[0, 1, 2],
                ticktext=["-1", "0", "1"]
            )
        )
    )    
    fig.update_layout(
        width=800,
        margin=dict(t=30),
        height=1000
    )
    fig.update_xaxes(
        fixedrange=True,
        ticklabelposition="outside top",
        side="top"
    )

    return pio.to_html(fig, full_html=False, include_plotlyjs="cdn")

   