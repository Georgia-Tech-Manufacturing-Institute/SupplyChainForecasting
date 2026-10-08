# core.processor

### DEFUNCT, DO NOT USE !!!

import numpy as np 
import pandas as pd 
from app.core.common import *
from app.prefixe import pre
 
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


'''
Previous versions of the data were as .prn and formatted as a diamond report. e.g.

### CUSTOMER DEMAND FORMAT
xxcsdmrp.p 2+                                 34.1.16.8 Customer Release Diamond Report                               Date: 04/16/26
Page:    1                                                    Arlington                                               Time: 13:16:05
sort=1

Ship-From: AT00     IAC Group - Arlington    GMA1020   143
 Customer: 01000474 GMC / SERVICE PARTS OPERATIO
  Ship-To: 01007780 NON DEST 17798.

Week# Rel Date SO        Ln ShipTo   Customer Part                  Part                         PCR 03/23/26 03/30/26 04/06/26 04/13/26 04/20/26 04/27/26 05/04/26 05/11/26 05/18/26 05/25/26 06/01/26 06/08/26 06/15/26 06/22/26 06/29/26 07/06/26 07/13/26 07/20/26 07/27/26 08/03/26 08/10/26 08/17/26 08/24/26 08/31/26
----- -------- -------- --- -------- ------------------------------ ------------------ ------------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- -------- --------
   13 03/28/26 GMA1020  143 01007780 42936541                       42936541                     0.0
   14 04/04/26 GMA1020  143 01007780 42936541                       42936541                     0.0                                                                                                                                                                                  18
   15 04/11/26 GMA1020  143 01007780 42936541                       42936541                     0.0

   
### CUSTOMER ORDER FORMAT

ictrrph0.p 99                                 34.5.3.8 Transactions By Item Summary Rpt                               Date: 04/17/26
Page:    1                                                    Arlington                                               Time: 13:14:07

Site     Item Number        Description                                      UM Type     Type1       Quantity1 Type2       Quantity2
-------- ------------------ ------------------------------------------------ -- -------- -------- ------------ -------- ------------
at00     1007279-AA                                                             MEMO     iss-so         -496.0                   0.0
AT00     1008270-AA                                                             MEMO     iss-so       -5,936.0                   0.0
AT00     1008271-AA                                                             MEMO     iss-so       -5,816.0                   0.0
AT00     20951827                                                               MEMO     iss-so            0.0                   0.0
AT00     210-BPCH                                                               MEMO     iss-so            0.0                   0.0
AT00     2665870105                                                             MEMO     iss-so            0.0                   0.0


Modern format uses 
   '''


waterfall_header = ['Week#', 'RelDate', 'SO', 'Ln', 'ShipTo', 'CustomerPart', 'Part', 'PCR']

def readWaterfallGroup(f):
    dates = []
    partGroup = True # Group all sharing a single PN
    line = ''
    group_text = []
    while partGroup:
        if 'Week#' in line:
            _, datestr = line.rsplit('PCR')
            dates = datestr[1:-1].split(' ')
            partGroup = False
        else:
            line = f.readline()
            if not line:
                break
            group_text.append(line)
    active_data = True
    group_data = []
    ruler = f.readline() # Fixed field delimiters
    if not line:
        return None, None
    widths = [len(group) for group in ruler.split()]
    
    while active_data:
        line = f.readline()
        if not line:
            break
        if line == '\n' or '+' in line:
            active_data = False
        else:
            group_data.append(parse_fixed_width(line, widths))
    df = pd.DataFrame(group_data, columns=waterfall_header + dates)
    return df.replace('', np.nan), dates


def waterfallParse(file):
    # Files need to bbe read stupidly bc big
    all_parts = pd.DataFrame([])
    with open(file) as f:
        while True:
            df, dates  = readWaterfallGroup(f)
            if (df is not None) and (len(df) > 0):
    
                melted = df.melt(id_vars=waterfall_header, 
                                value_vars=dates, 
                                var_name='OrderDate', 
                                value_name=pre['pq']).dropna(subset=[pre['pq']],
                                                                        axis=0)
                all_parts = pd.concat([all_parts, melted], ignore_index=True, axis=0)
            else:
                break
    return all_parts

import os
def consumpParse(file, 
                 cols =["Site", "Part", "Description", "UM","Type", 
                        "Type1", "Quantity1", "Type2", "Quantity2"],
                year=None, week=None):
    filename = os.path.basename(file)
    # match determines what week of the year
    if not year or not week:
        raise(ValueError("Please provide either a namepattern OR both year and week"))
    filedata = stateParse(file)
    df = pd.DataFrame(filedata, columns=cols)
    # Aggregate 
    return df