import numpy as np
import plotly.graph_objects as go
import plotly.io as pio


def holdout_report(bundle: dict) -> str:
    """
    Generate a Plotly HTML report from a bundle that contains holdout_df.
    Returns an HTML string (empty if no holdout data is available).

    Columns expected in bundle['holdout_df']:
        orderqty, est_orderqty, naive_orderqty, lookahead_wk
    """
    df = bundle.get('holdout_df')
    if df is None or df.empty:
        return ''

    oq    = df['orderqty'].values
    est   = df['est_orderqty'].values
    naive = df['naive_orderqty'].values

    # ── summary metrics ───────────────────────────────────────────────────────
    model_mae  = float(np.abs(oq - est).mean())
    naive_mae  = float(np.abs(oq - naive).mean())
    ss_res_m   = float(np.sum((oq - est)   ** 2))
    ss_res_n   = float(np.sum((oq - naive) ** 2))
    ss_tot     = float(np.sum((oq - oq.mean()) ** 2))
    model_r2   = 1 - ss_res_m / ss_tot if ss_tot else float('nan')
    naive_r2   = 1 - ss_res_n / ss_tot if ss_tot else float('nan')
    n_rows     = len(df)
    mae_lift   = (naive_mae - model_mae) / naive_mae * 100 if naive_mae else 0

    metrics_html = f"""
<div style="display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px;">
  {_metric_card("Model MAE",    f"{model_mae:.1f}")}
  {_metric_card("Naive MAE",    f"{naive_mae:.1f}")}
  {_metric_card("MAE lift",     f"{mae_lift:+.1f}%", highlight=mae_lift > 0)}
  {_metric_card("Model R²",     f"{model_r2:.3f}")}
  {_metric_card("Naive R²",     f"{naive_r2:.3f}")}
  {_metric_card("Holdout rows", f"{n_rows:,}")}
</div>"""

    # ── scatter: actual vs predicted ──────────────────────────────────────────
    q_min = min(float(oq.min()), 0)
    q_max = float(max(oq.max(), est.max(), naive.max())) * 1.05

    fig_scatter = go.Figure()
    fig_scatter.add_trace(go.Scatter(
        x=[q_min, q_max], y=[q_min, q_max],
        mode='lines',
        line=dict(color='#868e96', width=1, dash='dash'),
        name='Perfect prediction',
    ))
    fig_scatter.add_trace(go.Scatter(
        x=oq, y=naive,
        mode='markers',
        marker=dict(size=4, opacity=0.35, color='#adb5bd'),
        name='Naive (predqty)',
    ))
    fig_scatter.add_trace(go.Scatter(
        x=oq, y=est,
        mode='markers',
        marker=dict(size=4, opacity=0.5, color='#4361ee'),
        name='Model',
    ))
    fig_scatter.update_layout(
        title='Actual vs Predicted Order Qty — holdout set',
        xaxis_title='Actual order qty',
        yaxis_title='Predicted order qty',
        height=420,
        template='plotly_white',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        margin=dict(t=60, b=40),
    )

    # ── line: MAE by lookahead week ───────────────────────────────────────────
    df = df.copy()
    df['_model_err'] = np.abs(oq - est)
    df['_naive_err'] = np.abs(oq - naive)
    agg = (
        df.groupby('lookahead_wk')
        .agg(model_mae=('_model_err', 'mean'),
             naive_mae=('_naive_err', 'mean'),
             n=('orderqty', 'count'))
        .reset_index()
        .sort_values('lookahead_wk')
    )

    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(
        x=agg['lookahead_wk'], y=agg['naive_mae'],
        mode='lines+markers',
        line=dict(color='#adb5bd', dash='dot', width=2),
        marker=dict(size=6),
        name='Naive MAE',
        customdata=agg['n'],
        hovertemplate='Lookahead %{x}wk<br>Naive MAE: %{y:.1f}<br>n=%{customdata}<extra></extra>',
    ))
    fig_line.add_trace(go.Scatter(
        x=agg['lookahead_wk'], y=agg['model_mae'],
        mode='lines+markers',
        line=dict(color='#4361ee', width=2),
        marker=dict(size=6),
        name='Model MAE',
        customdata=agg['n'],
        hovertemplate='Lookahead %{x}wk<br>Model MAE: %{y:.1f}<br>n=%{customdata}<extra></extra>',
    ))
    fig_line.update_layout(
        title='MAE by Lookahead Week',
        xaxis_title='Lookahead (weeks)',
        yaxis_title='MAE (order qty)',
        height=360,
        template='plotly_white',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        margin=dict(t=60, b=40),
    )

    scatter_html = pio.to_html(fig_scatter, full_html=False, include_plotlyjs='cdn')
    line_html    = pio.to_html(fig_line,    full_html=False, include_plotlyjs=False)

    return metrics_html + scatter_html + line_html


def _metric_card(label: str, value: str, highlight: bool = False) -> str:
    color = '#2b9348' if highlight else 'var(--text)'
    return (
        f'<div style="background:var(--card-bg, #f8f9fa); border:1px solid var(--border, #dee2e6); '
        f'border-radius:8px; padding:10px 16px; min-width:100px; text-align:center;">'
        f'<div style="font-size:11px; color:var(--text-muted, #868e96); text-transform:uppercase; '
        f'letter-spacing:.05em; margin-bottom:4px;">{label}</div>'
        f'<div style="font-size:20px; font-weight:600; color:{color};">{value}</div>'
        f'</div>'
    )
