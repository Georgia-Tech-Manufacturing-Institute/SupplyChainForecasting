import os
import shutil
from glob import glob
from datetime import datetime

from app.core.common import preformat_plant
from app.prefixe import dirs, raw_dir, plant_key
from app.core.loader import (
    create_connection, 
    autoConsumption, autoWaterfall
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


def runAutomation(plant, autodir, archive_dir, title=None):
    plant = preformat_plant(plant)
    archive_dir = raw_dir(plant, data_type)
    archive_dir.mkdir(parents=True, exist_ok=True)

    report_dir = dirs['reports']
    # Get current ISO week and make a folder in
    iso_now = datetime.now().isocalendar()
    current_year = iso_now[0]
    current_week = iso_now[1]
    # Create a folder for outputs
    output_dir = report_dir / f"{current_year}wk{current_week:02d}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Tracks each step's pass/fail outcome to logs/routine_runs/ for the
    # Run History page in the UI.
    run = RunLogger(plant=plant)

    try:
        # Create an error report that includes the last 13 weeks of consumption data
        with run.step('Generate error report'):
            # TODO fix time span?
            error_report(plant=plant,
                         save_to_file=True, 
                         save_loc=output_dir,
                         )
        conn = create_connection(plant=plant.lower())

        loaded_types = []
        try:
            # check if file needs to be loaded
            # process consumption and or waterfall data from file if available
            for data_type in DATA_TYPES:
                step_name = f'Load {data_type}'

                items_to_move = []
                with run.step(step_name) as rec:
                    # for filepath in files:
                    #     shutil.copy(filepath, dest_dir / os.path.basename(filepath))

                    if data_type == 'Consumption':
                        plant_file, _, _ = autoConsumption(conn, folder=autodir,
                                        plant= plant)
                    elif data_type == 'Waterfall':
                        plant_file, _  =  autoWaterfall(conn, folder=autodir,
                                      plant = plant)
                        #waterfall_to_sql(conn, dest_dir)
                    # elif data_type == 'Cost':
                    #     cost_to_sql(conn, dest_dir)
                    else:
                        print("Unknown data ")
                    # When files are read successfully, move the file to an archive folder
                    archive_dest = os.path.join(archive_dir, data_type)
                    os.makedirs(archive_dest, exist_ok=True)
                    
                    if title:
                        archive_name = title
                    else:
                        ctime = os.path.getctime(plant_file)
                        dt = datetime.fromtimestamp(ctime)
                        archive_name = f"{dt.isocalendar().year}wk{dt.isocalendar().week}"
                    shutil.move(plant_file, os.path.join(archive_dest, archive_name))

                    rec['message'] = f'{data_type} loaded and archived'

                loaded_types.append(data_type)
        finally:
            conn.close()

        if not loaded_types:
            msg = f"No new files found in {autodir} for {plant}; skipping retrain/predict."
            print(msg)
            run.finish('no_new_data')
            return

        # Retrain the model with fresh data, leaving one week as holdout
        conn = create_connection(plant=plant.lower())
        try:
            with run.step('Train model') as rec:
                # After data is loaded, review past models and choose the best performing parameter set.
                config = select_best_config(plant)
                train_df = build_training_dataset(conn)
                bundle = model_train(train_df, holdout_weeks=1, plant=plant, config=config)
                rec['message'] = f'{len(train_df):,} training rows'

            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            model_name = f"{plant}_{current_year}wk{current_week:02d}_{ts}"
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


def main():
    for plant in plant_key.keys():
        runAutomation(plant, autodir=)

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Weekly load / retrain / predict routine.")
    parser.add_argument('incoming_folder', help="Folder to check for new data files (with Consumption/Waterfall/Cost subfolders).")
    parser.add_argument('plant', help="Plant source, e.g. Arlington or Hermasillo.")
    parser.add_argument('archive_folder', help="Folder to move processed files into.")
    args = parser.parse_args()

    main(args.incoming_folder, args.plant, args.archive_folder)
