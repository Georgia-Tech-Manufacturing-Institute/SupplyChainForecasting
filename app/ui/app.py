from flask import Flask, render_template, request, jsonify, redirect, url_for
import subprocess
import os
# !!!! Run from command line with "python app.py"

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
    result = None
    error = None
    if request.method == "POST":
        week_start = request.form.get("week_start", "").strip()
        week_end   = request.form.get("week_end", "").strip()
        ok, msg = run_script([
            "python", "backend/explore_data.py",
            "--week-start", week_start,
            "--week-end",   week_end,
        ])
        if ok:
            result = msg
        else:
            error = msg
    return render_template("explore.html", active="explore", result=result, error=error)


# ── DATA / Reports ────────────────────────────────────────────────────────────
# as of now only provides functionality to select the report type, start/end weeks, and to save as .xlsx
@app.route("/reports", methods=["GET", "POST"])
def reports():
    result = None
    error = None
    if request.method == "POST":
        report_type = request.form.get("report_type", "OrderAccuracy")
        week_start  = request.form.get("week_start", "").strip()
        week_end    = request.form.get("week_end",   "").strip()
        ok, msg = run_script([
            "python", "backend/generate_report.py",
            "--type",       report_type,
            "--week-start", week_start,
            "--week-end",   week_end,
        ])
        if ok:
            result = msg
        else:
            error = msg
    return render_template("reports.html", active="reports", result=result, error=error)


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
    if request.method == "POST":
        earliest_pred  = request.form.get("earliest_pred_date", "").strip()
        latest_pred    = request.form.get("latest_pred_date",   "").strip()
        earliest_order = request.form.get("earliest_order_date","").strip()
        latest_order   = request.form.get("latest_order_date",  "").strip()
        # run the training script here
        ok, msg = run_script([
            "python", "backend/retrain.py",
            "--earliest-pred",  earliest_pred,
            "--latest-pred",    latest_pred,
            "--earliest-order", earliest_order,
            "--latest-order",   latest_order,
        ])
        if ok:
            result = msg   # expected: path to new .pkl
        else:
            error = msg

    return render_template("retrain.html", active="retrain", result=result, error=error)


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
    """Return available model names from the models/ directory."""
    model_dir = os.path.join(os.path.dirname(__file__), "models")
    if not os.path.isdir(model_dir):
        return ["Full Dataset (2026wk01)"]   # fallback placeholder
    return [
        f for f in os.listdir(model_dir) if f.endswith(".pkl")
    ] or ["Full Dataset (2026wk01)"]


if __name__ == "__main__":
    app.run(debug=True, port=5000)
