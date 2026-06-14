from flask import Flask, render_template, request, jsonify, redirect, url_for
import subprocess
import os
import sys
from datetime import datetime

# Make the project root importable so app.* packages resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.reporting.data_quality import coverage_report
from app.reporting.history_report import error_report
from app.core.parser import week_to_idx
import sqlite3
from app.prefixe import dirs

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
@app.route("/load", methods=["GET", "POST"])
def load():
    result = None
    error = None
    if request.method == "POST":
        # Backend expects raw .prn files placed in the watched folder;
        # this route triggers the raw→processed pipeline.
        folder = request.form.get("folder_path", "").strip()
        ok, msg = run_script(["python", "backend/load_data.py", "--folder", folder])
        if ok:
            result = msg or "Data loaded successfully."
        else:
            error = msg
    return render_template("load.html", active="load", result=result, error=error)


# ── DATA / Explore ────────────────────────────────────────────────────────────
# Supposed to be for the Github style sorta heatmap for data coverage
# we not there yet but we gon get there
@app.route("/explore", methods=["GET", "POST"])
def explore():
    return render_template("explore.html", active="explore", result=None, error=None, chart_html=None)


# ── DATA / Reports ────────────────────────────────────────────────────────────
@app.route("/reports", methods=["GET", "POST"])
def reports():
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

        try:
            since_idx = None
            until_idx = None
            if week_start:
                since_idx = parse_week_to_idx(week_start)
            if week_end:
                until_idx = parse_week_to_idx(week_end)
            if report_type == "OrderAccuracy":
                chart_html = error_report(save_to_file=save_to_file,
                                         save_loc=file_path if save_to_file else None,
                                         max_lookahead=int(max_lookahead) if max_lookahead else None,
                                         since_idx=since_idx, until_idx=until_idx,
                                         part_prefix=part_prefix,
                                         render=True)
            elif report_type == "DataCoverage":
                chart_html = coverage_report(since_idx=since_idx, until_idx=until_idx, part_prefix=part_prefix)
            else:
                error = f"Report type '{report_type}' is not yet implemented."
        except Exception as e:
            error = str(e)
    return render_template("reports.html", active="reports", result=result, error=error, chart_html=chart_html)


# ── MODEL / Predict ───────────────────────────────────────────────────────────
# idk yet but seems like we have all the necessary inputs (do we need specific date spans for order horizon???)
@app.route("/predict", methods=["GET", "POST"])
def predict():
    result = None
    error = None
    # Discover available model pkl files for the dropdown
    models = _list_models()

    if request.method == "POST":
        model      = request.form.get("model", "")
        pred_date  = request.form.get("pred_date", "")        # YYYY-MM-DD from date picker, can reformat as necessary
        part_num   = request.form.get("part_number", "").strip()
        horizon    = request.form.get("order_horizon", "24").strip()
        multi_mode = request.form.get("multi_mode") == "on"   # waterfall fill

        args = [
            "python", "backend/predict.py",
            "--model",   model,
            "--date",    pred_date,
            "--horizon", horizon,
        ]
        if part_num and not multi_mode:
            args += ["--part", part_num]
        if multi_mode:
            args += ["--waterfall"]

        ok, msg = run_script(args)
        if ok:
            result = msg   # expected: path to saved .xlsx
        else:
            error = msg

    return render_template(
        "predict.html",
        active="predict",
        models=models,
        result=result,
        error=error,
    )


# ── MODEL / Retrain ───────────────────────────────────────────────────────────
# train a new model on data based on the window provided
@app.route("/retrain", methods=["GET", "POST"])
def retrain():
    result = None
    error = None
    models = _list_models()
    if request.method == "POST":
        model          = request.form.get("model", "").strip()
        earliest_pred  = request.form.get("earliest_pred_date", "").strip()
        latest_pred    = request.form.get("latest_pred_date",   "").strip()
        earliest_order = request.form.get("earliest_order_date","").strip()
        latest_order   = request.form.get("latest_order_date",  "").strip()
        nickname       = request.form.get("model_nickname", "").strip()
        # run the training script here

        # if ok:
        #     result = msg   # expected: path to new .pkl
        # else:
        #     error = msg

    return render_template("retrain.html", active="retrain", models=models, result=result, error=error)


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

        ok, msg = run_script([
            "python", "backend/evaluate.py",
            "--model",      model,
            "--eval-start", eval_start,
            "--eval-end",   eval_end,
        ])
        if ok:
            result = msg
        else:
            error = msg

    return render_template(
        "evaluate.html",
        active="evaluate",
        models=models,
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
