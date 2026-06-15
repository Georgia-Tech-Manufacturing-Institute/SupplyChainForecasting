# core.processor

import numpy as np 
import pandas as pd 

def filterZeroDemand(df, q1='RelQty', q2='ConsumQty'):
    q1=df.columns[7:-1]
    q2="OrderQty"
    temp = df.fillna(0)
    def groupmax(df, qg):
        if isinstance(qg, list):
            maxes = df.groupby('Part')[qg].max().max(axis=1)
        elif isinstance(qg,str) and (qg in temp.columns):
            maxes = df.groupby('Part')[qg].max()
        else: 
            raise TypeError("Please ensure columns exist and are given as a single column or list of columns")
        return maxes

    nopred = groupmax(temp, list(q1)) 
    #noconsum = groupmax(temp, q2)
    drop_index = nopred[nopred == 0].index
    filtered = df[~df.Part.isin(set(drop_index))]
    return filtered.reset_index(drop=True)

def timeFilter(df, year):
    filtdf = df[df.OrderYear == 2025]
    data_2025 = data_2025.sort_values(['OrderYear', 'OrderWeek'])

# ============ PARSE CONSUMPTION FILES ============ 

def delimited_split(s, header_length, n=9):
    parsed_str = [s[i:i+n] for i in range(0, len(s), n)]
    int_list = [int(x) if x.strip() else '' for x in parsed_str]
    return int_list + [""]*(header_length - len(int_list)) 
def parse_fixed_width(line, widths):
    fields = []
    pos = 0
    for w in widths:
        fields.append(line[pos:pos + w].strip())
        pos += w + 1  # +1 assumes a space between columns
    return fields

def parseHeader(line, keys):
    header = {}
    strip = line.strip()
    for k in keys:
        if strip.startswith(k):
            split = line.split(':')
            key_phrase = split[1].strip()
            value = key_phrase.split(' ')[0]
            header[k] = value.strip()
    return header


def stateParse(file):
    filedata = []
    state='header'
    with open(file, encoding='latin1') as f:
        for line in f:
            if state == 'header':
                if '----' in line:
                    state = 'ruler'
                else:
                    continue
            if state == 'ruler':
                widths = [len(group) for group in line.split(' ')]
                state = 'data'
                continue
            if state =='data':
                if "Transactions By Item" in line:
                    state = 'header'
                elif "End of Report" in line:
                    state='footer'
                    continue
                else:
                    filedata.append(parse_fixed_width(line, widths))
                    continue
            if state=='footer':
                continue
    return filedata

def forecast_age(df):
    has_pred = df[~df.PredYear.isna()]

    df.loc[has_pred.index, "forecast_age"] = (has_pred.OrderYear - has_pred.PredYear)*52 + has_pred.OrderWeek - has_pred.PredWeek
