# main.py
from core.parser import *

def main(waterfall_path:str,
        consumption_path:str, 
        cost_path:str,
        outpath:str,\
        cf_outpath:str):

    delim = {"sep":"|", "thousands":","}

    raw_wf = pd.read_csv(waterfall_path,
                    **delim, low_memory=False)
    # per prediction from waterfall reports 
    wf = processPrediction(raw_wf)
    part_pivot = shippingMelt(wf) 

    # Load consumption data
    raw_cf = pd.read_csv(consumption_path, **delim)
    cf  = processConsumption(raw_cf).rename({'OrderWeek':ow, 
                                        'OrderYear':oy,
                                        "Quantity1": oq,
                                        }, axis=1)

    cost_map = pd.read_csv(cost_path,  **delim)
    cf = cf.merge(cost_map, on="Part", how="left") 
    cf[oi] = make_week_idx(cf, on='Order')
    cf = cf.drop([oy, ow], axis=1, errors='ignore')
    cf.to_csv(cf_outpath, index=False, sep='|')

    merged = cf.merge(part_pivot, on=["Part", oi], how="inner")
    #merged.rename(columns={'Qty': pq}, inplace=True)

    print("Initial count of instances: ", merged.shape[0])
    filtered = filterZeroDemand(merged, 
                            q1=shipping_cols, 
                            q2=oq)
    print('After demand filter: ', filtered.shape[0])

    # Infill zero for non-prediction/non-consumption 
    filtered.loc[list(filtered[filtered.Predidx.isna()].index), pq] = 0
    filtered.loc[list(filtered[filtered.Orderidx.isna()].index), oq] = 0
    filtered = filtered.dropna(subset=[pq, oq], how='all', axis=0)

    # # do we want to add more cases for thsis ? augment more predictions to show gaps
    # filtered.loc[filtered.Predidx.isna(), "PredQty"] = 0  
    # filtered.loc[filtered.Predidx.isna(), "Predidx"] = filtered.loc[filtered.Predidx.isna(), "Orderidx"].values

    filtered.to_csv(outpath, index=False, sep='|')


if __name__=='__main__':
    main()