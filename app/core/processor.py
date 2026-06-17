# core.processor

import numpy as np 
import pandas as pd 

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
