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
from app.prefixe import dirs, raw_dir, PLANT_SOURCES, pre

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
            min_orderidx=params.get('min_oi'),
            max_orderidx=params.get('max_oi'),
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
                             config={'obj':  params.get('objective', 'log'),
                                     'loss': params.get('loss', 'absolute_error'),
                                     'l2':   params.get('l2', 1.0),
                                     'target_mode':    params.get('target_mode', 'qty'),
                                     'model_type':     params.get('model_type', 'hgb'),
                                     'scale_features': params.get('scale_features', False),
                                     'qty_scale_col':  params.get('qty_scale_col', 'predqty_max'),
                                     'wk_divisor':     params.get('wk_divisor', 52.0)})

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



# ── index / home ──────────────────────────────────────────────────────────────
CHANGELOG_PATH = os.path.join(os.path.dirname(__file__), '..', 'changelog.txt')

def parse_changelog(path=CHANGELOG_PATH):
    """Parse changelog.txt into a list of entries (newest first).

    Format, one block per entry separated by a blank line:
        <version> | <date> | <summary>
        - <bullet point>
        - <bullet point>
    Lines starting with '#' are comments and ignored.
    """
    if not os.path.exists(path):
        return []

    with open(path, encoding="utf-8") as f:
        raw = f.read()

    entries = []
    for block in raw.split("\n\n"):
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            continue

        header, *bullets = lines
        parts = [p.strip() for p in header.split("|")]
        if len(parts) != 3:
            continue

        version, date, summary = parts
        entries.append({
            "version": version,
            "date": date,
            "summary": summary,
            "bullets": [b.lstrip("-").strip() for b in bullets if b.strip().startswith("-")],
        })

    return entries

@app.route("/")
def index():
    return redirect(url_for("home"))

@app.route("/home")
def home():
    return render_template("home.html", active="home", changelog=parse_changelog())


@app.route("/userguide")
def userguide():
    return render_template("userguide.html", active="userguide")


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


# ── MODEL / Explore ───────────────────────────────────────────────────────
def _week_label(idx):
    """Convert an orderidx integer to a human-readable 'YYYY Wknn' string."""
    try:
        year, week = idx_to_week(int(idx))
        return f"{year} Wk{week:02d}"
    except Exception:
        return str(idx)


def _load_model_metadata():
    """Return a list of dicts summarising every saved model bundle."""
    model_dir = dirs['saved_models']
    rows = []
    if not model_dir.is_dir():
        return rows
    for fname in sorted(os.listdir(model_dir)):
        if not fname.endswith('.pkl'):
            continue
        try:
            bundle = load_model(fname)
            cfg = bundle.get('config', {})
            span = bundle.get('train_span')  # tuple of (min_series, max_series)

            trained_at = datetime.fromtimestamp((model_dir / fname).stat().st_mtime)

            min_oi = max_oi = None
            if span is not None:
                try:
                    min_oi = int(span[0]['orderidx'])
                    max_oi = int(span[1]['orderidx'])
                except Exception:
                    pass

            test_weeks = None
            holdout_df = bundle.get('holdout_df')
            if holdout_df is not None and pre['oi'] in holdout_df.columns:
                test_weeks = int(holdout_df[pre['oi']].nunique())

            scores_model = scores_naive = None
            if 'scores' in bundle:
                sc = bundle['scores']
                m_row = sc[sc['model'] == 'Combined A+B']
                n_row = sc[sc['model'] == 'Naive baseline']
                if not m_row.empty:
                    scores_model = {
                        'r2':  abs(round(float(m_row['r2'].iloc[0]),  4)),
                        'mae': round(float(m_row['mae'].iloc[0]), 2),
                        'mse': round(float(m_row['mse'].iloc[0]), 2),
                    }
                if not n_row.empty:
                    scores_naive = {
                        'r2':  round(float(n_row['r2'].iloc[0]),  4),
                        'mae': round(float(n_row['mae'].iloc[0]), 2),
                        'mse': round(float(n_row['mse'].iloc[0]), 2),
                    }

            rows.append({
                'name':         fname,
                'plant':        bundle.get('plant', '—'),
                'model_type':   cfg.get('model_type', 'hgb').upper(),
                'objective':    cfg.get('obj', '—'),
                'target_mode':  cfg.get('target_mode', '—'),
                'scaled':       bool(bundle.get('scale_config')),
                'train_start':  _week_label(min_oi) if min_oi is not None else '—',
                'train_end':    _week_label(max_oi) if max_oi is not None else '—',
                'trained_at':   trained_at.strftime('%Y-%m-%d %H:%M'),
                'test_weeks':   test_weeks,
                'n_features':   len(bundle.get('feature_cols', [])),
                'has_holdout':  'scores' in bundle,
                'scores_model': scores_model,
                'scores_naive': scores_naive,
            })
        except Exception as exc:
            rows.append({'name': fname, 'error': str(exc)})
    return rows


@app.route("/explore")
def explore():
    models = _load_model_metadata()
    return render_template("explore.html", active="explore", models=models)


def _iso_week_label(idx):
    """Convert an orderidx integer to an ISO-ish 'YYYY-Wnn' string (matches
    the format expected by the Prediction Week(s) input)."""
    if idx is None:
        return None
    try:
        year, week = idx_to_week(int(idx))
        return f"{year}-W{week:02d}"
    except Exception:
        return None


@app.route("/models/<name>/info")
def model_info(name):
    """Summarise a saved model's trained/validation/prediction week ranges,
    for the Predict page sidebar. Trained weeks come from the bundle;
    the prediction horizon (if a plant is given) comes from that plant's
    current waterfall snapshot."""
    plant = request.args.get('plant', '').strip().lower()
    try:
        bundle = load_model(os.path.basename(name))
    except Exception as exc:
        return jsonify({'error': str(exc)}), 404

    span = bundle.get('train_span')
    train_start_idx = train_end_idx = None
    if span is not None:
        try:
            train_start_idx = int(span[0]['orderidx'])
            train_end_idx   = int(span[1]['orderidx'])
        except Exception:
            pass

    holdout_df = bundle.get('holdout_df')
    val_start_idx = val_end_idx = None
    if holdout_df is not None and not holdout_df.empty:
        val_start_idx = int(holdout_df['orderidx'].min())
        val_end_idx   = int(holdout_df['orderidx'].max())

    if val_end_idx is not None:
        predict_start_idx = val_end_idx + 1
    elif train_end_idx is not None:
        predict_start_idx = train_end_idx + 1
    else:
        predict_start_idx = None

    predict_end_idx = None
    if plant:
        try:
            conn = create_connection(plant=plant)
            wf = filter_SQL(conn, table='waterfall_agg')
            conn.close()
            if not wf.empty:
                latest_pi = wf['predidx'].max()
                predict_end_idx = int(wf.loc[wf['predidx'] == latest_pi, 'orderidx'].max())
                if predict_start_idx is not None and predict_end_idx < predict_start_idx:
                    predict_end_idx = None
        except Exception:
            predict_end_idx = None

    return jsonify({
        'train_start':      _iso_week_label(train_start_idx),
        'train_end':        _iso_week_label(train_end_idx),
        'has_holdout':       val_start_idx is not None,
        'validation_start': _iso_week_label(val_start_idx),
        'validation_end':   _iso_week_label(val_end_idx),
        'predict_start':    _iso_week_label(predict_start_idx),
        'predict_end':      _iso_week_label(predict_end_idx),
    })


@app.route("/models/<name>/config")
def model_retrain_config(name):
    """Return the training parameters used for a saved model, for the
    'Retrain' button on the Explore page to autopopulate the Train form."""
    try:
        bundle = load_model(os.path.basename(name))
    except Exception as exc:
        return jsonify({'error': str(exc)}), 404

    cfg = bundle.get('config', {}) or {}
    scale_cfg = bundle.get('scale_config') or {}
    span = bundle.get('train_span')

    def _iso_week(idx):
        if idx is None:
            return ''
        try:
            year, week = idx_to_week(int(idx))
            return f"{year}-W{week:02d}"
        except Exception:
            return ''

    min_oi = max_oi = None
    if span is not None:
        try:
            min_oi = int(span[0]['orderidx'])
            max_oi = int(span[1]['orderidx'])
        except Exception:
            pass

    return jsonify({
        'plant_source':        bundle.get('plant', ''),
        'model_type':          str(cfg.get('model_type', 'hgb')).lower(),
        'earliest_order_date': _iso_week(min_oi),
        'latest_order_date':   _iso_week(max_oi),
        'target_mode':         cfg.get('target_mode', 'qty'),
        'objective':           cfg.get('obj', 'log'),
        'loss':                cfg.get('loss', 'absolute_error'),
        'l2':                  cfg.get('l2', 1.0),
        'scale_features':      bool(bundle.get('scale_config')),
        'qty_scale_col':       scale_cfg.get('qty_scale_col', 'predqty_max'),
        'wk_divisor':          scale_cfg.get('wk_divisor', 52),
        'other_scale':         scale_cfg.get('other_scale') or '',
    })


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
_PREDICT_STAGES = {
    'queued':      ('Queued',                  0),
    'loading':     ('Loading model & data',   10),
    'predicting':  ('Generating predictions', 55),
    'formatting':  ('Formatting results',     85),
    'saving':      ('Saving to file',         95),
    'done':        ('Complete',              100),
    'error':       ('Error',                   0),
}

def _run_predict_job(job_id: str, params: dict):
    job = _JOBS[job_id]

    def report(stage: str, message: str = ''):
        _, pct = _PREDICT_STAGES.get(stage, ('', 0))
        job.update({'stage': stage, 'pct': pct,
                    'message': message or _PREDICT_STAGES[stage][0]})

    try:
        report('loading', 'Loading model…')
        bundle = load_model(params['model_name'])

        report('loading', 'Reading waterfall data…')
        conn = create_connection(plant=params['plant'])
        df   = build_prediction_dataset(conn, predidxs=params['predidxs'],
                                        aug_lookahead=params['aug_lookahead'])
        conn.close()

        if df.empty:
            job.update({'error': 'No waterfall rows found for the specified parameters.',
                        'done': True, 'stage': 'error'})
            return

        import pandas as pd
        report('predicting', f'Scoring {len(df):,} waterfall rows…')
        preds     = predict_from_bundle(bundle, df)
        row_count = len(preds)

        report('formatting', 'Building result tables…')
        scores_html = None
        scores = preds.attrs.get('scores')
        if scores is not None and not scores.empty:
            scores_html = scores.to_html(index=False, classes='pred-table', border=0)

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

        result = None
        if params['save_to_file']:
            report('saving', 'Writing XLSX…')
            out_dir = dirs['reports']
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            fname = params['file_path'] or f"predictions_{ts}.xlsx"
            if not fname.endswith('.xlsx'):
                fname += '.xlsx'
            save_path = out_dir / fname
            pivot.to_excel(save_path)
            result = str(save_path)

        table_html = pivot.head(50).to_html(index=False, classes='pred-table', border=0)
        elapsed = round(time.time() - job['started'])

        job.update({
            'stage':       'done',
            'pct':         100,
            'message':     f'{row_count:,} predictions generated',
            'result':      result,
            'table_html':  table_html,
            'row_count':   row_count,
            'scores_html': scores_html,
            'elapsed':     elapsed,
            'done':        True,
        })

    except Exception as exc:
        job.update({
            'stage':   'error',
            'pct':     job.get('pct', 0),
            'message': str(exc),
            'error':   str(exc),
            'done':    True,
        })


@app.route("/predict", methods=["GET"])
def predict():
    return render_template(
        "predict.html",
        active="predict",
        models=_list_models(),
        plant_sources=PLANT_SOURCES,
        current_week=datetime.now().strftime("%G-W%V"),
    )


@app.route("/predict/start", methods=["POST"])
def predict_start():
    model_name     = request.form.get("model", "")
    plant          = request.form.get("plant_source", "arlington").strip().lower()
    use_all_future = request.form.get("use_all_future") == "1"
    week_start     = request.form.get("week_start", "").strip()
    week_end       = request.form.get("week_end", "").strip()
    save_to_file   = request.form.get("save_to_file") == "1"
    file_path      = request.form.get("file_path", "").strip()

    try:
        aug_lookahead = int(request.form.get("aug_lookahead", "0") or 0)
        predidxs = None
        if not use_all_future:
            if not week_start or not week_end:
                raise ValueError("Start and end weeks are required when not predicting on all available future waterfall data.")
            start_idx = parse_week_to_idx(week_start)
            end_idx   = parse_week_to_idx(week_end)
            if end_idx < start_idx:
                raise ValueError("End week must not be before start week.")
            predidxs = list(range(start_idx, end_idx + 1))
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400

    params = {
        'model_name':    model_name,
        'plant':         plant,
        'predidxs':      predidxs,
        'aug_lookahead': aug_lookahead,
        'save_to_file':  save_to_file,
        'file_path':     file_path,
    }

    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {
        'stage': 'queued', 'pct': 0, 'message': 'Queued…',
        'done': False, 'error': None, 'result': None,
        'started': time.time(), 'elapsed': None,
    }
    threading.Thread(target=_run_predict_job, args=(job_id, params),
                     daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route("/predict/status/<job_id>")
def predict_status(job_id):
    job = _JOBS.get(job_id)
    if job is None:
        return jsonify({'error': 'unknown job'}), 404
    elapsed = round(time.time() - job['started']) if not job['done'] else job.get('elapsed', 0)
    return jsonify({**job, 'elapsed': elapsed})


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
            # 'augment':        request.form.get("augment") == "1",
            # 'min_la_pad':     int(request.form.get("min_part_lookahead_to_pad", 4) or 4),
            # 'la_pad':         int(request.form.get("lookahead_padding", 4) or 4),
            'model_type':     model_type,
            'target_mode':    request.form.get("target_mode", "qty").strip(),
            'objective':      request.form.get("objective", "log").strip(),
            'loss':           request.form.get("loss", "absolute_error").strip(),
            'l2':             float(request.form.get("l2", 1.0) or 1.0),
            'scale_features':          request.form.get("scale_features") == "1",
            'qty_scale_col':           request.form.get("qty_scale_col", "predqty_max").strip(),
            'wk_divisor':              float(request.form.get("wk_divisor", 52) or 52),
            'other_scale': request.form.get("other_scale") or None,
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
        test_mode  = request.form.get("test_mode", "holdout")
        try:
            bundle = load_model(model_name)
            if test_mode == "recent":
                plant     = request.form.get("plant_source", PLANT_SOURCES[0]).strip().lower()
                test_week = request.form.get("test_week", "").strip()
                if not test_week:
                    raise ValueError("Choose a week to test on.")
                predidx = parse_week_to_idx(test_week)

                conn = create_connection(plant=plant)
                df   = build_prediction_dataset(conn, predidxs=predidx)
                conn.close()

                preds = predict_from_bundle(bundle, df)
                known = preds[preds['orderqty'].notna()].copy()
                if known.empty:
                    error = "No actual consumption is posted yet for that week — try an earlier one."
                else:
                    known['naive_orderqty'] = known['predqty']
                    known['lookahead_wk']   = known['orderidx'] - known['predidx']
                    chart_html = holdout_report(known, label='recent-week test')
            else:
                chart_html = holdout_report(bundle.get('holdout_df'))
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


@app.route("/models/<name>/test-range")
def model_test_range(name):
    """Range of order weeks a model hasn't seen but for which real consumption
    is already posted — bounds the Evaluate page's 'Test On' week input."""
    plant = request.args.get('plant', '').strip().lower()
    try:
        bundle = load_model(os.path.basename(name))
    except Exception as exc:
        return jsonify({'error': str(exc)}), 404

    span = bundle.get('train_span')
    train_end_idx = None
    if span is not None:
        try:
            train_end_idx = int(span[1]['orderidx'])
        except Exception:
            pass

    holdout_df = bundle.get('holdout_df')
    val_end_idx = None
    if holdout_df is not None and not holdout_df.empty:
        val_end_idx = int(holdout_df['orderidx'].max())

    if val_end_idx is not None:
        low_idx = val_end_idx + 1
    elif train_end_idx is not None:
        low_idx = train_end_idx + 1
    else:
        low_idx = None

    high_idx = None
    if plant:
        try:
            _, cf = get_coverage_counts(plant)
            if cf:
                high_idx = max(r['idx'] for r in cf)
        except Exception:
            high_idx = None

    available = low_idx is not None and high_idx is not None and low_idx <= high_idx

    return jsonify({
        'available': available,
        'min_week': _iso_week_label(low_idx) if available else None,
        'max_week': _iso_week_label(high_idx) if available else None,
    })


# ── util ──────────────────────────────────────────────────────────────────────
# once we have existing models, this function could populate selection dropdowns
def _list_models():
    """Return available model names from the saved_models/ directory, most recently trained first."""
    model_dir = dirs['saved_models']
    if not model_dir.is_dir():
        return ["Full Dataset (2026wk01)"]   # fallback placeholder
    names = [f for f in os.listdir(model_dir) if f.endswith(".pkl")]
    names.sort(key=lambda f: (model_dir / f).stat().st_mtime, reverse=True)
    return names or ["Full Dataset (2026wk01)"]


if __name__ == "__main__":
    app.run(debug=True, port=5000)
