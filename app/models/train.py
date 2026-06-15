import joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error

import pandas as pd
import numpy as np
from app.prefixe import pre, dirs

pq = pre['pq']
oi = pre['oi']
oq = pre['oq']

AMT_COL = 'amount'
N_THRESHOLD = 1  # split point: Model A trains on predqty > N, Model B on <= N


def transform_target(y, objective):
    if objective == 'raw':
        return y, None
    elif objective == 'log':
        return np.sign(y) * np.log1p(np.abs(y)), None
    elif objective == 'sqrt':
        return np.sign(y) * np.sqrt(np.abs(y)), None
    raise ValueError(f"Unknown objective: {objective}")


def inverse_transform_target(y_pred, objective):
    if objective == 'raw':
        return y_pred
    elif objective == 'log':
        return np.sign(y_pred) * np.expm1(np.abs(y_pred))
    elif objective == 'sqrt':
        return np.sign(y_pred) * (np.abs(y_pred) ** 2)
    raise ValueError(f"Unknown objective: {objective}")


def score_block(actual, predicted, label, week, records):
    records.append({
        "model": label,
        "week":  week,
        "n":     len(actual),
        "r2":    r2_score(actual, predicted),
        "mae":   np.abs(actual - predicted).mean(),
        "mse":   mean_squared_error(actual, predicted),
    })


def _build_XY(df, obj, drop_cols=None):
    """Shared feature/target construction used in both train and predict paths."""
    drop = [oq, 'part'] + (drop_cols or [])
    drop = [c for c in drop if c in df.columns]
    target = (df[oq] - df[pq]) * df[AMT_COL]
    y, _ = transform_target(target, objective=obj)
    X = df.drop(columns=drop)
    X.columns = [str(c) for c in X.columns]
    return X, y


def model_train(df: pd.DataFrame, holdout_weeks: int = -1,
                plant: str='arlington',
                config: dict = None):
    """
    Train dual models (high/low predqty split) on df.

    Returns a bundle dict with models, config, and feature column names.
    If holdout_weeks > 0, also evaluates and returns scores in the bundle.
    """
    if config is None:
        config = {'obj': 'log', 'loss': 'absolute_error', 'l2': 1.0}

    obj  = config['obj']
    loss = config['loss']
    L2   = config['l2']
    N    = N_THRESHOLD

    df = df.sort_values(['part', oi]).copy()
    df['Volume'] = df[pq] * df[AMT_COL]

    X, y = _build_XY(df, obj)

    valid     = ~y.isna()
    X_valid   = X[valid]
    y_valid   = y[valid]
    df_valid  = df[valid]

    if holdout_weeks > 0:
        split_week = df[oi].max() - holdout_weeks
        train_idx  = df_valid[oi] < split_week
        test_idx   = df_valid[oi] >= split_week
    else:
        train_idx = pd.Series(True, index=df_valid.index)
        test_idx  = pd.Series(False, index=df_valid.index)

    # Model A: predqty > N
    train_hi   = train_idx & (df_valid[pq] > N)
    X_tr_A, y_tr_A = X_valid[train_hi], y_valid[train_hi]
    mA = HistGradientBoostingRegressor(loss=loss, l2_regularization=L2)
    mA.fit(X_tr_A, y_tr_A)

    # Model B: predqty <= N
    train_lo   = train_idx & (df_valid[pq] <= N)
    X_tr_B, y_tr_B = X_valid[train_lo], y_valid[train_lo]
    mB = HistGradientBoostingRegressor(loss=loss, l2_regularization=L2)
    mB.fit(X_tr_B, y_tr_B)

    bundle = {
        'plant': plant,
        'train_span': (X_valid[train_idx].min(), 
                       X_valid[train_idx].max()),
        'mA': mA,
        'mB': mB,
        'config': config,
        'size_threshold': N,
        'feature_cols': list(X_valid.columns),
    }
    print(bundle)

    if holdout_weeks > 0:
        X_test  = X_valid[test_idx]
        y_test  = y_valid[test_idx]
        amt_test = df_valid[AMT_COL][test_idx]
        pq_test  = df_valid[pq][test_idx]

        mask_hi = pq_test.values > N
        mask_lo = ~mask_hi

        preds_A = inverse_transform_target(mA.predict(X_test[mask_hi]), obj)
        preds_B = inverse_transform_target(mB.predict(X_test[mask_lo]), obj)
        y_raw   = inverse_transform_target(y_test.values, obj)

        preds = np.empty(len(X_test))
        preds[mask_hi] = preds_A
        preds[mask_lo] = preds_B

        baseline = (pq_test * amt_test).values
        records  = []
        score_block(y_raw, preds,    "Combined A+B", split_week, records)
        score_block(y_raw, baseline, "Naive baseline", split_week, records)
        bundle['scores'] = pd.DataFrame(records)

    return bundle


def predict_from_bundle(bundle, df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply a saved model bundle to a feature-ready dataframe.

    df must have the same feature columns the bundle was trained on,
    except it does NOT need oq (orderqty).  Returns df with new columns:
        adj_volume  — predicted volume adjustment
        est_orderqty — estimated order quantity (rounded)
    """
    mA   = bundle['mA']
    mB   = bundle['mB']
    N    = bundle['size_threshold']
    obj  = bundle['config']['obj']
    cols = bundle['feature_cols']

    df = df.copy()
    if 'Volume' not in df.columns:
        df['Volume'] = df[pq] * df[AMT_COL]

    drop = [c for c in [oq, 'part'] if c in df.columns]
    X = df.drop(columns=drop)
    X.columns = [str(c) for c in X.columns]

    # Align to training columns; fill any missing with NaN (HistGBR handles it)
    X = X.reindex(columns=cols)

    mask_hi = df[pq].values > N
    mask_lo = ~mask_hi

    preds = np.zeros(len(X))
    if mask_hi.any():
        preds[mask_hi] = inverse_transform_target(mA.predict(X[mask_hi]), obj)
    if mask_lo.any():
        preds[mask_lo] = inverse_transform_target(mB.predict(X[mask_lo]), obj)

    df['adj_volume']   = preds
    amt = df[AMT_COL].replace(0, np.nan)
    df['est_orderqty'] = np.round(df[pq] + preds / amt).fillna(df[pq])
    return df


def save_model(bundle: dict, name: str) -> str:
    """Persist a model bundle to saved_models/{name}.pkl. Returns the path."""
    model_dir = dirs['saved_models']
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"{name}.pkl"
    joblib.dump(bundle, path)
    return str(path)


def load_model(name: str) -> dict:
    """Load a model bundle from saved_models/{name} (with or without .pkl)."""
    if not name.endswith('.pkl'):
        name = name + '.pkl'
    path = dirs['saved_models'] / name
    return joblib.load(path)
