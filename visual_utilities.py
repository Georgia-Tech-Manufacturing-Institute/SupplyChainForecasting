# Utilities used to create figures - might be integrated into future reports 

import os
from pathlib import Path
from app.core.loader import *

import pandas as pd
import sqlite3
pd.set_option('future.no_silent_downcasting', True)
import matplotlib.pyplot as plt
from matplotlib import ticker
import seaborn as sns


def set_week_year_ticks(ax, axis="x", sec=False):
    isx = axis == "x" or axis=='all'
    isy = axis == "y" or axis=='all'
    if isx:
        ticks = ax.get_xticks()
        labels = ax.get_xticklabels()
        week_info = [idx_to_week(int(label.get_text())) for label in labels]

        ax.set_xticks(ticks)
        ax.set_xticklabels([f"{week:02d}" for year, week in week_info])
        if sec:
            secax = ax.secondary_xaxis("bottom")
            secax.spines["bottom"].set_position(("outward", 30))

    if isy:
        ticks = ax.get_yticks()
        labels = ax.get_yticklabels()
        week_info = [idx_to_week(int(label.get_text())) for label in labels]

        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{week:02d}" for year, week in week_info])
        if sec:
            secax = ax.secondary_yaxis("left")
            secax.spines["left"].set_position(("outward", 30))
    if sec: 
        year_ticks, year_labels = [], []
        for tick, (year, week) in zip(ticks, week_info):
            if not year_ticks or year != year_labels[-1]:
                year_ticks.append(tick)
                year_labels.append(year)

        if isx:
            secax.set_xticks(year_ticks)
            secax.set_xticklabels(year_labels)
        if isy:
            secax.set_yticks(year_ticks)
            secax.set_yticklabels(year_labels)
        
        return secax
    else:
        return ax
def week_pivot(df, col='Pct'):
    return df.pivot_table(index='lookahead', columns='orderidx', values=col, aggfunc='mean')



def pn_date_vis():
    with create_connection() as conn:
        wf   = filter_SQL(conn, table='waterfall_agg')
        cf   = filter_SQL(conn, table='consumption')
        # cost = filter_SQL(conn, table='cost')


        oi_counts = cf.drop_duplicates(subset=['part', 'orderidx']).orderidx.value_counts().reset_index()
        pi_counts = wf.drop_duplicates(subset=['part', 'orderidx'], keep='last').orderidx.value_counts().reset_index()

        # Determine 
        fig, axes = plt.subplots(2,1, sharex=True)
        ax = axes[0]
        ax.set_title("Unique PNs per Week")
        ax.bar(oi_counts.orderidx, oi_counts['count'],width=1, alpha=0.6, label='Order')
        ax.bar(pi_counts.orderidx, pi_counts['count'],width=1, alpha=0.6, label='Forecast')
        ax.legend(bbox_to_anchor=(1,1))

        # determine the earliest predictions to include 
        earliest_order = max(cf.orderidx.min(), wf.orderidx.min())
        latest_order = min(cf.orderidx.max(), wf.orderidx.max())

        # determine the latest predictions to include 
        cf = cf[(cf.orderidx>=earliest_order)&(cf.orderidx<=latest_order)]
        wf = wf[(wf.orderidx>=earliest_order)&(wf.orderidx<=latest_order)]

        oi_counts = cf.drop_duplicates(subset=['part', 'orderidx']).orderidx.value_counts().reset_index()
        pi_counts = wf.drop_duplicates(subset=['part', 'orderidx'], keep='last').orderidx.value_counts().reset_index()
        ax = axes[1]

        ax.bar(oi_counts.orderidx, oi_counts['count'],width=1, alpha=0.6)
        ax.bar(pi_counts.orderidx, pi_counts['count'],width=1, alpha=0.6)
        fig.savefig('assets/pn_date_range.png', bbox_inches='tight')
        # print(df.orderqty.isna().sum() / len(df))


def build_views(conn):
    wf = filter_SQL(conn, table='waterfall_agg')
    cf = filter_SQL(conn, table='consumption')
    cost = filter_SQL(conn, table='cost')
    # cost = filter_SQL(conn, table='cost')
    # determine the earliest predictions to include 
    earliest_order = max(cf.orderidx.min(), wf.orderidx.min())
    latest_order = min(cf.orderidx.max(), wf.orderidx.max())

    # determine the latest predictions to include 
    cf = cf[(cf.orderidx>=earliest_order)&(cf.orderidx<=latest_order)]
    wf = wf[(wf.orderidx>=earliest_order)&(wf.orderidx<=latest_order)]

    merged = (
        wf
        .merge(cf, on=['part', oi], how='left')
        .sort_values([pi, 'part', oi])
    )

    # for this limited frange 
    merged.loc[merged.orderqty.isna(), 'orderqty'] = 0
    merged = merged.merge(cost, on='part', how='left')

    merged['error'] = merged.orderqty - merged.predqty
    merged['usd_delta'] = merged.error * merged.amount
    merged['lookahead'] = merged.orderidx - merged.predidx

    merged = merged[merged.lookahead>=0]
    # latest_order = merged[merged.lookahead<8].sort_values('predidx').drop_duplicates(subset=['part', 'orderidx'], keep='last')

    merged.loc[merged.error == 0, 'Pct'] = 0
    merged.loc[merged.error > 0, 'Pct'] = merged.error/merged[pre['oq']] # Consumption > Predcition
    merged.loc[merged.error<0, 'Pct'] = merged.error/merged[pre['pq']]
    merged.loc[:, 'Pct_Abs'] = merged.Pct.abs()*100
    merged = merged[merged.lookahead >= 1]
    