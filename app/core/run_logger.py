import atexit
import json
import os
import threading
import traceback
import uuid
import weakref
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.prefixe import dirs

# Terminal states a run can end in; anything else in the UI is treated as
# still in progress (e.g. a process that crashed mid-step never wrote 'failed').
END_STATUSES = {'success', 'failed', 'no_new_data', 'interrupted'}

# A live run rewrites its log every HEARTBEAT_SECS, even mid-step. A 'running'
# log untouched for STALE_SECS belongs to a process that died without a
# chance to record it (worker restart, container stop, kill -9).
HEARTBEAT_SECS = 30
STALE_SECS = 5 * 60

_INTERRUPTED_MSG = 'Interrupted: the process running this step stopped'

_live_loggers = weakref.WeakSet()


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _mark_interrupted(data, finished_at):
    """Close out a 'running' run dict in place: the run and any step still
    running are recorded as interrupted."""
    data['status'] = 'interrupted'
    data['finished_at'] = finished_at
    for step in data.get('steps', []):
        if step.get('status') == 'running':
            step['status'] = 'failed'
            step['message'] = (f"{_INTERRUPTED_MSG} ({step['message']})"
                               if step.get('message') else _INTERRUPTED_MSG)
            step['finished_at'] = finished_at


class RunLogger:
    """Records the step-by-step outcome of a routine.py execution to a JSON
    file so the UI's Run History page can show what succeeded/failed.

    While the run is open a background heartbeat keeps the log's
    `updated_at` fresh. If the thread that created the logger ends without
    calling finish(), the heartbeat records the run as interrupted. If the
    whole process dies, the heartbeat stops and mark_stale_runs() catches it."""

    def __init__(self, plant, script='routine.py', log_dir=None):
        self.log_dir = Path(log_dir) if log_dir else dirs['run_logs']
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6]
        self.plant = plant
        self.script = script
        self.started_at = _now()
        self.finished_at = None
        self.status = 'running'
        self.steps = []
        self._path = self.log_dir / f"{self.run_id}.json"
        self._lock = threading.RLock()
        self._done = threading.Event()
        self._owner = threading.current_thread()
        self._save()

        _live_loggers.add(self)
        threading.Thread(target=self._heartbeat, daemon=True,
                         name=f'runlog-heartbeat-{self.run_id}').start()

    def _save(self):
        with self._lock:
            data = {
                'run_id': self.run_id,
                'script': self.script,
                'plant': self.plant,
                'pid': os.getpid(),
                'started_at': self.started_at,
                'finished_at': self.finished_at,
                'updated_at': _now(),
                'status': self.status,
                'steps': self.steps,
            }
            tmp = self._path.with_suffix('.json.tmp')
            with open(tmp, 'w') as fh:
                json.dump(data, fh, indent=2)
            tmp.replace(self._path)

    def _heartbeat(self):
        while not self._done.wait(HEARTBEAT_SECS):
            if not self._owner.is_alive():
                # The run's thread ended without finish(), e.g. it was killed
                # by a BaseException the script's own handler didn't catch
                self.interrupt()
                return
            try:
                self._save()
            except OSError:
                pass  # a missed beat is fine; the next one will retry

    @contextmanager
    def step(self, name):
        """Wrap one pipeline step. Records success on normal exit, or
        failure (with the exception message/traceback) if it raises -
        the exception is always re-raised after being recorded."""
        rec = {'name': name, 'status': 'running', 'message': '',
               'started_at': _now(), 'finished_at': None}
        self.steps.append(rec)
        self._save()
        try:
            yield rec
        except Exception as exc:
            rec['status'] = 'failed'
            rec['message'] = rec['message'] or str(exc)
            rec['traceback'] = traceback.format_exc()
            rec['finished_at'] = _now()
            self._save()
            raise
        else:
            rec['status'] = 'success'
            rec['finished_at'] = _now()
            self._save()

    def progress(self, rec, message, current=None, total=None):
        """Update a running step's message mid-step (e.g. trial 3/20).
        `current`/`total` let the Run History page draw a progress bar."""
        rec['message'] = message
        if total:
            rec['progress'] = {'current': current, 'total': total}
        self._save()

    def skip(self, name, message=''):
        self.steps.append({'name': name, 'status': 'skipped', 'message': message,
                            'started_at': _now(), 'finished_at': _now()})
        self._save()

    def finish(self, status='success'):
        with self._lock:
            if self.status != 'running':
                return  # already closed out (e.g. interrupted at exit)
            self.status = status
            self.finished_at = _now()
            self._done.set()
            self._save()
        _live_loggers.discard(self)

    def interrupt(self):
        """Record the run as interrupted, unless it already finished."""
        with self._lock:
            if self.status != 'running':
                return
            data = {'steps': self.steps}
            _mark_interrupted(data, _now())
            self.status, self.finished_at = data['status'], data['finished_at']
            self._done.set()
            self._save()
        _live_loggers.discard(self)


@atexit.register
def _interrupt_live_runs():
    """A clean interpreter exit (worker shutdown, Ctrl+C) kills daemon run
    threads without unwinding them, so close their logs out here."""
    for logger in list(_live_loggers):
        try:
            logger.interrupt()
        except Exception:
            pass


def mark_stale_runs(log_dir=None, stale_secs=STALE_SECS):
    """Rewrite any 'running' log whose heartbeat has gone quiet as
    interrupted, so a run whose process died stops showing as in progress.
    Returns the run_ids that were marked."""
    log_dir = Path(log_dir) if log_dir else dirs['run_logs']
    if not log_dir.is_dir():
        return []

    marked = []
    now = datetime.now()
    for path in log_dir.glob('*.json'):
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        if data.get('status', 'running') != 'running':
            continue
        try:
            last = datetime.fromisoformat(data.get('updated_at') or data['started_at'])
        except (ValueError, KeyError, TypeError):
            continue
        if (now - last).total_seconds() <= stale_secs:
            continue

        _mark_interrupted(data, data.get('updated_at') or data.get('started_at'))
        tmp = path.with_suffix('.json.tmp')
        try:
            with open(tmp, 'w') as fh:
                json.dump(data, fh, indent=2)
            tmp.replace(path)
        except OSError:
            continue
        marked.append(data.get('run_id'))
    return marked


def list_runs(log_dir=None, limit=None):
    """Load all recorded routine.py runs, newest first."""
    log_dir = Path(log_dir) if log_dir else dirs['run_logs']
    if not log_dir.is_dir():
        return []

    runs = []
    for path in log_dir.glob('*.json'):
        try:
            with open(path) as fh:
                runs.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            continue

    runs.sort(key=lambda r: r.get('started_at', ''), reverse=True)
    return runs[:limit] if limit else runs
