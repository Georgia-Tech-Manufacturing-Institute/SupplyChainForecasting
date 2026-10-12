from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
import subprocess
import json
import os
import sys
import threading
import uuid
import time
from datetime import datetime
from pathlib import Path

# Make the project root importable so app.* packages resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.reporting.data_quality import coverage_report
from app.reporting.history_report import error_report
from app.reporting.core import get_coverage_counts, plant_stats
from app.core.parser import week_to_idx, idx_to_week
from app.core.loader import (create_connection, consumption_to_sql, waterfall_to_sql,
                             cost_to_sql, filter_SQL, file_result)
from app.models.train import model_train, save_model, load_model, predict_from_bundle, N_THRESHOLD
from app.reporting.model_perform import holdout_report
from app.models.create_dataset import build_training_dataset, build_prediction_dataset
from app.core.run_logger import list_runs
from app.routine import runAutomation, DATA_TYPES
from app.optimize import runOptimization, DEFAULT_TRIALS
import sqlite3
from app import prefixe
from app.prefixe import dirs, raw_dir, PLANT_SOURCES, pre, plant_key

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
    'training_a':  ('Training model',         76),
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
            exclusions=params['exclusions']['rules'],
        )
        print("FINISHED TRAINING ")
        conn.close()

        if df.empty:
            job.update({'error': 'No training data found for the specified date range.',
                        'done': True, 'stage': 'error'})
            return

        report('consumption', f'Dataset ready — {len(df):,} rows')
        report('merging',     'Filtering and finalising features…')

        split, N = params['split_model'], params['size_threshold']
        report('training_a', f'Fitting Models A (predqty > {N:g}) and B (predqty ≤ {N:g})…'
                             if split else 'Fitting model…')
        bundle = model_train(df, holdout_weeks=params['holdout'],
                             plant=params['plant'],
                             config={'obj':  params.get('objective', 'log'),
                                     'loss': params.get('loss', 'absolute_error'),
                                     'l2':   params.get('l2', 1.0),
                                     'target_mode':    params.get('target_mode', 'qty'),
                                     'model_type':     params.get('model_type', 'hgb'),
                                     'split_model':    split,
                                     'size_threshold': N,
                                     'scale_features': params.get('scale_features', False),
                                     'qty_scale_col':  params.get('qty_scale_col', 'predqty_max'),
                                     'wk_divisor':     params.get('wk_divisor', 52.0)})

        # Raw field text alongside the expanded rules, so Retrain can refill the form
        bundle['exclusions'] = params['exclusions']

        report('saving', 'Persisting model to disk…')
        nickname = params['nickname']
        if not nickname:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            nickname = f"{plant_key[params['plant']]}_{params.get('model_type')}_{ts}"

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


def parse_exclusions(text, data_type):
    """Parse a Train-form exclusion field into rules for build_training_dataset.

    Comma-separated tokens, each a week or an inclusive range, optionally
    scoped to one part with a 'PART:' prefix:
        2025-W52, 2026-W01..2026-W03, 2026-W05..W07, 1234-567:2026-W10
    """
    rules = []
    for token in (t.strip() for t in (text or '').split(',')):
        if not token:
            continue
        part, _, weeks = token.rpartition(':')
        start, _, end = weeks.strip().partition('..')
        start = start.strip()
        end = end.strip()
        if re.fullmatch(r"W\d{2}", end):          # '2026-W05..W07' shorthand
            end = start[:5] + end
        try:
            lo = parse_week_to_idx(start)
            hi = parse_week_to_idx(end) if end else lo
        except (AttributeError, ValueError):     # no match / no such ISO week
            raise ValueError(f"Can't read excluded {data_type} week {token!r}; "
                             f"use YYYY-Www, YYYY-Www..YYYY-Www, or PART:YYYY-Www")
        if hi < lo:
            raise ValueError(f"Excluded {data_type} range {token!r} ends before it starts")
        rules += [{'data_type': data_type, 'week_idx': w,
                   'part': part.strip() or None}
                  for w in range(lo, hi + 1)]
    return rules



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
    stats = {}
    for p in PLANT_SOURCES:
        try:
            stats[p] = plant_stats(p.lower())
        except Exception:
            stats[p] = None
    return render_template("home.html", active="home", changelog=parse_changelog(),
                           plant_sources=PLANT_SOURCES, plant_stats=stats,
                           plant_result=request.args.get("plant_result"),
                           plant_error=request.args.get("plant_error"))


@app.route("/plants/add", methods=["POST"])
def add_plant():
    global PLANT_SOURCES
    name = request.form.get("plant_name", "").strip()
    try:
        prefixe.add_plant(name)
        PLANT_SOURCES = prefixe.PLANT_SOURCES
        # Initialize the SQL database (creates the .db file and schema) now.
        conn = create_connection(plant=name.lower())
        conn.close()
        return redirect(url_for("home", plant_result=f"Added plant '{name}' and initialized its database."))
    except Exception as exc:
        return redirect(url_for("home", plant_error=str(exc)))


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
    file_results = []
    if request.method == "POST":
        plant_source = request.form.get("plant_source", "").strip()
        data_type    = request.form.get("data_type",    "").strip()
        overwrite    = request.form.get("overwrite") == "on"
        files        = [f for f in request.files.getlist("files") if f.filename]

        if not plant_source or not data_type:
            error = "Plant Source and Data Type are required."
        elif not files:
            error = "No files selected."
        else:
            dest_dir = raw_dir(plant_source, data_type)
            print(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            saved = []
            for f in files:
                path = dest_dir / os.path.basename(f.filename)
                f.save(path)
                saved.append(path)
            try:
                conn = create_connection(plant=plant_source.lower())
                try:
                    # Load only what was just uploaded, one file at a time, so
                    # the report covers exactly these files.
                    for path in saved:
                        try:
                            if data_type == "Consumption":
                                file_results += consumption_to_sql(conn, path, plant=plant_source,
                                                                   overwrite=overwrite)
                            elif data_type == "Waterfall":
                                file_results += waterfall_to_sql(conn, path, plant=plant_source,
                                                                 overwrite=overwrite)
                            elif data_type == "Cost":
                                file_results += cost_to_sql(conn, path, overwrite=overwrite)
                        except ValueError as e:
                            # resolve_sources rejects unsupported extensions
                            file_results.append(file_result(path.name, False, str(e)))
                finally:
                    conn.close()
                n_loaded = sum(r['loaded'] for r in file_results)
                result = (f"Loaded {n_loaded} of {len(file_results)} file(s) "
                          f"into {plant_source} {data_type}"
                          f"{' (overwriting)' if overwrite else ''}.")
            except Exception as e:
                error = str(e)

    return render_template("load.html", active="load", result=result, error=error,
                           file_results=file_results, plant_sources=PLANT_SOURCES)


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
                'train_seconds': bundle.get('train_seconds'),
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
    excl = bundle.get('exclusions') or {}
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
        # Bundles from before splitting was optional are always split
        'split_model':         bundle.get('mB') is not None,
        'size_threshold':      bundle.get('size_threshold') or N_THRESHOLD,
        'earliest_order_date': _iso_week(min_oi),
        'latest_order_date':   _iso_week(max_oi),
        'target_mode':         cfg.get('target_mode', 'qty'),
        'objective':           cfg.get('obj', 'log'),
        'loss':                cfg.get('loss', 'absolute_error'),
        'l2':                  cfg.get('l2', 1.0),
        'scale_features':      bool(bundle.get('scale_config')),
        'qty_scale_col':       scale_cfg.get('qty_scale_col', 'predqty_max'),
        'wk_divisor':          scale_cfg.get('wk_divisor', 52),
        'exclude_consumption': excl.get('consumption', ''),
        'exclude_waterfall':   excl.get('waterfall', ''),
        'other_scale':         scale_cfg.get('other_scale') or '',
    })


# ── AUTOMATION / Run History ────────────────────────────────────────────────
_RUN_STATUS_LABELS = {
    'success':     'Success',
    'failed':      'Failed',
    'no_new_data': 'No new data',
    'running':     'In progress',
    'interrupted': 'Interrupted',
}

def _fmt_elapsed(started_at, finished_at):
    if not started_at:
        return '—'
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at) if finished_at else datetime.now()
    except ValueError:
        return '—'
    secs = int((end - start).total_seconds())
    if secs < 60:
        return f"{secs}s"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m {secs}s"
    hrs, mins = divmod(mins, 60)
    return f"{hrs}h {mins}m"

def _group_status(statuses):
    for s in ('failed', 'running', 'success'):
        if s in statuses:
            return s
    return 'skipped'

def _progress_segments(step):
    """One segment per unit of a step's reported progress (e.g. one per trial)."""
    cur, tot = step['progress']['current'], step['progress']['total']
    if step['status'] == 'success':
        cur = tot
    segs = []
    for i in range(tot):
        if i < cur:
            status = 'success'
        elif i == cur and step['status'] in ('running', 'failed'):
            status = step['status']
        else:
            status = 'pending'
        segs.append({'name': f'Trial {i + 1}', 'status': status, 'message': ''})
    return segs

_PIE_COLORS = {'success': '#4caf50', 'failed': '#e53935',
               'skipped': 'var(--border)', 'running': 'var(--blue)', 'pending': 'var(--bg)'}

def _pie_gradient(segments):
    """conic-gradient() with one slice per run of same-status segments."""
    n = len(segments)
    slices, start = [], 0
    for i in range(1, n + 1):
        if i == n or segments[i]['status'] != segments[start]['status']:
            color = _PIE_COLORS.get(segments[start]['status'], 'var(--bg)')
            slices.append(f'{color} {100 * start / n:.2f}% {100 * i / n:.2f}%')
            start = i
    return f"conic-gradient({', '.join(slices)})"

def _build_stages(steps):
    """Collapse the step list into what the Run History row draws: steps
    sharing a prefix (Load Consumption/Waterfall/Cost) become one stage with
    a segment each, and a step reporting progress gets a segment per unit."""
    stages = []
    for step in steps:
        prefix, _, rest = step['name'].partition(' ')
        if prefix == 'Load' and rest:
            if stages and stages[-1]['name'] == 'Load' and stages[-1].get('group'):
                stages[-1]['segments'].append({**step, 'name': rest})
            else:
                stages.append({'name': 'Load', 'group': True,
                               'segments': [{**step, 'name': rest}]})
        elif step.get('progress') and step['progress'].get('total'):
            stages.append({**step, 'segments': _progress_segments(step)})
        else:
            stages.append(step)

    for st in stages:
        segs = st.get('segments')
        if not segs:
            continue
        if st.get('group'):
            # Always one segment per data type, even before later loads are logged
            seen = {s['name'] for s in segs}
            segs += [{'name': t, 'status': 'pending', 'message': ''}
                     for t in DATA_TYPES if t not in seen]
            st['status'] = _group_status([s['status'] for s in segs])
            st['message'] = '; '.join(f"{s['name']}: {s['message'] or s['status']}" for s in segs)
            n_done = sum(s['status'] not in ('running', 'pending') for s in segs)
            st['count_label'] = f'{n_done}/{len(segs)}'
        else:
            cur, tot = st['progress']['current'], st['progress']['total']
            st['count_label'] = (f'trial {min(cur + 1, tot)}/{tot}' if st['status'] == 'running'
                                 else f'{tot} trials')
        # Once finished, the bar collapses into a circle drawn as a pie of its parts
        if st['status'] != 'running':
            st['pie'] = _pie_gradient(segs)
            seg_statuses = {s['status'] for s in segs}
            st['uniform'] = seg_statuses.pop() if len(seg_statuses) == 1 else None
    return stages

def _format_run(run):
    status = run.get('status', 'running')
    # A run that hasn't written to its log for a long time almost certainly
    # crashed without a chance to record its own failure (killed process, etc).
    if status == 'running':
        try:
            last = datetime.fromisoformat(run.get('updated_at') or run['started_at'])
            if (datetime.now() - last).total_seconds() > 2 * 3600:
                status = 'interrupted'
        except (ValueError, KeyError):
            pass
    run['status'] = status
    run['status_label'] = _RUN_STATUS_LABELS.get(status, status)
    run['elapsed_str'] = _fmt_elapsed(run.get('started_at'), run.get('finished_at'))
    run['stages'] = _build_stages(run.get('steps', []))
    return run

@app.route("/runs")
def runs():
    formatted = [_format_run(r) for r in list_runs()]
    return render_template("runs.html", active="runs",
                           runs=formatted,
                           any_running=any(r['status'] == 'running' for r in formatted),
                           plant_sources=PLANT_SOURCES,
                           default_trials=DEFAULT_TRIALS,
                           default_incoming=str(dirs['incoming']),
                           run_result=request.args.get("run_result"),
                           run_error=request.args.get("run_error"))


# Manually launched routine.py / optimize.py runs. They record their own
# progress through RunLogger, so the Run History page is the status view.
_ACTIVE_ROUTINES: dict = {}   # (kind, plant) → Thread

def _launch_routine(kind: str, plant: str, target, **kwargs) -> bool:
    """Start `target` in a background thread unless the same kind of run is
    already going for this plant. Returns False if one is already running."""
    key = (kind, plant.lower())
    existing = _ACTIVE_ROUTINES.get(key)
    if existing is not None and existing.is_alive():
        return False

    def _wrapped():
        try:
            target(plant, **kwargs)
        except Exception:
            # RunLogger has already recorded the failed step for the UI
            import traceback
            traceback.print_exc()

    thread = threading.Thread(target=_wrapped, daemon=True)
    _ACTIVE_ROUTINES[key] = thread
    thread.start()
    return True


def _browse_roots():
    dirs['incoming'].mkdir(parents=True, exist_ok=True)
    return [dirs['incoming'].resolve(), dirs['data'].resolve()]

def _browse_root(path=None):
    """The browse root containing `path`, or the incoming folder by default."""
    roots = _browse_roots()
    if path is not None:
        path = Path(path).resolve()
        for root in roots:
            if path == root or root in path.parents:
                return root
    return roots[0]

def _within_browse_root(path) -> bool:
    path = Path(path).resolve()
    return any(path == root or root in path.parents for root in _browse_roots())


@app.route("/runs/folders")
def runs_folders():
    """List subfolders for the incoming-folder picker. The UI runs in a
    headless container, so a native OS dialog isn't possible; browsing is
    confined to the incoming and data folders (the parts of the host mounted
    into it)."""
    path = Path(request.args.get("path") or dirs['incoming']).resolve()
    if not _within_browse_root(path) or not path.is_dir():
        path = _browse_root()
    try:
        subdirs = sorted((p.name for p in path.iterdir()
                          if p.is_dir() and not p.name.startswith('.')), key=str.lower)
    except OSError as exc:
        return jsonify({'error': str(exc)}), 400
    root = _browse_root(path)
    return jsonify({
        'path':    str(path),
        'parent':  str(path.parent) if path != root else None,
        'subdirs': subdirs,
    })


@app.route("/runs/automation/start", methods=["POST"])
def runs_automation_start():
    plant   = request.form.get("plant_source", "").strip()
    autodir = request.form.get("incoming_folder", "").strip() or str(dirs['incoming'])
    if not plant:
        return redirect(url_for("runs", run_error="Plant is required."))
    if not _within_browse_root(autodir) or not os.path.isdir(autodir):
        return redirect(url_for("runs", run_error=f"Incoming folder not found: {autodir}"))

    if not _launch_routine('automation', plant, runAutomation, autodir=autodir):
        return redirect(url_for("runs", run_error=f"An automation run for {plant} is already in progress."))
    return redirect(url_for("runs", run_result=f"Started automation run for {plant}."))


@app.route("/runs/optimize/start", methods=["POST"])
def runs_optimize_start():
    plant = request.form.get("plant_source", "").strip()
    try:
        n_trials = int(request.form.get("n_trials", DEFAULT_TRIALS) or DEFAULT_TRIALS)
        if not plant or n_trials < 1:
            raise ValueError("Plant and a positive number of trials are required.")
    except ValueError as exc:
        return redirect(url_for("runs", run_error=str(exc)))

    if not _launch_routine('optimize', plant, runOptimization, n_trials=n_trials):
        return redirect(url_for("runs", run_error=f"An optimization run for {plant} is already in progress."))
    return redirect(url_for("runs", run_result=f"Started hyperparameter search for {plant} ({n_trials} trials)."))


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


# Whole-week exclusions picked in the Train page's week picker, remembered across
# sessions. One entry per scope -- 'all' (every plant) or a lowercase plant name --
# each {'consumption': [orderidx, ...], 'waterfall': [predidx, ...]}. A plant's
# effective exclusions are its own plus the 'all' entry; the Train form expands
# them into its text fields, so training itself never reads this file.
EXCLUSION_SETTINGS_PATH = dirs['data'] / 'training_exclusions.json'
EXCLUSION_TYPES = ('consumption', 'waterfall')

def _exclusion_scopes():
    return {'all', *(p.lower() for p in PLANT_SOURCES)}

def load_exclusion_settings():
    try:
        with open(EXCLUSION_SETTINGS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_exclusion_settings(raw):
    """Validate and persist the picker's settings; returns what was saved."""
    if not isinstance(raw, dict):
        raise ValueError("Expected an object keyed by plant")
    clean = {}
    for scope, weeks in raw.items():
        if scope not in _exclusion_scopes():
            raise ValueError(f"Unknown plant {scope!r}")
        entry = {t: sorted({int(w) for w in (weeks or {}).get(t, [])}) for t in EXCLUSION_TYPES}
        if any(entry.values()):
            clean[scope] = entry
    EXCLUSION_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EXCLUSION_SETTINGS_PATH.with_suffix('.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(clean, f, indent=1)
    os.replace(tmp, EXCLUSION_SETTINGS_PATH)
    return clean


@app.route("/exclusions/settings", methods=["GET", "POST"])
def exclusion_settings():
    if request.method == "GET":
        return jsonify(load_exclusion_settings())
    try:
        return jsonify(save_exclusion_settings(request.get_json(silent=True)))
    except (ValueError, TypeError) as exc:
        return jsonify({'error': str(exc)}), 400


@app.route("/exclusions/coverage/<scope>")
def exclusion_coverage(scope):
    """Week mosaic data for the picker, keyed the way exclusions are: waterfall
    by snapshot week (predidx), consumption by order week. 'all' sums the plants."""
    if scope not in _exclusion_scopes():
        return jsonify({"error": "Unknown plant"}), 404
    plants = [p.lower() for p in PLANT_SOURCES] if scope == 'all' else [scope]
    # sqlite would create an empty database for a plant with no data yet
    plants = [p for p in plants if (dirs['processed'] / f'{p}.db').exists()]
    totals = {'wf': {}, 'cf': {}}
    for plant in plants:
        for key, records in zip(('wf', 'cf'), get_coverage_counts(plant, wf_week='predidx')):
            for r in records:
                totals[key][r['idx']] = totals[key].get(r['idx'], 0) + r['count']
    return jsonify({key: [{'idx': i, 'count': c} for i, c in sorted(counts.items())]
                    for key, counts in totals.items()})


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
            'split_model':    request.form.get("split_model") == "1",
            'size_threshold': float(request.form.get("size_threshold", N_THRESHOLD) or N_THRESHOLD),
            'target_mode':    request.form.get("target_mode", "qty").strip(),
            'objective':      request.form.get("objective", "log").strip(),
            'loss':           request.form.get("loss", "absolute_error").strip(),
            'l2':             float(request.form.get("l2", 1.0) or 1.0),
            'scale_features':          request.form.get("scale_features") == "1",
            'qty_scale_col':           request.form.get("qty_scale_col", "predqty_max").strip(),
            'wk_divisor':              float(request.form.get("wk_divisor", 52) or 52),
            'other_scale': request.form.get("other_scale") or None,
        }
        exclude_text = {t: request.form.get(f"exclude_{t}", "").strip()
                        for t in ('consumption', 'waterfall')}
        params['exclusions'] = {
            **exclude_text,
            'rules': [r for t, text in exclude_text.items()
                      for r in parse_exclusions(text, t)],
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
