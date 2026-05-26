import os
from pathlib import Path
from app.core.parser import *
import pandas as pd
from typing import NamedTuple

class ColumnMeta(NamedTuple):
    """Semantic unit tag for a group of feature columns."""
    week_cols:     list[str]   # dimensioned in weeks (lead time, lag, horizon …)
    qty_cols:      list[str]   # dimensioned in SKU units (qty, mean, std …)
    ratio_cols:    list[str]   # dimensionless ratios / proportions  [0-1 or ±]
    count_cols:    list[str]   # raw counts / cardinalities
    flag_cols:     list[str]   # binary indicators

def filter_disjoint(wf, consumption):
    # Filter out disjoint set of parts
    # If NOT disjoint, there is at least one case of a consumption/waterfall
    # that is, if a corresponding prediction is missing, its due to bad broadcasting
    # Should be used on big representative datasets 
    disjoint = pd.Index(wf.Part).symmetric_difference(pd.Index(consumption.Part))
    wf = wf[~wf.Part.isin(disjoint)]
    consumption = consumption[~consumption.Part.isin(disjoint)]
    return wf, consumption


def all_consumption_load(consum_dir):
    '''Bulk '''
    consum = []
    for year in os.listdir(consum_dir):
        year_dir  = consum_dir / year
        for week_file  in os.listdir(year_dir):
            path = year_dir / week_file
            if str(path).endswith('.parquet'):
                consum_data = pd.read_parquet(path)
                consum.append(consum_data)
    consum = pd.concat(consum, ignore_index=True)
    return consum

def bulk_consumption_parse(consump_dir,   proc_consump_dir, 
                           overwrite=False):
    '''
    Bulk loads data from raw -> processed. Loads data from allof /data/raw/Consumption into 
    Assumes structure of 
    /data/raw/Consumption/
        /year/ 
            -%ywk%w.prn
            -e.g. 25wk01.prn
        /year/ ... 
    '''
    consump_dir = Path(consump_dir)
    proc_consump_dir = Path(proc_consump_dir)
    proc_consump_dir.mkdir(parents=True, exist_ok=True)

    for consump_year in sorted(os.listdir(consump_dir)):
        year_input_dir = consump_dir / consump_year
        year_output_dir = proc_consump_dir / consump_year
        year_output_dir.mkdir(parents=True, exist_ok=True)

        if consump_year.startswith('2'):
            for file in sorted(os.listdir(year_input_dir)):
                input_path = year_input_dir / file

                pattern = re.compile( r'(?:\d+wk|Wk_)(\d+)',re.IGNORECASE)
                test = consumpParse(input_path,
                                    name_pattern= pattern, year=consump_year,
                                    cols= ["Site", "Item Number", "Description", "UM","Type", "Type1", "Quantity1", "Type2", "Quantity2"])
                yr, wk = idx_to_week(test.Orderidx[0])
                parquet_name = f"{int(yr)}_Wk_{wk:02d}.parquet"
                output_path = year_output_dir / parquet_name

                if output_path.exists() and not overwrite:
                    print(f"Skipping existing: {output_path}")
                    continue

                filt = filter_partno(test, col='Part')
                filt = filt.groupby(['Part', 'Orderidx']).sum(numeric_only=True).reset_index()
                filt.to_parquet(output_path, index=False)

def load_waterfall_week(year=None, week=None, idx=None, proc_dir=Path('data/processed/Waterfall')):
    '''
    Loads a single week of processed waterfall data; uses year and week, otherwise uses idx
    '''
    if year is None or week is None:
        if idx is None:
            raise ValueError("Must provide either year and week, or idx")
        year, week = idx_to_week(idx)
    filename = f"{year}_Wk_{week:02d}.parquet"
    filepath = proc_dir / str(year) / filename
    if not filepath.exists():
        #print(f"File {filepath} does not exist.")
        return None
    return pd.read_parquet(filepath)

def load_waterfall_span(idx_start, idx_end, proc_dir=Path('data/processed/Waterfall')):
    ''' Loads a span of processed waterfall data ; 
        week is INCLUSIVE'''
    data = []
    for idx in range(idx_start, idx_end+1):
        yr, wk = idx_to_week(idx) 
        week_data = load_waterfall_week(year=yr, week=wk, proc_dir=proc_dir)
        if week_data is not None:
            data.append(week_data)
    wf = pd.concat(data, ignore_index=True)
    wf = wf.drop_duplicates(subset=['Part', 'RelDate', 'Orderidx'])
    return wf

def bulk_waterfall_parse(waterfall_dir,
                   wf_proc_dir,
                   overwrite=False):
    '''
    Loads raw waterfall data into processed 
    Assumes structure of: 
    /data/
    -/raw/
    --/2025/
    ---/25wk06.prn
    ---/25wk07.prn
    --/2026/
    -/processed/
    '''
    print("Bulk waterfall processing: ")
    for wf_year in sorted(os.listdir(waterfall_dir)):
            if wf_year.startswith("2"): # process whole year
                print("     Processing ", wf_year)
                year_input_dir = waterfall_dir / wf_year
                year_output_dir = wf_proc_dir / wf_year
                year_output_dir.mkdir(parents=True, exist_ok=True)

                for filename in sorted(os.listdir(year_input_dir)):
                    input_path = year_input_dir / filename
                    pattern = re.compile( r'(?:\d+wk)(\d+)',re.IGNORECASE)
                    m = re.search(pattern, filename)
                    week = int(m.group(1))
                    parquet_name = f"{int(wf_year)}_Wk_{week:02d}.parquet"
                    output_path = year_output_dir / parquet_name

                    if output_path.exists() and not overwrite:
                        print(f"             Skipping existing: {filename}")
                        continue
                    print("             Parsing ", filename)
                    parsed = waterfallParse(input_path)
                    wf = waterfallProcess(parsed)
                    part_pivot = shippingMelt(wf)
                    # Only use this in the case where there is no usable data befroe a certain point (in this case,
                    # we only have order data after week 41 of 2024, so any orders cant be validated)
                    #newdata = part_pivot[(wepart_pivotekdata.Orderidx - 2024*52)>=41]

                    part_pivot.to_parquet(output_path, index=False)

def main():
    pd.set_option('future.no_silent_downcasting', True)

    parent_dir = Path(__file__).resolve().parent.parent

    #parent_dir = Path.cwd()
    data = parent_dir / 'data' 

    raw_dir = data / 'raw'
    proc_dir = data /  "processed"

    # Refresh all data
    bulk_consumption_parse(consump_dir = raw_dir /  'Consumption',
                     proc_consump_dir = proc_dir / 'Consumption', 
                     overwrite=True)


    costPrep(raw_dir / 'Cost.txt', # Just gets the first file from this directory, fix for future?
            proc_dir / 'Cost.parquet', 
            save=True, 
            overwrite=False)
    
    bulk_waterfall_parse(waterfall_dir=raw_dir / 'Waterfall', 
                   wf_proc_dir=proc_dir/'Waterfall', 
                   overwrite=True)

if __name__ == '__main__':
    main()