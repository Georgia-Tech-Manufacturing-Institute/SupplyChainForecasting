from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
import subprocess
import os
import sys
import threading
import uuid
import time
from datetime import datetime

# Make the project root importable so app.* packages resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.reporting.data_quality import coverage_report
from app.reporting.history_report import error_report
from app.reporting.core import get_coverage_counts
from app.core.parser import week_to_idx, idx_to_week
from app.core.loader import create_connection, consumption_to_sql, waterfall_to_sql, cost_to_sql, filter_SQL
from app.models.train import model_train, save_model, load_model, predict_from_bundle
from app.reporting.model_perform import holdout_report
from app.models.create_dataset import build_training_dataset, build_prediction_dataset
import sqlite3
from app.prefixe import dirs, raw_dir, PLANT_SOURCES

# !!!! Run from command line with "python app/ui/app.py" from project root

# lots of placeholder code in the routes, just have to retool to work with our specific scripts
app = Flask(__name__)

# ── training job state ────────────────────────────────────────────────────────
_JOBS: dict = {}   # job_id → {stage, pct, message, done, error, result, started}

_STAGES = {
    'queued':      ('Queued',                  0),
    'loading':     ('Loading data',            3),
    'features':    ('Walk-forward features',   8),
    'consumption': ('Consumption features',   68),
    'merging':     ('Merging & filtering',    72),
    'training_a':  ('Training Model A',       76),
    'training_b':  ('Training Model B',       88),
    'saving':      ('Saving model',           96),
    'done':        ('Complete',              100),
    'error':       ('Error',                   0),
}

def _run_training_job(job_id: str, params: dict):
    job = _JOBS[job_id]

    def report(stage: str, message: str = ''):
        _, pct = _STAGES.get(stage, ('', 0))
        job.update({'stage': stage, 'pct': pct,
                    'message': message or _STAGES[stage][0]})

    def wf_progress(step: int, total: int, msg: str):
        pct_lo, pct_hi = _STAGES['features'][1], _STAGES['consumption'][1]
        pct = pct_lo + int((pct_hi - pct_lo) * step / max(total, 1))
        job.update({'stage': 'features', 'pct': pct, 'message': msg})

    try:
        report('loading', 'Connecting to database…')
        conn = create_connection(plant=params['plant'])

        report('loading', 'Reading SQL tables…')
        df = build_training_dataset(
            conn,
            min_orderidx=params['min_oi'],
            max_orderidx=params['max_oi'],
            augment=params.get('augment', True),
            min_lookahead_to_pad=params.get('min_la_pad', 4),
            lookahead_pad=params.get('la_pad', 4),
            progress_cb=wf_progress,
        )
        conn.close()

        if df.empty:
            job.update({'error': 'No training data found for the specified date range.',
                        'done': True, 'stage': 'error'})
            return

        report('consumption', f'Dataset ready — {len(df):,} rows')
        report('merging',     'Filtering and finalising features…')

        report('training_a', f'Fitting Model A (predqty > {1})…')
        bundle = model_train(df, holdout_weeks=params['holdout'],
                             plant=params['plant'],
                             config={'obj': 'log', 'loss': 'absolute_error', 'l2': 1.0,
                                     'model_type': params.get('model_type', 'hgb')})

        report('training_b', 'Fitting Model B (predqty ≤ 1)…')

        report('saving', 'Persisting model to disk…')
        nickname = params['nickname']
        if not nickname:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            nickname = f"{params['plant']}_{params.get('model_type')}_{ts}"

        saved_path = save_model(bundle, nickname)
        elapsed = round(time.time() - job['started'])

        job.update({
            'stage':   'done',
            'pct':     100,
            'message': f'Saved as {nickname}.pkl',
            'result':  saved_path,
            'elapsed': elapsed,
            'done':    True,
        })

    except Exception as exc:
        import traceback
        job.update({
            'stage':     'error',
            'pct':       job.get('pct', 0),
            'message':   str(exc),
            'traceback': traceback.format_exc(),
            'error':     str(exc),
            'done':      True,
        })


# ── helpers ──────────────────────────────────────────────────────────────────
# use in other funcs, pass in all command line args for whatever function needs to be executed
def run_script(script_args: list[str]) -> tuple[bool, str]:
    """Run a backend script and return (success, output_or_error)."""
    try:
        result = subprocess.run(
            script_args,
            capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip() or result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Operation timed out."
    except FileNotFoundError as e:
        return False, f"Script not found: {e}"

import re

def parse_week_to_idx(week):
    m = re.fullmatch(r"(\d{4})-W(\d{2})", week)
    return week_to_idx(int(m.group(1)), int(m.group(2)))



# ── index ─────────────────────────────────────────────────────────────────────
# HTML base route, just redirects to Load
@app.route("/")
def index():
    return redirect(url_for("load"))


# ── DATA / Load ───────────────────────────────────────────────────────────────
# GET method returns the UI page
# POST executes when the "Load Data" button is clicked
_NAME_RE = re.compile(r'^\d{4}wk\d{2}\.(prn|txt)$', re.IGNORECASE)

@app.route("/load", methods=["GET", "POST"])
def load():
    result = None
    error = None
    if request.method == "POST":
        plant_source = request.form.get("plant_source", "").strip()
        data_type    = request.form.get("data_type",    "").strip()
        files        = [f for f in request.files.getlist("files") if f.filename]

        if not plant_source or not data_type:
            error = "Plant Source and Data Type are required."
        elif not files:
            error = "No files selected."
        else:
            bad = [os.path.basename(f.filename) for f in files
                   if not _NAME_RE.match(os.path.basename(f.filename))]
            if bad:
                error = f"Invalid filename(s): {', '.join(bad)}. Expected format: 2026wk01.prn"
            else:
                dest_dir = raw_dir(plant_source, data_type)
                print(dest_dir)
                dest_dir.mkdir(parents=True, exist_ok=True)
                for f in files:
                    f.save(dest_dir / os.path.basename(f.filename))
                try:
                    conn = create_connection(plant=plant_source.lower())
                    if data_type == "Consumption":
                        consumption_to_sql(conn, dest_dir)
                    elif data_type == "Waterfall":
                        waterfall_to_sql(conn, dest_dir)
                    elif data_type == "Cost":
                        cost_to_sql(conn, dest_dir)
                    conn.close()
                    result = f"Saved and processed {len(files)} file(s) → {plant_source}/raw/{data_type}."
                except Exception as e:
                    error = str(e)

    return render_template("load.html", active="load", result=result, error=error,
                           plant_sources=PLANT_SOURCES)


@app.route("/load/coverage/<plant>")
def load_coverage(plant):
    if plant not in [p.lower() for p in PLANT_SOURCES]:
        return jsonify({"error": "Unknown plant"}), 404
    wf, cf = get_coverage_counts(plant)
    return jsonify({"wf": wf, "cf": cf})


# ── DATA / Reports ────────────────────────────────────────────────────────────
@app.route("/reports", methods=["GET", "POST"])
def reports():
    current_week = datetime.now().strftime("%G-W%V")
    result = None
    error = None
    chart_html = None
    if request.method == "POST":
        report_type = request.form.get("report_type", "OrderAccuracy")
        week_start  = request.form.get("week_start", "").strip()
        week_end    = request.form.get("week_end",   "").strip()
        max_lookahead = request.form.get("max_lookahead", "").strip()
        save_to_file = request.form.get("save_to_file") == "1"
        file_path = request.form.get("file_path", "").strip()
        part_prefix = request.form.get("part_prefix", "").strip() or None
        plant = request.form.get("plant_source", "arlington").strip() 
        try:
            since_idx = None
            until_idx = None
            if week_start:
                since_idx = parse_week_to_idx(week_start)
            if week_end:
                until_idx = parse_week_to_idx(week_end)
            if report_type == "OrderAccuracy":
                chart_html = error_report(plant=plant,save_to_file=save_to_file,
                                         save_loc=file_path if save_to_file else None,
                                         max_lookahead=int(max_lookahead) if max_lookahead else None,
                                         since_idx=since_idx, until_idx=until_idx,
                                         part_prefix=part_prefix,
                                         render=True)
            elif report_type == "DataCoverage":
                chart_html = coverage_report(plant=plant, since_idx=since_idx, until_idx=until_idx, part_prefix=part_prefix)
            else:
                error = f"Report type '{report_type}' is not yet implemented."
        except Exception as e:
            error = str(e)
    return render_template("reports.html", active="reports", result=result, error=error,
                           chart_html=chart_html, current_week=current_week,
                           plant_sources=PLANT_SOURCES)


# ── MODEL / Predict ───────────────────────────────────────────────────────────
@app.route("/predict", methods=["GET", "POST"])
def predict():
    result        = None
    error         = None
    table_html    = None
    row_count     = None
    models        = _list_models()
    current_week  = datetime.now().strftime("%G-W%V")

    if request.method == "POST":
        model_name     = request.form.get("model", "")
        plant          = request.form.get("plant_source", "arlington").strip().lower()
        pred_weeks_raw = request.form.get("pred_weeks", "").strip()
        aug_lookahead  = int(request.form.get("aug_lookahead", "0") or 0)
        save_to_file   = request.form.get("save_to_file") == "1"
        file_path      = request.form.get("file_path", "").strip()

        try:
            predidxs = None
            if pred_weeks_raw:
                weeks    = [w.strip() for w in re.split(r'[,\s]+', pred_weeks_raw) if w.strip()]
                predidxs = [parse_week_to_idx(w) for w in weeks]

            bundle = load_model(model_name)
            conn   = create_connection(plant=plant)
            df     = build_prediction_dataset(conn, predidxs=predidxs,
                                              aug_lookahead=aug_lookahead)
            conn.close()

            if df.empty:
                error = "No waterfall rows found for the specified parameters."
            else:
                import pandas as pd
                preds = predict_from_bundle(bundle, df)
                row_count = len(preds)

                # Build pivot: naive (predqty) vs model (est_qty)
                out = preds.sort_values('predidx').copy()
                out[['year', 'week']] = pd.DataFrame(
                    out['orderidx'].apply(idx_to_week).tolist(),
                    index=out.index
                )
                out = out.rename(columns={'est_orderqty': 'ModelQty',
                                          'predqty': 'WaterfallQty'})
                pivot = out.pivot_table(
                    index=['part'],
                    columns=['week', 'year'],
                    values=['WaterfallQty', 'ModelQty'],
                    aggfunc='last',
                )
                swapped = pivot.swaplevel(axis=1, i=0, j=-1)
                pivot = swapped.sort_index(axis=1)       
                         
                out_dir = dirs['reports']
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')

                if save_to_file:
                    fname = file_path or f"predictions_{ts}.xlsx"
                    if not fname.endswith('.xlsx'):
                        fname += '.xlsx'
                    save_path = out_dir / fname
                    pivot.to_excel(save_path)
                    result = str(save_path)

                display = pivot.copy()
                # display['est_qty']    = display['est_qty'].astype(int)
                # display['adj_volume'] = display['adj_volume'].round(2)
                table_html = display.head(50).to_html(
                    index=False, classes='pred-table', border=0
                )

        except Exception as e:
            error = str(e)

    return render_template(
        "predict.html",
        active="predict",
        models=models,
        plant_sources=PLANT_SOURCES,
        result=result,
        error=error,
        table_html=table_html,
        row_count=row_count,
        current_week=current_week,
    )


# ── MODEL / Retrain ───────────────────────────────────────────────────────────
@app.route("/retrain", methods=["GET"])
def retrain():
    return render_template("retrain.html", active="retrain",
                           models=_list_models(), plant_sources=PLANT_SOURCES)


@app.route("/retrain/start", methods=["POST"])
def retrain_start():
    earliest_order = request.form.get("earliest_order_date", "").strip()
    latest_order   = request.form.get("latest_order_date",   "").strip()
    try:
        model_type = request.form.get("model_type", "hgb").strip()
        if model_type not in ('hgb', 'rf', 'nn'):
            model_type = 'hgb'
        params = {
            'plant':      request.form.get("plant_source", "arlington").strip().lower(),
            'min_oi':     parse_week_to_idx(earliest_order) if earliest_order else None,
            'max_oi':     parse_week_to_idx(latest_order)   if latest_order   else None,
            'holdout':    int(request.form.get("holdout_weeks", "0") or 0),
            'nickname':   request.form.get("model_nickname", "").strip(),
            'augment':    request.form.get("augment") == "1",
            'min_la_pad': int(request.form.get("min_part_lookahead_to_pad", 4) or 4),
            'la_pad':     int(request.form.get("lookahead_padding", 4) or 4),
            'model_type': model_type,
        }
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400

    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {
        'stage': 'queued', 'pct': 0, 'message': 'Queued…',
        'done': False, 'error': None, 'result': None,
        'started': time.time(), 'elapsed': None,
    }
    threading.Thread(target=_run_training_job, args=(job_id, params),
                     daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route("/retrain/status/<job_id>")
def retrain_status(job_id):
    job = _JOBS.get(job_id)
    if job is None:
        return jsonify({'error': 'Job not found'}), 404
    elapsed = round(time.time() - job['started']) if not job['done'] else job.get('elapsed', 0)
    return jsonify({**job, 'elapsed': elapsed})


# ── MODEL / Evaluate ──────────────────────────────────────────────────────────
@app.route("/evaluate", methods=["GET", "POST"])
def evaluate():
    error      = None
    chart_html = None
    models     = _list_models()

    if request.method == "POST":
        model_name = request.form.get("model", "")
        try:
            bundle     = load_model(model_name)
            chart_html = holdout_report(bundle)
            if not chart_html:
                error = "This model was not trained with a holdout set — retrain with Holdout Weeks > 0 to generate a performance report."
        except Exception as e:
            error = str(e)

    return render_template(
        "evaluate.html",
        active="evaluate",
        models=models,
        plant_sources=PLANT_SOURCES,
        chart_html=chart_html,
        error=error,
    )


# ── util ──────────────────────────────────────────────────────────────────────
# once we have existing models, this function could populate selection dropdowns
def _list_models():
    """Return available model names from the saved_models/ directory."""
    model_dir = dirs['saved_models']
    if not model_dir.is_dir():
        return ["Full Dataset (2026wk01)"]   # fallback placeholder
    return sorted(
        f for f in os.listdir(model_dir) if f.endswith(".pkl")
    ) or ["Full Dataset (2026wk01)"]


if __name__ == "__main__":
    app.run(debug=True, port=5000)
