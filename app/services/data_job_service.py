"""In-process data jobs for slow provider operations."""
from __future__ import annotations

import copy
import queue
import threading
import uuid
from datetime import datetime
from typing import Any, Optional

from app.services.data_service import backfill_data, estimate_backfill_api_usage


def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


class DataJobService:
    def __init__(self):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None

    def enqueue_backfill(
        self,
        stock_input: str,
        *,
        days: int = None,
        start_date: Any = None,
        end_date: Any = None,
    ) -> dict:
        estimate = estimate_backfill_api_usage(stock_input, days=days)
        job_id = uuid.uuid4().hex
        job = {
            'id': job_id,
            'type': 'backfill',
            'status': 'queued',
            'progress': 0,
            'stock': stock_input,
            'params': {
                'days': days,
                'start_date': start_date,
                'end_date': end_date,
            },
            'estimate': estimate,
            'created_at': _now_iso(),
            'updated_at': _now_iso(),
            'result': None,
            'error': None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._ensure_worker_locked()
            self._queue.put(job_id)
            return copy.deepcopy(job)

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def _ensure_worker_locked(self):
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name='stockquant-data-job-worker',
            daemon=True,
        )
        self._worker.start()

    def _update_job(self, job_id: str, **fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(fields)
            job['updated_at'] = _now_iso()

    def _worker_loop(self):
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = copy.deepcopy(self._jobs.get(job_id))
            if not job:
                self._queue.task_done()
                continue

            self._update_job(job_id, status='running', progress=10)
            params = job.get('params') or {}
            try:
                result = backfill_data(
                    job['stock'],
                    days=params.get('days'),
                    start_date=params.get('start_date'),
                    end_date=params.get('end_date'),
                )
                self._update_job(
                    job_id,
                    status='completed',
                    progress=100,
                    result=result,
                )
            except Exception as exc:
                self._update_job(
                    job_id,
                    status='failed',
                    progress=100,
                    error=str(exc),
                )
            finally:
                self._queue.task_done()


data_job_service = DataJobService()
