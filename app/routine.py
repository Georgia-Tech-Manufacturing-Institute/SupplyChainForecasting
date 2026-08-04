import os
import shutil
from glob import glob
from datetime import datetime

from app.prefixe import dirs, raw_dir
from app.core.loader import (
    create_connection, consumption_to_sql, waterfall_to_sql, cost_to_sql,
)
from app.models.create_dataset import build_training_dataset, build_prediction_dataset
from app.models.train import model_train, save_model, load_model, predict_from_bundle
from app.reporting.history_report import error_report
from app.core.run_logger import RunLogger

DATA_TYPES = ['Consumption', 'Waterfall', 'Cost']
DEFAULT_CONFIG = {'obj': 'log', 'loss': 'absolute_error', 'l2': 1.0}
DEFAULT_LOOKAHEAD_WEEKS = 4


def select_best_config(plant, default=None):
    """Look at previously saved models for this plant and reuse the config
    of whichever had the lowest holdout MAE. Falls back to `default` if no
    prior model for this plant has holdout scores."""
    default = default or DEFAULT_CONFIG
    model_dir = dirs['saved_models']
    if not model_dir.is_dir():
        return default

    best_config, best_mae = default, None
    for fname in os.listdir(model_dir):
        if not fname.endswith('.pkl'):
            continue
        try:
            bundle = load_model(fname)
        except Exception:
            continue
        if bundle.get('plant') != plant or 'scores' not in bundle:
            continue
        row = bundle['scores'][bundle['scores']['model'] == 'Combined A+B']
        if row.empty:
            continue
        mae = row['mae'].iloc[0]
        if best_mae is None or mae < best_mae:
            best_mae, best_config = mae, bundle['config']
    return best_config


def main(f, plant_source, archive_folder):
    report_dir = dirs['reports']
    # Get current ISO week and make a folder in
    iso_now = datetime.now().isocalendar()
    current_year = iso_now[0]
    current_week = iso_now[1]
    output_dir = report_dir / f"{current_year}wk{current_week:02d}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Tracks each step's pass/fail outcome to logs/routine_runs/ for the
    # Run History page in the UI.
    run = RunLogger(plant=plant_source)

    try:
        # Create an error report that includes the last 13 weeks of consumption data
        with run.step('Generate error report'):
            error_report(plant=plant_source,
                         save_to_file=True, save_loc=output_dir,
                         )

        # look at items in folder - read the newest item
        # `f` mirrors the raw data layout (data/{plant}/{DataType}): one
        # subfolder per data type, each holding new .prn files to load
        incoming_dir = f
        archive_dir = archive_folder
        conn = create_connection(plant=plant_source.lower())

        loaded_types = []
        try:
            # check if file needs to be loaded
            # process consumption and or waterfall data from file if available
            for data_type in DATA_TYPES:
                step_name = f'Load {data_type}'
                src_dir = os.path.join(incoming_dir, data_type)
                files = glob(os.path.join(src_dir, '*.prn'))
                if len(files) == 0:
                    run.skip(step_name, 'No new files found')
                    continue

                with run.step(step_name) as rec:
                    dest_dir = raw_dir(plant_source, data_type)
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    for filepath in files:
                        shutil.copy(filepath, dest_dir / os.path.basename(filepath))

                    if data_type == 'Consumption':
                        consumption_to_sql(conn, dest_dir)
                    elif data_type == 'Waterfall':
                        waterfall_to_sql(conn, dest_dir)
                    elif data_type == 'Cost':
                        cost_to_sql(conn, dest_dir)

                    # When files are read successfully, move the file to an archive folder
                    archive_dest = os.path.join(archive_dir, data_type)
                    os.makedirs(archive_dest, exist_ok=True)
                    for filepath in files:
                        shutil.move(filepath, os.path.join(archive_dest, os.path.basename(filepath)))

                    rec['message'] = f'{len(files)} file(s) loaded and archived'

                loaded_types.append(data_type)
                print(f"Loaded and archived {len(files)} {data_type} file(s) for {plant_source}.")
        finally:
            conn.close()

        if not loaded_types:
            msg = f"No new files found in {incoming_dir} for {plant_source}; skipping retrain/predict."
            print(msg)
            run.finish('no_new_data')
            return

        # Retrain the model with fresh data, leaving one week as holdout
        conn = create_connection(plant=plant_source.lower())
        try:
            with run.step('Train model') as rec:
                # After data is loaded, review past models and choose the best performing parameter set.
                config = select_best_config(plant_source)
                train_df = build_training_dataset(conn)
                bundle = model_train(train_df, holdout_weeks=1, plant=plant_source, config=config)
                rec['message'] = f'{len(train_df):,} training rows'

            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            model_name = f"{plant_source}_{current_year}wk{current_week:02d}_{ts}"
            with run.step('Save model') as rec:
                save_model(bundle, model_name)
                rec['message'] = f'{model_name}.pkl'

            # Use the new model to perform predictions; push the predictions to the output_dir
            with run.step('Generate predictions') as rec:
                predict_df = build_prediction_dataset(conn, aug_lookahead=DEFAULT_LOOKAHEAD_WEEKS)
                predictions = predict_from_bundle(bundle, predict_df)
                rec['message'] = f'{len(predictions):,} predictions'
        finally:
            conn.close()

        with run.step('Save predictions') as rec:
            pred_path = output_dir / f"predictions_{model_name}.xlsx"
            predictions.to_excel(pred_path, index=False)
            rec['message'] = str(pred_path)

        print(f"Saved model {model_name}.pkl and predictions to {output_dir}.")
        run.finish('success')
    except Exception:
        run.finish('failed')
        raise


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Weekly load / retrain / predict routine.")
    parser.add_argument('incoming_folder', help="Folder to check for new data files (with Consumption/Waterfall/Cost subfolders).")
    parser.add_argument('plant_source', help="Plant source, e.g. Arlington or Hermasillo.")
    parser.add_argument('archive_folder', help="Folder to move processed files into.")
    args = parser.parse_args()

    main(args.incoming_folder, args.plant_source, args.archive_folder)
