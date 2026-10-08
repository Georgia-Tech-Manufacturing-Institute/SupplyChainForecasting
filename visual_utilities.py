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
        cf   = filter_SQL(conn, table='consumption_agg')
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

from app.prefixe import pre
pq = pre['pq'] 
oq = pre['oq']

def split_error_amount(merged, catr = 'lookahead_wk' ):

    fig, ax = plt.subplots(1,1)# width_ratios=[15,1])
    # vx = merged[pq]*merged.amount
    # vy = merged[oq]*merged.amount
    submerg = merged[merged[catr]>0]#[(vx >3e5)&(vy< 1.5e5)]
    norm = plt.Normalize(submerg[catr].min(), 
                        submerg[catr].max())
    sm = plt.cm.ScalarMappable(cmap="viridis", norm=norm)
    sm.set_array([])
    ax.set_xlabel(''), ax.set_ylabel('')
    xmin = -10000
    xmax = 4e5
    ax.plot([xmin, xmax], [xmin, xmax], 
            linestyle='dashed', c='k', alpha=0.5)
    x = np.linspace(xmin, xmax, 500)
    ax.fill_between(x, x, xmax, color='blue', alpha=0.2)
    ax.fill_between(x, xmin, x, color='red', alpha=0.2)

    color = submerg[catr][~submerg.amount.isna()]
    xxx = submerg[pq]*submerg.amount
    yyy = submerg[oq]*submerg.amount
    print(color.shape, xxx.shape, yyy.shape)
    # ax.get_legend().remove()
    # plt.colorbar(sm, cax=axes[1])
    ax.get_xaxis().set_major_formatter(
        ticker.FuncFormatter(lambda x, p: f'{x/1000:g}k' if x >= 1000 else f'{int(x):,}'))
    ax.get_yaxis().set_major_formatter(
        ticker.FuncFormatter(lambda x, p: f'{x/1000:g}k' if x >= 1000 else f'{int(x):,}'))

    cax = sns.scatterplot(x = xxx,  y = yyy, s=3,
                    ax=ax, zorder=1000,
                    c= color
                    )
    ax.set_ylabel(''), ax.set_xlabel('')
    ax.set_xlim(xmin, xmax), ax.set_ylim(xmin, xmax)
    plt.show()

def derive_stats(df):
    df['error'] = df.orderqty - df.predqty
    df['usd_delta'] = df.error * df.amount
    # df['lookahead'] = df.orderidx - df.predidx

    df = df[df.lookahead>=0]
    # latest_order = df[df.lookahead<8].sort_values('predidx').drop_duplicates(subset=['part', 'orderidx'], keep='last')

    df.loc[df.error == 0, 'Pct'] = 0
    df.loc[df.error > 0, 'Pct'] = df.error/df[pre['oq']] # Consumption > Predcition
    df.loc[df.error<0, 'Pct'] = df.error/df[pre['pq']]
    df.loc[:, 'Pct_Abs'] = df.Pct.abs()*100
    df = df[df.lookahead >= 1]
    return df


def build_views(conn):
    wf = filter_SQL(conn, table='waterfall_agg')
    cf = filter_SQL(conn, table='consumption_agg')
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
    return merged

def aggregate_view(merged_view):
    fig, axes = plt.subplots(1,1, figsize=(14, 6))
    cmap = sns.blend_palette(
        [ "#FFFFFF", "#9B0101"],
        as_cmap=True
    )

    ax = sns.heatmap(merged_view.pivot_table(index=pi, columns='orderidx', 
                                        values='Pct_Abs'), ax=axes, cmap=cmap) # 
    ax.set_xlabel(''), ax.set_ylabel('')
    # ax.invert_yaxis()
    ax = set_week_year_ticks(ax, axis='both')
    plt.yticks(rotation=0)

def lookahead_evolution(merged_view):
    fig, ax = plt.subplots(1,1, figsize=(10,3))
    sns.lineplot(data =merged_view[merged_view.lookahead >0],
                x='lookahead', y='Pct_Abs', c='red', 
                errorbar=('ci', 80),
                label='Avg. Demand Error', ax=ax)
    ax.hlines(y=0, xmin=1, xmax=merged_view.lookahead.max(), 
            linestyles='dashed', 
            colors='k', label='Zero Error' )
    ax.invert_xaxis()
    ax.set_xticks(np.arange(4,24, 4),  )
    ax.set_ylabel("Absolute % Ordering Error")
    ax.set_xlabel("Lookahead (weeks until order)")
    plt.ylim([-10, 90])
    ax.legend()
    return ax


def split_supply_view(merged_view):
    oversupply = merged_view[merged_view.error <= 0 ]
    undersupply = merged_view[merged_view.error >= 0] # Ordered was way more than predicted

    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(12, 10))
    ax = sns.heatmap(week_pivot(undersupply), ax=axes[0], cmap='Blues') # 
    axes[0].set_xlabel('')
    ax = sns.heatmap(week_pivot(oversupply), ax=axes[1], cmap='Reds_r') # 

    ax.set_xlabel('')
    [ax.invert_yaxis() for ax in axes]
    ax = set_week_year_ticks(axes[1], axis='x')
    # ax = set_week_year_ticks(axes[1], axis='x')
    fig.subplots_adjust(hspace=0.05)
