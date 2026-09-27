
Stores prior versions of the model. Metadata should include the span of dates and features that the model was trained with as well as performance statistics. 

MOST IMPORTANT NOTE: This set of features has room to grow and expand with the data. The BIGGEST concern when adding new features is ENSURING that future information is NOT included. The input to both model training and prediction is a row representing a future order date (orderidx), made on a date (predictidx). Each row should contain features (columns) ONLY based on information available up to the prediction date. Incorporating future information will often improve performance, but requires information that isn't actually available for real future projections! 

# features.py

Feature engineering utilities for the supply chain forecasting model. Consumes the
merged prediction/order frame (and standalone consumption/order frames) and derives
columns describing demand history, prediction stability, and seasonality.

## Column prefixes

Defined in [app/prefixe.py](app/prefixe.py) and imported as short aliases for consistency:

| alias | value      | meaning                          |
|-------|------------|-----------------------------------|
| `pi`  | `predidx`  | prediction week index             |
| `oi`  | `orderidx` | order week index                  |
| `pq`  | `predqty`  | predicted quantity                |
| `oq`  | `orderqty` | actual/ordered quantity           |

## Naming convention

Generated feature columns generally follow:

```
<originating column>_<operator>_<unit>
```

e.g. `predqty_mean_qty`, `orderqty_gap_wk`. Summary-style features instead use
`<summarizor>_<unit>`, e.g. `lookahead_wk`.

---

## `release_gp_variation`
Release-level accuracy dispersion table, one row per `reldate` (or per
`part` + `reldate`), built from an already-merged prediction/order frame and
an error/accuracy column `col`:

- `rel_mean_{col}` — mean of `col` across all parts released on a given `reldate`
- `rel_std_{col}` — std of `col` across all parts released on a given `reldate`
- `part_rel_mean_{col}` — mean of `col` for one part on one `reldate`
- `part_rel_std_{col}` — std of `col` for one part on one `reldate`

---

## Waterfall features

The dataframe returned by `waterfall_features(frame)` — the main feature set
for the part/order/prediction frame. Rows are sorted by `['part', oi, pi]`,
and every column below is added on top of the original frame:

- `lookahead_wk` — how many weeks out this prediction is from the order week
  it targets (`orderidx - predidx`).
- `predqty_lag_qty` — this part's prediction value from the row immediately
  before it (one prediction-index step back).
- `lag_ratio` — the prior prediction relative to the current one
  (`predqty_lag_qty / (predqty + 1)`); >1 means the prediction has been
  revised downward since last time, <1 means revised upward.
- `predqty_mean_qty` — running (expanding, walk-forward) average of `predqty`
  for this part, using only rows up to and including the current one when
  sorted by `predidx`.
- `predqty_std_qty` — running standard deviation of `predqty` for this part,
  same walk-forward basis as `predqty_mean_qty`.
- `predqty_cv` — coefficient of variation of the part's prediction history so
  far (`predqty_std_qty / predqty_mean_qty`; `NaN` where the running mean is 0).
- `predqty_max_qty` — the highest `predqty` this part has ever reached, up to
  and including the current row (running cumulative max).
- `total_orderidx_demand` — total predicted demand summed across all parts
  for this row's order week, using only predictions that were known as of
  this row's prediction week (no look-ahead into later revisions).
- `demand_share` — this part's share of that total order-week demand
  (`predqty / (total_orderidx_demand + 1)`).
- `n_prior_preds` — how many predictions had already been issued for this
  same part/order-week pair before this one (0 for the first prediction).
- `first_predqty` — the very first prediction quantity ever issued for this
  part/order-week pair.
- `predqty_1wkdiff_qty` — change in `predqty` from the immediately preceding
  prediction for this same part/order-week pair.
- `predqty_total_drift` — total change from the first prediction ever made
  for this part/order-week pair (`predqty - first_predqty`).
- `first_to_current_ratio` — current prediction relative to the very first
  one made for this part/order-week pair (`predqty / (first_predqty + 1)`;
  1.0 means the prediction hasn't moved since it was first issued).
- `predqty_vs_mean_ratio` — current prediction relative to this part's
  running average prediction (`predqty / (predqty_mean_qty + 1)`).
- `predqty_vs_max_ratio` — current prediction relative to this part's
  highest prediction ever (`predqty / (predqty_max_qty + 1)`).
- `_woy` — week of year for the week (e.g. `orderidx % 52`).
- `_quarter` — fiscal quarter for the week (e.g. `orderidx_woy // 13`).

`waterfall_features` accepts a `progress_cb(step, total, message)` callback
that gets invoked during the walk-forward loop underlying `demand_share`/
`total_orderidx_demand`, so a caller can surface progress in a UI while these
columns are computed.


---

## Consumption features

The dataframe returned by `consumption_features(frame)` — built from raw
consumption/order data only (`['part', orderidx, orderqty]`), independent of
any prediction history. Rows are one per part per order week, sorted by
`['part', orderidx]`:

- `orderqty_1wkdiff_qty` — change in `orderqty` from the previous order week
  for this part.
- `orderqty_4wkdiff_qty` — change in `orderqty` from 4 order weeks earlier
  for this part.
- `roll2mean_orderqty_qty` — mean `orderqty` over the trailing 2 order weeks
  for this part (current week excluded).
- `roll2std_orderqty_qty` — std of `orderqty` over the trailing 2 order weeks.
- `roll3mean_orderqty_qty` — mean `orderqty` over the trailing 3 order weeks.
- `roll3std_orderqty_qty` — std of `orderqty` over the trailing 3 order weeks.
- `roll4mean_orderqty_qty` — mean `orderqty` over the trailing 4 order weeks.
- `roll4std_orderqty_qty` — std of `orderqty` over the trailing 4 order weeks.
- `orderqty_mean_qty` — running average of `orderqty` for this part, computed
  after ordering rows by `orderqty` value itself (not chronologically) —
  effectively the mean of all order quantities up to and including the
  current one in ascending-quantity order.
- `orderqty_std_qty` — running std of `orderqty` for this part, same
  ascending-quantity ordering as `orderqty_mean_qty`.
- `orderqty_cv` — coefficient of variation from the two columns above
  (`orderqty_std_qty / orderqty_mean_qty`; `NaN` where the running mean is 0).
- `orderqty_gap_wk` — weeks elapsed since this part's previous order
  (difference in `orderidx` from the prior row for this part).
- `orderqty_gap_mean_wk` — running average of `orderqty_gap_wk` for this
  part, again ordered by `orderqty` value rather than chronologically.
- `orderqty_gap_std_wk` — running std of `orderqty_gap_wk` for this part,
  same ordering as `orderqty_gap_mean_wk`.
- `orderqty_gap_cv` — coefficient of variation of the ordering-gap columns
  above (`NaN` where the running mean is 0).

`consumption_features` returns this as a new dataframe — it does not mutate
or return the original `frame`.

---

## Accuracy features

The dataframe returned by `acc_features(real_data)` — `real_data` with
part-level historical accuracy columns merged on. For each order week `i`,
only orders that had already occurred (`orderidx < i`) are used to compute
that week's accuracy figures, so no future information leaks into a given
row. Within that prior-orders window, accuracy is measured as
`orderqty_error_qty = orderqty - predqty`. The following columns are added
to `real_data` (merged on `['part', predidx]`):

- `part_acc_mean_qty` — this part's average prediction error
  (`orderqty - predqty`) across all its orders known prior to the current
  order week.
- The following features exits for 3<= N <= 5
- `part_acc_rollmean3_qty` — this part's average prediction error, restricted
  to predictions made within 3 weeks of the order (`lookahead_wk <= 3`).
- `part_acc_rollstd3_qty` — std of the same 3-week-lookahead prediction error.

Rows belonging to the earliest order week (which has no prior orders to compute accuracy from) are skipped when building these figures, so after the merge those rows will have missing values in all `part_acc_*` columns.

---

## `date_features`
Calendar features derived from a `RelDate` datetime column:

- `ReleaseMonth` — calendar month of the release.
- `ReleaseDay` — day of year of the release.
- `ReleaseWD` — day of week of the release.
- `ReleaseOrd` — dense rank of distinct release dates, giving each unique release date a sequential ordinal.

## `id_features`
Decomposes a part-number-like identifier `Series` into per-character
categorical columns. Strips characters in `[ABEUWHG-]`, and for
abnormally long IDs (longer than `exp_length`, default 8) extracts a
starter-character indicator plus a truncated/reassembled core before splitting into fixed positions:

- `pn0` … `pn7` — one integer-coded column per identifier character position (8 positions by default), `int8`, missing values filled as 0.
- One additional `int8` (0/1) dummy column per distinct "starter" character seen among abnormally long IDs.

# feature engineering; warnings!

feature engineering is a tantalizing method of model improvement. However, there are a lot of pitfalls within this dataset in particular. 
- Seeing into the future: This is time series 101, but just to be abundantly clear: features should NEVER learn into not available at the time of row creation. This is tricky when it comes to these datasets, because when the consumption and prediction is merged, you can accidentally look into the future if you're not careful.
  - for example: if data is sorted according to orderidx rather than predidx, then a "prior" row might be already ordered, giving unrealistic insight
- Lag != prior week. this is one i have to catch myself doing; because the customer demand is not always present for every week, sometimes the "last" value is from many weeks ago. At the time of the last week, the lagging value IS the last known prediction, but it was not forecast in the prior week. 



## Future work
- Exogenous features: holidays, company stock 