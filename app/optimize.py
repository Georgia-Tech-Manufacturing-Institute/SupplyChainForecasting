import random
from datetime import datetime

import pandas as pd

from app.core.common import preformat_plant
from app.prefixe import dirs, pre, plant_key
from app.core.loader import create_connection

from app.models.create_dataset import build_training_dataset
from app.models.train import model_train, save_model
from app.core.run_logger import RunLogger
from app.routine import DEFAULT_CONFIG, select_best_config

oi = pre['oi']

# Gradient boosting search space. Losses are limited to the two that accept
# negative targets, since the default 'qty' target is an error (order - pred).
SEARCH_SPACE = {
    'model_type':       ['hgb'],
    'target_mode':      ['qty', 'raw'],
    'obj':              ['log', 'sqrt', 'raw'],
    'loss':             ['absolute_error', 'squared_error'],
    'l2':               [0.0, 0.1, 1.0, 5.0, 10.0],
    'learning_rate':    [0.03, 0.1, 0.2, 0.3],
    'max_iter':         [100, 300, 500, 1000],
    'max_leaf_nodes':   [15, 31, 63, 121, 241],
    'min_samples_leaf': [20, 50, 100],
}
DEFAULT_TRIALS = 5
DEFAULT_ORIGINS = 3        # rolling evaluation origins per trial
DEFAULT_ORIGIN_STEP = 4    # weeks between origins
HOLDOUT_WEEKS = 1          # matches runAutomation's retrain


def sample_configs(n_trials, seed=None, baseline=None):
    """Draw n_trials distinct configs from SEARCH_SPACE. `baseline` (e.g. the
    plant's current best config) is always evaluated first so the search is
    measured against what the routine would use today."""
    rng = random.Random(seed)
    configs, seen = [], set()
    if baseline:
        configs.append(dict(baseline))
        seen.add(tuple(sorted(baseline.items())))

    space_size = 1
    for vals in SEARCH_SPACE.values():
        space_size *= len(vals)
    target = min(n_trials, space_size + len(configs))
    while len(configs) < target:
        cfg = {k: rng.choice(v) for k, v in SEARCH_SPACE.items()}
        key = tuple(sorted(cfg.items()))
        if key not in seen:
            seen.add(key)
            configs.append(cfg)
    return configs


def evaluate_config(df, config, n_origins=DEFAULT_ORIGINS, step=DEFAULT_ORIGIN_STEP,
                    holdout_weeks=HOLDOUT_WEEKS, plant=None):
    """Mean holdout MAE of `config` over several rolling origins. A single
    split is too noisy to rank configs, so each origin truncates the data
    `step` weeks earlier and holds out the last `holdout_weeks` from there."""
    max_oi = df[oi].max()
    maes, naive_maes = [], []
    for k in range(n_origins):
        sub = df[df[oi] <= max_oi - k * step]
        bundle = model_train(sub, holdout_weeks=holdout_weeks, plant=plant,
                             config=dict(config))
        sc = bundle['scores'].set_index('model')['mae']
        maes.append(sc['Combined A+B'])
        naive_maes.append(sc['Naive baseline'])
    return {
        'mae':       sum(maes) / len(maes),
        'mae_std':   pd.Series(maes).std(),
        'naive_mae': sum(naive_maes) / len(naive_maes),
    }


def runOptimization(plant, n_trials=DEFAULT_TRIALS, n_origins=DEFAULT_ORIGINS,
                    step=DEFAULT_ORIGIN_STEP, seed=None):
    """Random search over SEARCH_SPACE for one plant. Whenever a trial beats
    the best score so far (starting from the plant's current config), it is
    retrained the same way runAutomation retrains and saved over this run's
    model file, so an interrupted search still leaves its best model behind
    for select_best_config() to pick up on the next routine run."""
    plant = preformat_plant(plant)

    iso_now = datetime.now().isocalendar()
    current_year, current_week = iso_now[0], iso_now[1]
    output_dir = dirs['reports'] / f"{current_year}wk{current_week:02d}"
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    model_name = f"{plant_key.get(plant, plant)}_opt_{current_year}wk{current_week:02d}_{ts}"

    run = RunLogger(plant=plant, script='optimize.py')

    try:
        conn = create_connection(plant=plant)
        try:
            with run.step('Build dataset') as rec:
                df = build_training_dataset(conn)
                rec['message'] = f'{len(df):,} training rows'
        finally:
            conn.close()

        with run.step('Hyperparameter search') as rec:
            baseline = select_best_config(plant, default=DEFAULT_CONFIG)
            configs = sample_configs(n_trials, seed=seed, baseline=baseline)
            n = len(configs)
            results = []
            best_mae, best_trial, best_bundle, n_saved = None, None, None, 0
            for i, cfg in enumerate(configs, start=1):
                best_str = f'best MAE {best_mae:.2f}; ' if best_mae is not None else ''
                run.progress(rec, f'{best_str}{n_saved} saved', current=i - 1, total=n)
                try:
                    scores = evaluate_config(df, cfg, n_origins=n_origins,
                                             step=step, plant=plant)
                except Exception as exc:
                    # One bad combination shouldn't sink the whole search
                    scores = {'mae': float('nan'), 'error': str(exc)}
                results.append({'trial': i, 'baseline': i == 1, **cfg, **scores})

                mae = scores['mae']
                if pd.isna(mae) or (best_mae is not None and mae >= best_mae):
                    continue
                best_mae, best_trial = mae, i
                if i == 1:
                    continue  # the baseline is already what the routine uses
                best_bundle = model_train(df, holdout_weeks=HOLDOUT_WEEKS, plant=plant,
                                          config=dict(cfg))
                best_bundle['search_results'] = pd.DataFrame(results)
                save_model(best_bundle, model_name)
                n_saved += 1

            results = pd.DataFrame(results).sort_values('mae').reset_index(drop=True)
            if best_trial is None:
                raise RuntimeError('Every trial failed; see search results for errors.')
            # Take the config from the original list, not the results row:
            # mixed key sets upcast the DataFrame's int columns to float.
            best_config = dict(configs[best_trial - 1])
            base_mae = results.loc[results['baseline'], 'mae'].iloc[0]
            run.progress(rec, (f'{n} trials; best MAE {best_mae:.2f} vs current '
                               f'{base_mae:.2f}; {n_saved} improvement(s) saved'),
                         current=n, total=n)

        if best_bundle is None:
            run.skip('Save model', 'No trial beat the current config')
        else:
            with run.step('Save model') as rec:
                # Re-save so the bundle carries the complete search results
                best_bundle['search_results'] = results
                save_model(best_bundle, model_name)
                rec['message'] = f'{model_name}.pkl (trial {best_trial}: {best_config})'

        with run.step('Save search results') as rec:
            res_path = output_dir / f"hparam_search_{model_name}.csv"
            results.to_csv(res_path, index=False)
            rec['message'] = str(res_path)

        print(f"Best config for {plant}: {best_config}")
        run.finish('success')
        return best_config, results
    except Exception:
        run.finish('failed')
        raise
    finally:
        # No-op once finished; catches KeyboardInterrupt/SystemExit, which
        # skip the handler above and would otherwise leave the run 'running'
        run.interrupt()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Hyperparameter search for one plant.")
    parser.add_argument('plant', help="Plant source, e.g. Arlington.")
    parser.add_argument('--trials', type=int, default=DEFAULT_TRIALS)
    parser.add_argument('--origins', type=int, default=DEFAULT_ORIGINS)
    parser.add_argument('--step', type=int, default=DEFAULT_ORIGIN_STEP)
    parser.add_argument('--seed', type=int, default=None)
    args = parser.parse_args()

    runOptimization(args.plant, n_trials=args.trials, n_origins=args.origins,
                    step=args.step, seed=args.seed)
