
# System basics

Major elements: 
- Forecast App: Flask-based interface 
 = 
- Automation flow
- 

# Basic user flows
Automated workflow: How it works when it works correctly
Manual Reporting: 
 
## Automated Workflow 
The fundamental workflow for the tool does not involve any  
1. SCDW folder updates with new data on Tuesdays (?). This includes two files per plant: ictrans and custdemand. ictrans gives all transactions recorded within the past 8 days, while custdemand gives the customer waterfall.
2. A bash script stored in routine.py. The script searches the SCDW folder for files in the convention [plantkey]_[filetype].txt where file is stored
3. 


Q: How  
## Manual reporting

The reporting module exists to provide feedback on the tool itself and model performance. The following operations are supported:

- Ordering history report: Compare historic trends 
- Model performance report: For a given trained model and test dataset, provides diagnostics about past model performance, namely 
    - ccuracy



## Filling missing week data
Based on the data mosaic shown on the Data load page, it may be evident that there is missing info. To manually load data outside of the automation pipeline, 


# Advanced user flows
This is stuff that should be left to the professionals (ie someone who knows how to roll back a version, at least). A lot of opportunities for things to 

## New plant
Needed: Historical data in a known form. Form is either the concatenated diamond reports or the automated warehouse format; the later is preferred. 

1. Register the plant source. Open' app/prefixe.py'
   - Add the name of the plant in `app/prefixe.py` in the list of "_BASE_PLANT_SOURCES'
   - Double check that there is an existing plant key 

2. Prepare the raw historical files for the plant, one file per week, named `YYYYwkWW.prn` or `.txt` (e.g. `2026wk01.prn`), for each data type you have (Waterfall, Consumption, Cost).
3. On the **Load** page, select the new plant under "Plant Source", choose the "Data Type", and upload the files (individually or as a folder). Files are copied to `data/<plant>/raw/<data type>/` and parsed into that plant's database.
4. Repeat step 3 for each data type / batch of weeks you need to backfill.
5. Confirm the load on the Home page's Plant Sources table (weeks/parts counts) or the Load page's coverage heatmap.


## Deleting a plant 
Given a plant "ExamplePlant", say we want to remove it and all its data. There is currently no UI action for this — it must be done by hand:
1. Remove raw data: delete the `data/ExamplePlant/` directory (the raw `.prn`/`.txt` files uploaded via the Load page).
2. Remove the processed database: delete `data/processed/exampleplant.db` (note the plant's database file is always named in lowercase, regardless of how the plant name is cased elsewhere).
3. Remove it from the plant list:
   - If it was added via the Home page's "Add Plant" form, remove its entry from `data/custom_plants.json`.
   - If it was added by hand, remove it from the `PLANT_SOURCES` list in `app/prefixe.py`.
4. Optionally, delete any saved models trained on that plant under `models/saved_models/` (check each bundle's `plant` field, e.g. via the Models page).
5. Restart the app so the in-memory `PLANT_SOURCES` list picks up the removal.

## Feature Development 

Model features are elaborated in app/models/features.py. There are 4 types of features, waterfall, consumption, accuracy, and part.  

The BIGGEST concern when adding new features is ENSURING that future information is NOT included. The input to both model training and prediction is a row representing a future order date (orderidx), made on a date (predictidx). Each row should contain features (columns) ONLY based on information available up to the prediction date. Incorporating future information will often improve performance, but requires information that isn't actually available for real future projections! 


feature engineering is a tantalizing method of model improvement. However, there are a lot of pitfalls within this dataset in particular. 
- Seeing into the future: This is time series 101, but just to be abundantly clear: features should NEVER learn into not available at the time of row creation. This is tricky when it comes to these datasets, because when the consumption and prediction is merged, you can accidentally look into the future if you're not careful.
  - for example: if data is sorted according to orderidx rather than predidx, then a "prior" row might be already ordered, giving unrealistic insight
- Lag != prior week. this is one i have to catch myself doing; because the customer demand is not always present for every week, sometimes the "last" value is from many weeks ago. At the time of the last week, the lagging value IS the last known prediction, but it was not forecast in the prior week. 

opportunities for new features (performed by an intern or others)
- Exogeous features: 
- 

## Moving devices

Suppose that the current information is interested 



# Areas for future improvement

- <b> Alternative cost functions</b>: Currently, the objective function can be chosen between minimizing error (number of units difference between )
   - Higher short-supply penalities: Punish the model much more if the model underpredicts stock; This will encourage the model to take fewer risks in tightness to the 
   - Part specific weights: Weight the part numbers such that the most important parts are the most  
   - New metrics for error reporting: Mean absolute error and mean squared error consider undershoot/overshoot equivalent. While weighting and penalties will just make the impact of certain parts/undershoot result in a larger MAE for the same amount of error, it might also be useful to look at this in terms of each metric individually. 
- <b>resiliency against incomplete data</b>: The current model updates on a week by week basis; we chose this because although data is updated daily, predictions are made per location, while ordering is aggregate, making validation per prediction imposible. However, this requires us to wait until all possible week data is known prior to making new predictions or validating old ones. 
- <b> Plant-to-plant model transferability </b>: current model requires part numbers to be distinct to work; duplicated part numbers results in misattributed demand/ordering. While using internal part numbers might enable data to be aggregated, consideration should be given as to the value of insight relating to this. Aggregating all customer demand for a single OEM may provide model insight on large 
- <b> Dynamic cost </b>: When cost data was delivered, I was instructed to only 
- <b> Improved Augmentation Strategies </b>: Current approach uses either last value or prior year order to project a forward customer order. While this is not the only feature used for these forward projections, these choices can be pretty impactful the further in the future projections are made. For example, for the "last value" strategy, a PN which is otherwise only ordered every other week might might end up a multi-week forecast of zero or double stock. 

