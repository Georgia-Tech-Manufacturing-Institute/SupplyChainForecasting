from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
import subprocess
import os
import sys
from datetime import datetime

# Make the project root importable so app.* packages resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.reporting.data_quality import coverage_report
from app.reporting.history_report import error_report
from app.core.parser import week_to_idx
from app.core.loader import create_connection, consumption_to_sql, waterfall_to_sql, cost_to_sql, filter_SQL
from app.models.train import model_train, save_model, load_model, predict_from_bundle
from app.models.create_dataset import build_training_dataset, build_prediction_dataset
import sqlite3
from app.prefixe import dirs, PLANT_SOURCES

# !!!! Run from command line with "python app/ui/app.py" from project root

# lots of placeholder code in the routes, just have to retool to work with our specific scripts
app = Flask(__name__)

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
                dest_dir = dirs["ext_data"] / plant_source / "raw" / data_type
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


# ── DATA / Explore ────────────────────────────────────────────────────────────
# Supposed to be for the Github style sorta heatmap for data coverage
# we not there yet but we gon get there
@app.route("/explore", methods=["GET", "POST"])
def explore():
    return render_template("explore.html", active="explore", result=None, error=None, chart_html=None)


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
    result      = None
    error       = None
    table_html  = None
    row_count   = None
    models      = _list_models()

    if request.method == "POST":
        model_name = request.form.get("model", "")
        part_num   = request.form.get("part_number", "").strip()
        plant      = request.form.get("plant_source", "arlington").strip().lower()

        try:
            bundle = load_model(model_name)
            conn   = create_connection(plant=plant)
            df     = build_prediction_dataset(conn)
            conn.close()

            if part_num:
                df = df[df['part'].str.startswith(part_num)]

            if df.empty:
                error = f"No waterfall rows found for part prefix '{part_num}'."
            else:
                preds = predict_from_bundle(bundle, df)
                row_count = len(preds)

                display_cols = ['part', 'orderidx', 'predqty', 'est_orderqty', 'adj_volume', 'amount']
                display_cols = [c for c in display_cols if c in preds.columns]
                display = preds[display_cols].copy()
                display['est_orderqty'] = display['est_orderqty'].astype(int)
                display['adj_volume']   = display['adj_volume'].round(2)

                # Save full results as CSV
                out_dir = dirs['reports']
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                csv_path = out_dir / f"predictions_{ts}.csv"
                preds.to_csv(csv_path, index=False)
                result = str(csv_path)

                # Show first 200 rows inline
                table_html = display.head(200).to_html(
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
    )


# ── MODEL / Retrain ───────────────────────────────────────────────────────────
@app.route("/retrain", methods=["GET", "POST"])
def retrain():
    result = None
    error  = None
    models = _list_models()

    if request.method == "POST":
        earliest_order = request.form.get("earliest_order_date", "").strip()
        latest_order   = request.form.get("latest_order_date",   "").strip()
        nickname       = request.form.get("model_nickname", "").strip()
        holdout_str    = request.form.get("holdout_weeks", "0").strip()
        plant          = request.form.get("plant_source", "arlington").strip().lower()

        try:
            min_oi = parse_week_to_idx(earliest_order) if earliest_order else None
            max_oi = parse_week_to_idx(latest_order)   if latest_order   else None
            holdout = int(holdout_str) if holdout_str else 0

            conn    = create_connection(plant=plant)
            df      = build_training_dataset(conn, min_orderidx=min_oi,
                                             max_orderidx=max_oi)
            conn.close()

            if df.empty:
                error = "No training data found for the specified date range."
            else:
                bundle = model_train(df, holdout_weeks=holdout)

                if not nickname:
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    nickname = f"{plant}_{ts}"

                saved_path = save_model(bundle, nickname)
                result = saved_path

        except Exception as e:
            error = str(e)

    return render_template("retrain.html", active="retrain", models=models,
                           plant_sources=PLANT_SOURCES, result=result, error=error)


# ── MODEL / Evaluate ──────────────────────────────────────────────────────────
# wip
@app.route("/evaluate", methods=["GET", "POST"])
def evaluate():
    result = None
    error = None
    models = _list_models()

    if request.method == "POST":
        model      = request.form.get("model", "")
        eval_start = request.form.get("eval_start", "").strip()
        eval_end   = request.form.get("eval_end",   "").strip()

        eval_start = parse_week_to_idx(eval_start)
        eval_end = parse_week_to_idx(eval_end)

    return render_template(
        "evaluate.html",
        active="evaluate",
        models=models,
        plant_sources=PLANT_SOURCES, 
        result=result,
        error=error,
    )


# ── util ──────────────────────────────────────────────────────────────────────
# once we have existing models, this function could populate selection dropdowns
def _list_models():
    """Return available model names from the saved_models/ directory."""
    model_dir = os.path.join(os.path.dirname(__file__), "..", "models", "saved_models")
    if not os.path.isdir(model_dir):
        return ["Full Dataset (2026wk01)"]   # fallback placeholder
    return sorted(
        f for f in os.listdir(model_dir) if f.endswith(".pkl")
    ) or ["Full Dataset (2026wk01)"]


if __name__ == "__main__":
    app.run(debug=True, port=5000)
