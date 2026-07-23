import joblib
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error

import pandas as pd
import numpy as np
from app.prefixe import pre, dirs
from app.models.estimators import NaNAwareMLPRegressor

pq = pre['pq']
oi = pre['oi']
oq = pre['oq']

AMT_COL = 'amount'
N_THRESHOLD = 1  # split point: Model A trains on predqty > N, Model B on <= N
TARGET_MODES = ('raw', 'volume', 'qty')
DEFAULT_TARGET_MODE = 'qty'


def raw_target(df, target_mode):
    """
    Raw (untransformed) prediction target.
      'raw'    → direct order quantity: orderqty
      'volume' → dollar-value adjustment: (orderqty - predqty) * amount
      'qty'    → raw quantity adjustment (error): orderqty - predqty
    """
    if target_mode == 'raw':
        return df[oq]
    err = df[oq] - df[pq]
    if target_mode == 'volume':
        return err * df[AMT_COL]
    if target_mode == 'qty':
        return err
    raise ValueError(f"Unknown target_mode: {target_mode!r}. Use 'raw', 'volume', or 'qty'.")


def target_to_qty(preds_raw: np.ndarray, amt: np.ndarray, target_mode: str,
                  pq: np.ndarray = None) -> np.ndarray:
    """
    Convert an inverse-transformed raw target prediction into a qty-space delta,
    mirroring raw_target's forward transform. 'raw' mode predicts an absolute
    order quantity rather than a delta from pq, so pq must be supplied to
    recover the equivalent delta (preds_raw - pq).
    """
    if target_mode == 'volume':
        amt_safe = np.where(amt == 0, np.nan, amt)
        return preds_raw / amt_safe
    if target_mode == 'qty':
        return preds_raw
    if target_mode == 'raw':
        if pq is None:
            raise ValueError("pq is required to convert a 'raw' target prediction to a qty delta")
        return preds_raw - pq

    raise ValueError(f"Unknown target_mode: {target_mode!r}. Use 'raw', 'volume', or 'qty'.")


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
    if isinstance(actual, pd.Series):
        actual = actual.values
    if isinstance(predicted, pd.Series):
        predicted = predicted.values

    records.append({
        "model": label,
        "week":  week,
        "n":     len(actual),
        "r2":    abs(r2_score(actual, predicted)),
        "mae":   np.abs(actual - predicted).mean(),
        "mse":   mean_squared_error(actual, predicted),
    })


_OTHER_SCALE_EXCEPTIONS = set()

def scale_features(X: pd.DataFrame, qty_scale_col: str | None, wk_divisor: float = 52.0,
                   other_scale: str | None = None) -> pd.DataFrame:
    """
    Scale feature columns by suffix convention.
      _qty cols → divided row-wise by qty_scale_col (a column already in X)
      _wk  cols → divided by wk_divisor (default 52)
      other cols → scaled by max or mean across the column if other_scale is 'max' or 'mean'
                   columns in _OTHER_SCALE_EXCEPTIONS are skipped
    qty_scale_col must be present in X before calling; it is NOT dropped here.
    """
    X = X.copy()
    if qty_scale_col and qty_scale_col in X.columns:
        denom = X[qty_scale_col].replace(0, np.nan)
        qty_cols = [c for c in X.columns if str(c).endswith('_qty') and c != qty_scale_col]
        if qty_cols:
            X[qty_cols] = X[qty_cols].div(denom, axis=0)
    if wk_divisor and wk_divisor != 0:
        wk_cols = [c for c in X.columns if str(c).endswith('_wk')]
        if wk_cols:
            X[wk_cols] = X[wk_cols] / wk_divisor
    if other_scale in ('max', 'mean'):
        suffixed = {'_qty', '_wk'}
        other_cols = [
            c for c in X.columns
            if not any(str(c).endswith(s) for s in suffixed) and c not in _OTHER_SCALE_EXCEPTIONS
        ]
        for c in other_cols:
            stat = X[c].max() if other_scale == 'max' else X[c].mean()
            if stat and stat != 0:
                X[c] = X[c] / stat
    return X


def build_XY(df, obj, drop_cols=None, scale_config: dict | None = None,
             target_mode: str = DEFAULT_TARGET_MODE):
    """Shared feature/target construction used in both train and predict paths."""
    drop = [oq, 'part'] + (drop_cols or [])
    drop = [c for c in drop if c in df.columns]
    target = raw_target(df, target_mode)
    y, _ = transform_target(target, objective=obj)
    X = df.drop(columns=drop)
    X.columns = [str(c) for c in X.columns]
    if scale_config:
        X = scale_features(
            X,
            qty_scale_col=scale_config.get('qty_scale_col'),
            wk_divisor=scale_config.get('wk_divisor', 52.0),
            other_scale=scale_config.get('other_scale'),
        )
    return X, y


def make_model(config: dict):
    """Return an untrained estimator for the requested model_type."""
    mtype = config.get('model_type', 'hgb')
    if mtype == 'hgb':
        return HistGradientBoostingRegressor(
            loss=config.get('loss', 'absolute_error'),
            l2_regularization=config.get('l2', 1.0),
        )
    if mtype == 'rf':
        return RandomForestRegressor(
            n_estimators=config.get('n_estimators', 300),
            max_features=config.get('max_features', 'sqrt'),
            min_samples_leaf=config.get('min_samples_leaf', 5),
            n_jobs=-1,
            random_state=42,
        )
    if mtype == 'nn':
        return NaNAwareMLPRegressor(
            hidden_layer_sizes=config.get('hidden_layer_sizes', (128, 64, 32)),
            alpha=config.get('alpha', 1e-3),
            max_iter=config.get('max_iter', 1000),
            random_state=42,
        )
    raise ValueError(f"Unknown model_type: {mtype!r}. Use 'hgb', 'rf', or 'nn'.")


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

    obj = config.get('obj', 'log')
    N   = N_THRESHOLD

    target_mode = config.get('target_mode', DEFAULT_TARGET_MODE)
    if target_mode not in TARGET_MODES:
        raise ValueError(f"Unknown target_mode: {target_mode!r}. Use 'raw', 'volume', or 'qty'.")
    config['target_mode'] = target_mode  # normalize onto config so it's always saved in the bundle

    scale_config = None
    if config.get('scale_features'):
        scale_config = {
            'qty_scale_col':          config.get('qty_scale_col', 'predqty_max'),
            'wk_divisor':             float(config.get('wk_divisor', 52.0)),
            'other_scale': config.get('other_scale'),
        }

    df = df.sort_values(['part', oi]).copy()

    X, y = build_XY(df, obj, scale_config=scale_config, target_mode=target_mode)

    valid     = ~y.isna()
    X_valid   = X[valid]
    y_valid   = y[valid]
    df_valid  = df[valid]

    if holdout_weeks > 0:
        split_week = df[oi].max() - holdout_weeks
        train_idx  = df_valid[oi] < split_week
        test_idx   = df_valid[oi] >= split_week
        if 'augmented' in df:
            test_idx = test_idx&(df.augmented==0)
    else:
        train_idx = pd.Series(True, index=df_valid.index)
        test_idx  = pd.Series(False, index=df_valid.index)

    # Model A: predqty > N
    train_hi   = train_idx & (df_valid[pq] > N)
    X_tr_A, y_tr_A = X_valid[train_hi], y_valid[train_hi]
    mA = make_model(config)
    mA.fit(X_tr_A, y_tr_A)

    # Model B: predqty <= N
    train_lo   = train_idx & (df_valid[pq] <= N)
    X_tr_B, y_tr_B = X_valid[train_lo], y_valid[train_lo]
    mB = make_model(config)
    mB.fit(X_tr_B, y_tr_B)

    part_max_oq = df.groupby('part')[oq].max()

    bundle = {
        'plant': plant,
        'train_span': (X_valid[train_idx].min(),
                       X_valid[train_idx].max()),
        'mA': mA,
        'mB': mB,
        'config': config,
        'size_threshold': N,
        'feature_cols': list(X_valid.columns),
        'scale_config': scale_config,
        'part_max_oq': part_max_oq,
    }

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


        base_cols = ['part', oi, oq, pq]
        if 'lookahead_wk' in df_valid.columns:
            base_cols.append('lookahead_wk')
        holdout_df = df_valid[test_idx][base_cols].copy().reset_index(drop=True)
        holdout_df['naive_orderqty'] = pq_test.values
        
        # Per-row holdout data for reporting (qty space, not target_mode space)
        qty_delta = target_to_qty(preds, amt_test.values, target_mode, pq=pq_test.values)
        est_qty   = np.round(pq_test.values + qty_delta)
        est_qty   = np.where(np.isnan(est_qty), pq_test.values, est_qty)

        cap = holdout_df.part.map(part_max_oq)
        holdout_df['est_orderqty'] = pd.Series(est_qty).clip(0, cap)

        if 'lookahead_wk' not in holdout_df.columns:
            holdout_df['lookahead_wk'] = (
                df_valid[test_idx][oi].values - df_valid[test_idx]['predidx'].values
            )
        bundle['holdout_df'] = holdout_df
        records  = []
        score_block(holdout_df[oq], holdout_df['est_orderqty'], "Combined A+B", split_week, records)
        score_block(holdout_df[oq], holdout_df['naive_orderqty'], "Naive baseline", split_week, records)
        bundle['scores'] = pd.DataFrame(records)
    return bundle


def predict_from_bundle(bundle, df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply a saved model bundle to a feature-ready dataframe.

    df must have the same feature columns the bundle was trained on.
    oq (orderqty) is optional: rows may span everything since the model was
    trained, so some may already have known consumption. Returns df with new
    columns:
        adj_volume   — predicted volume adjustment
        est_orderqty — estimated order quantity (rounded)

    Every row gets est_orderqty regardless of whether oq is known. Rows where
    oq is known additionally get performance stats attached at
    result.attrs['scores'] (a DataFrame from score_block, Combined A+B vs.
    Naive baseline) so callers can report accuracy instead of treating those
    rows as blind guesses.
    """
    mA   = bundle['mA']
    mB   = bundle['mB']
    N    = bundle['size_threshold']
    obj  = bundle['config']['obj']
    target_mode = bundle['config'].get('target_mode', DEFAULT_TARGET_MODE)
    cols = bundle['feature_cols']

    scale_config = bundle.get('scale_config')

    df = df.copy()
    known_mask = df[oq].notna() if oq in df.columns else pd.Series(False, index=df.index)

    drop = [c for c in [oq, 'part'] if c in df.columns]
    X = df.drop(columns=drop)
    X.columns = [str(c) for c in X.columns]

    if scale_config:
        X = scale_features(
            X,
            qty_scale_col=scale_config.get('qty_scale_col'),
            wk_divisor=scale_config.get('wk_divisor', 52.0),
            other_scale=scale_config.get('other_scale'),
        )

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
    qty_delta = target_to_qty(preds, df[AMT_COL].values, target_mode, pq=df[pq].values)
    df['est_orderqty'] = np.round(df[pq].values + qty_delta)
    missing = df['est_orderqty'].isna()
    df.loc[missing, 'est_orderqty'] = df.loc[missing, pq]

    part_max_oq = bundle.get('part_max_oq')
    if part_max_oq is not None and 'part' in df.columns:
        cap = df['part'].map(part_max_oq)
        df['est_orderqty'] = df['est_orderqty'].clip(upper=cap, lower=0)

    if known_mask.any():
        records = []
        score_block(df.loc[known_mask, oq], df.loc[known_mask, 'est_orderqty'],
                    "Combined A+B", None, records)
        score_block(df.loc[known_mask, oq], df.loc[known_mask, pq],
                    "Naive baseline", None, records)
        df.attrs['scores'] = pd.DataFrame(records)

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
