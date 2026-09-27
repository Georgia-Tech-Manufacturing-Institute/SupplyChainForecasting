# User pattern

## Automated Workflow 

## 

## Load a new plant in 
Needed: Historical data in a known form. Form is either the concatenated diamond reports or the automated warehouse format; the later is preferred. 

1. Register the plant source. On the **Home** page, use the "Add Plant" form to enter the plant's name and submit. This:
   - Adds the name to the list of available plant sources (persisted to `data/custom_plants.json`, alongside the hardcoded defaults in `app/prefixe.py`), so it appears in the plant dropdowns on the Load, Reports, Train, Predict, and Evaluate pages.
   - Initializes the plant's SQLite database at `data/processed/<plant>.db` with the standard schema (`waterfall`, `consumption`, `cost`, `waterfall_agg`, `loaded_files` tables), ready to receive data.
   - Alternatively, a plant can be added by hand by adding its name to the `PLANT_SOURCES` list in `app/prefixe.py`; its database still gets created automatically the first time data is loaded for it (or you can call `app.core.loader.create_connection(plant)` directly).
2. Prepare the raw historical files for the plant, one file per week, named `YYYYwkWW.prn` or `.txt` (e.g. `2026wk01.prn`), for each data type you have (Waterfall, Consumption, Cost).
3. On the **Load** page, select the new plant under "Plant Source", choose the "Data Type", and upload the files (individually or as a folder). Files are copied to `data/<plant>/raw/<data type>/` and parsed into that plant's database.
4. Repeat step 3 for each data type / batch of weeks you need to backfill.
5. Confirm the load on the Home page's Plant Sources table (weeks/parts counts) or the Load page's coverage heatmap.


## Delting a plant 
Given a plant "ExamplePlant", say we want to remove it and all its data. There is currently no UI action for this — it must be done by hand:
1. Remove raw data: delete the `data/ExamplePlant/` directory (the raw `.prn`/`.txt` files uploaded via the Load page).
2. Remove the processed database: delete `data/processed/exampleplant.db` (note the plant's database file is always named in lowercase, regardless of how the plant name is cased elsewhere).
3. Remove it from the plant list:
   - If it was added via the Home page's "Add Plant" form, remove its entry from `data/custom_plants.json`.
   - If it was added by hand, remove it from the `PLANT_SOURCES` list in `app/prefixe.py`.
4. Optionally, delete any saved models trained on that plant under `models/saved_models/` (check each bundle's `plant` field, e.g. via the Models page).
5. Restart the app so the in-memory `PLANT_SOURCES` list picks up the removal.

# Areas for future improvement

- <b>resiliency against incomplete data</b>: The current model updates on a week by week basis; we chose this because although data is updated daily, predictions are made per location, while ordering is aggregate, making validation per prediction imposible. However, this requires us to wait until all possible week data is known prior to making new predictions or validating old ones. 
- <b> Plant-to-plant model transferability </b>: current model requires part numbers to be distinct to work; duplicated part numbers results in misattributed demand/ordering. While using internal part numbers might enable data to be aggregated, consideration should be given as to the value of insight relating to this. Aggregating all customer demand for a single OEM may provide model insight on large 

- <b> Augmentation Strategies </b>: Current approach uses either last value or prior year order to project a forward customer order. While this is not the only feature used for these forward projections, these choices can be pretty impactful the further in the future projections are made. For example, for the "last value" strategy, a PN which is otherwise only ordered every other week might might end up a multi-week forecast of zero or double stock. 