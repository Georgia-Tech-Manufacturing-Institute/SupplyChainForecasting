from sklearn.ensemble import GradientBoostingRegressor, HistGradientBoostingRegressor
from sklearn.metrics import mean_pinball_loss, mean_squared_error, r2_score

import pandas as pd 
import numpy as np 
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
from app.prefixe import pre

def transform_target(y, objective):
    if objective == 'raw':
        return y, None
    elif objective == 'log':
        return np.sign(y) * np.log1p(np.abs(y)), None
    elif objective == 'sqrt':
        return np.sign(y) * np.sqrt(np.abs(y)), None
    raise ValueError(f"Unknown objective: {objective}")

def inverse_transform_target(y_pred, objective, thresh=10):
    if objective == 'raw':
        return y_pred
    elif objective == 'log':
        return np.sign(y_pred) * np.expm1(np.abs(y_pred))
    elif objective == 'sqrt':
        return np.sign(y_pred) * (np.abs(y_pred) ** 2)
    raise ValueError(f"Unknown objective: {objective}")

def score_block(actual, predicted, label, week, records):
    """Append r2/mae/mse for one prediction block."""
    records.append({
        "model": label,
        "week":  week,
        "n":     len(actual),
        "r2":    r2_score(actual, predicted),
        "mae":   np.abs(actual - predicted).mean(),
        "mse":   mean_squared_error(actual, predicted),
    })

def create_target(df, qty_cols):
    pass

pq = pre['pq']
oi = pre['oi']
oq = pre['oq']

def model_train(df: pd.DataFrame, holdout_weeks: int=-1,
                config: dict={'obj': 'log', 
                              'loss': 'absolute_error', 
                              'l2': 1.0}):
    '''
    if holdout_weeks is -1; use all data to train
    if holdout_weeks given as integer, holdout the last holdout_weeks weeks to give model validation
    '''

    obj    = config['obj']
    loss   = config['loss']
    L2     = config['l2']
    N      = 1

    results_df = []
    split_week = df[oi].max() - holdout_weeks

    df = df.sort_values(["part", oi]).copy()
    df['Volume'] = df[pq]*df.Amt1

    train_mask = df[pre['oi']] < split_week
    test_mask  = df[pre['oi']] >= split_week

    target   = (df[oq] - df[pq]) * df.Amt1
    baseline = np.zeros_like(df[pq]  * df.Amt1)

    y, thresh = transform_target(target, objective=obj)
    X = df.drop(columns=[oq, 'Part'])
    X.columns = [str(c) for c in X.columns]

    valid     = ~y.isna()
    X_valid   = X[valid]
    y_valid   = y[valid]
    df_valid  = df[valid]
    train_idx = train_mask[valid]
    test_idx  = test_mask[valid]

    X_train, y_train = X_valid[train_idx], y_valid[train_idx]
    X_test,  y_test  = X_valid[test_idx],  y_valid[test_idx]

    baseline_qty = (df_valid[pq])[test_idx]
    baseline_test = (df_valid[pq] * df_valid.Amt1)[test_idx]
    pred_qty_test =  df_valid[pq][test_idx]
    pred_amt_test =  df_valid.Amt1[test_idx]

    # ── boolean masks for the two splits ──────────────────────────────────────
    mask_lo = (pred_qty_test.values <= N)  
    mask_hi = ~mask_lo  

    # ── Model A: trained & evaluated only on PredQty > N rows ─────────────────
    train_hi = train_idx & (df_valid[pq] > N)
    X_tr_A, y_tr_A = X_valid[train_hi], y_valid[train_hi]

    mA = HistGradientBoostingRegressor(loss=loss, l2_regularization=L2)
    mA.fit(X_tr_A, y_tr_A,
           sample_weight=1 / (X_tr_A.Lookahead + 2))

    # ── Model B: trained & evaluated only on PredQty <= N rows ────────────────
    train_lo = train_idx & (df_valid[pq] <= N)
    X_tr_B, y_tr_B = X_valid[train_lo], y_valid[train_lo]

    mB = HistGradientBoostingRegressor(loss=loss, l2_regularization=L2)
    mB.fit(X_tr_B, y_tr_B,
           sample_weight=1 / (X_tr_B.Lookahead + 2))

    # ── predict on test split ─────────────────────────────────────────────────
    preds_A_raw = inverse_transform_target(mA.predict(X_test[mask_hi]), objective=obj)
    preds_B_raw = inverse_transform_target(mB.predict(X_test[mask_lo]), objective=obj)

    y_test_raw = inverse_transform_target(y_test.values, objective=obj)

    preds_combined = np.empty(len(X_test))
    preds_combined[mask_hi] = preds_A_raw
    preds_combined[mask_lo] = preds_B_raw
    predQty_combined = np.round(preds_combined/pred_amt_test)

    return predQty_combined
    # ── score every reporting slice ───────────────────────────────────────────
    score_block(y_test_raw, preds_combined,
                "Combined (A+B): all", split_week, results_df)
    score_block(y_test_raw[mask_hi],  preds_A_raw,
                f"Model A: PredQty>{N}",split_week, results_df)
    score_block(y_test_raw[mask_lo], preds_B_raw,
                f"Model B: PredQty<={N}",split_week, results_df)
    score_block(y_test_raw[mask_hi],  baseline_test.values[mask_hi],
                f"Naive: PredQty>{N}",split_week, results_df)
    score_block(y_test_raw[mask_lo], baseline_test.values[mask_lo],
                f"Naive: PredQty<={N}",split_week, results_df)
    score_block(y_test_raw, baseline_test.values,
                "Naive: all", split_week, results_df)

