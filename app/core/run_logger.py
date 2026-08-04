import json
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.prefixe import dirs

# Terminal states a run can end in; anything else in the UI is treated as
# still in progress (e.g. a process that crashed mid-step never wrote 'failed').
END_STATUSES = {'success', 'failed', 'no_new_data'}


def _now():
    return datetime.now().isoformat(timespec='seconds')


class RunLogger:
    """Records the step-by-step outcome of a routine.py execution to a JSON
    file so the UI's Run History page can show what succeeded/failed."""

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
        self._save()

    def _save(self):
        data = {
            'run_id': self.run_id,
            'script': self.script,
            'plant': self.plant,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'status': self.status,
            'steps': self.steps,
        }
        tmp = self._path.with_suffix('.json.tmp')
        with open(tmp, 'w') as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(self._path)

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

    def skip(self, name, message=''):
        self.steps.append({'name': name, 'status': 'skipped', 'message': message,
                            'started_at': _now(), 'finished_at': _now()})
        self._save()

    def finish(self, status='success'):
        self.status = status
        self.finished_at = _now()
        self._save()


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
