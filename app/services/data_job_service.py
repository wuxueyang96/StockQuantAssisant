"""Persistent data jobs with retryable fetch tasks."""
from __future__ import annotations

import copy
import json
import queue
import threading
import uuid
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from app.config import Config
from app.models.database import db_manager
from app.services.data_service import (
    _calendar_days_for_trading_days,
    _normalize_ohlcv,
    data_status_for_market,
    estimate_backfill_api_usage,
)
from app.services.data_sources import FetchRequest
from app.services.data_sources.registry import (
    active_data_source,
    data_source_by_name,
    list_data_sources,
)
from app.services.data_sources.utils import chunk_windows
from app.services.stock_service import detect_market


def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _clean_scalar(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _json_dumps(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _json_loads(value, default):
    value = _clean_scalar(value)
    if not value:
        return copy.deepcopy(default)
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return copy.deepcopy(default)


def _compact_error(error, limit: int = 240) -> str:
    text = ' '.join(str(error or '').split())
    if len(text) <= limit:
        return text
    return f'{text[:limit - 3]}...'


def _to_iso(value) -> Optional[str]:
    value = _clean_scalar(value)
    if value is None:
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return str(value)


class DataJobService:
    JOB_COLUMNS = [
        'id', 'type', 'status', 'progress', 'stock', 'params', 'estimate',
        'created_at', 'updated_at', 'started_at', 'finished_at',
        'total_tasks', 'success_tasks', 'failed_tasks', 'empty_tasks',
        'skipped_tasks', 'pending_tasks', 'running_tasks', 'inserted_rows',
        'updated_rows', 'source_rows', 'result', 'error',
    ]
    TASK_COLUMNS = [
        'id', 'job_id', 'seq', 'type', 'status', 'market', 'stock_code',
        'display_code', 'table', 'source', 'original_source', 'retry_source',
        'start_date', 'end_date', 'request_mode', 'attempts', 'rows',
        'inserted_rows', 'updated_rows', 'error', 'error_summary',
        'skip_reason', 'created_at', 'updated_at', 'started_at', 'finished_at',
        'last_retry_at', 'attempt_logs',
    ]
    TERMINAL_TASK_STATUSES = {'success', 'failed', 'empty', 'skipped'}

    def __init__(self):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._queued_task_ids: set[str] = set()
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None
        self._loaded_key = None

    def enqueue_backfill(
        self,
        stock_input: str,
        *,
        days: int = None,
        start_date: Any = None,
        end_date: Any = None,
        source: str = None,
    ) -> dict:
        requested_days = int(days or Config.INITIAL_5MIN_HISTORY_DAYS)
        source_name = (source or active_data_source().name).strip().lower()
        source_obj = data_source_by_name(source_name)
        estimate = estimate_backfill_api_usage(stock_input, days=requested_days)
        job_id = uuid.uuid4().hex
        now = _now_iso()

        with self._lock:
            self._ensure_loaded_locked()
            tasks = self._build_backfill_tasks(
                job_id,
                stock_input,
                requested_days,
                start_date,
                end_date,
                source_name,
                source_obj,
                now,
            )
            if not tasks:
                raise ValueError('没有可执行的数据任务')

            job = {
                'id': job_id,
                'type': 'backfill',
                'status': 'queued',
                'progress': 0,
                'stock': stock_input,
                'params': {
                    'days': requested_days,
                    'start_date': _to_iso(start_date),
                    'end_date': _to_iso(end_date),
                    'source': source_name,
                    'chunk_days': int(Config.AKSHARE_BACKFILL_CHUNK_DAYS),
                },
                'estimate': estimate,
                'created_at': now,
                'updated_at': now,
                'started_at': None,
                'finished_at': None,
                'total_tasks': len(tasks),
                'success_tasks': 0,
                'failed_tasks': 0,
                'empty_tasks': 0,
                'skipped_tasks': 0,
                'pending_tasks': len(tasks),
                'running_tasks': 0,
                'inserted_rows': 0,
                'updated_rows': 0,
                'source_rows': 0,
                'result': None,
                'error': None,
            }
            self._jobs[job_id] = job
            for task in tasks:
                self._tasks[task['id']] = task
                self._enqueue_task_locked(task['id'])
            self._persist_locked()
            self._ensure_worker_locked()
            return self._public_job_locked(job_id, include_tasks=True)

    def list_jobs(
        self,
        *,
        status: str = None,
        stock: str = None,
        limit: int = 50,
    ) -> list[dict]:
        with self._lock:
            self._ensure_loaded_locked()
            rows = list(self._jobs.values())
            if status:
                rows = [j for j in rows if j.get('status') == status]
            if stock:
                keyword = stock.strip().lower()
                rows = [j for j in rows if keyword in str(j.get('stock') or '').lower()]
            rows = sorted(rows, key=lambda j: j.get('created_at') or '', reverse=True)
            return [
                self._public_job_locked(job['id'], include_tasks=False)
                for job in rows[:max(1, int(limit or 50))]
            ]

    def get_job(self, job_id: str, *, include_tasks: bool = True) -> Optional[dict]:
        with self._lock:
            self._ensure_loaded_locked()
            if job_id not in self._jobs:
                return None
            return self._public_job_locked(job_id, include_tasks=include_tasks)

    def get_tasks(self, job_id: str) -> list[dict]:
        with self._lock:
            self._ensure_loaded_locked()
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return [
                self._public_task(task)
                for task in self._job_tasks_locked(job_id)
            ]

    def retry_task(self, job_id: str, task_id: str, *, source: str = None) -> dict:
        with self._lock:
            self._ensure_loaded_locked()
            job = self._jobs.get(job_id)
            task = self._tasks.get(task_id)
            if not job or not task or task.get('job_id') != job_id:
                raise KeyError(task_id)
            if task.get('status') not in ('failed', 'empty'):
                raise ValueError('只有失败或空返回的 Task 可以重试')

            source_name = (source or task.get('source') or task.get('original_source')).strip().lower()
            source_obj = data_source_by_name(source_name)
            if not source_obj.supports(task['market']):
                raise ValueError(f"{source_name} 不支持 {task['market']} 市场")

            now = _now_iso()
            task.update({
                'status': 'pending',
                'source': source_name,
                'retry_source': source_name,
                'error': None,
                'error_summary': None,
                'started_at': None,
                'finished_at': None,
                'last_retry_at': now,
                'updated_at': now,
            })
            job['error'] = None
            job['finished_at'] = None
            self._recalculate_job_locked(job_id)
            self._enqueue_task_locked(task_id)
            self._persist_locked()
            self._ensure_worker_locked()
            return self._public_task(task)

    def data_sources(self, market: str = None) -> list[dict]:
        return list_data_sources(market=market)

    def reset_for_tests(self):
        with self._lock:
            self._jobs = {}
            self._tasks = {}
            self._queue = queue.Queue()
            self._queued_task_ids = set()
            self._worker = None
            self._loaded_key = None

    def _build_backfill_tasks(
        self,
        job_id: str,
        stock_input: str,
        requested_days: int,
        start_date: Any,
        end_date: Any,
        source_name: str,
        source_obj,
        now: str,
    ) -> list[dict]:
        detections = detect_market(stock_input)
        tasks = []
        seq = 1
        for market, stock_code in detections:
            if not source_obj.supports(market):
                raise ValueError(f"{source_name} 不支持 {market} 市场")
            record = data_status_for_market(market, stock_code)
            if not record.get('registered'):
                raise ValueError(f"{record.get('display_code') or stock_code} 尚未注册，请先注册该股票")

            request_calendar_days = _calendar_days_for_trading_days(requested_days)
            explicit_window = bool(start_date or end_date)
            effective_start = start_date
            effective_end = end_date
            request_mode = 'explicit_window' if explicit_window else 'recent_window'

            if not explicit_window and record.get('first_timestamp'):
                first_ts = pd.Timestamp(record['first_timestamp'])
                effective_end = first_ts
                effective_start = first_ts - pd.DateOffset(days=request_calendar_days)
                request_mode = 'backfill_before_first_timestamp'
            if not effective_end:
                effective_end = pd.Timestamp.now()
            if not effective_start:
                effective_start = pd.Timestamp(effective_end) - pd.DateOffset(days=request_calendar_days)

            windows = chunk_windows(
                pd.Timestamp(effective_start),
                pd.Timestamp(effective_end),
                int(Config.AKSHARE_BACKFILL_CHUNK_DAYS),
            )
            for chunk_start, chunk_end in windows:
                task_id = uuid.uuid4().hex
                tasks.append({
                    'id': task_id,
                    'job_id': job_id,
                    'seq': seq,
                    'type': 'backfill_window',
                    'status': 'pending',
                    'market': market,
                    'stock_code': stock_code,
                    'display_code': record.get('display_code'),
                    'table': record.get('table'),
                    'source': source_name,
                    'original_source': source_name,
                    'retry_source': None,
                    'start_date': _to_iso(chunk_start),
                    'end_date': _to_iso(chunk_end),
                    'request_mode': f'{request_mode}_task_chunks',
                    'attempts': 0,
                    'rows': 0,
                    'inserted_rows': 0,
                    'updated_rows': 0,
                    'error': None,
                    'error_summary': None,
                    'skip_reason': None,
                    'created_at': now,
                    'updated_at': now,
                    'started_at': None,
                    'finished_at': None,
                    'last_retry_at': None,
                    'attempt_logs': [],
                })
                seq += 1
        return tasks

    def _ensure_loaded_locked(self):
        key = (Config.DATA_DIR, Config.OSS_BUCKET)
        if self._loaded_key == key:
            return

        self._jobs = {}
        self._tasks = {}
        self._queued_task_ids = set()
        changed = False

        jobs_df = db_manager.read_metadata_frame('data_jobs')
        if not jobs_df.empty:
            for row in jobs_df.to_dict('records'):
                job = self._decode_job(row)
                self._jobs[job['id']] = job

        tasks_df = db_manager.read_metadata_frame('data_tasks')
        if not tasks_df.empty:
            for row in tasks_df.to_dict('records'):
                task = self._decode_task(row)
                if task.get('status') == 'running':
                    task['status'] = 'failed'
                    task['error'] = '服务重启，运行中的 Task 已中断'
                    task['error_summary'] = task['error']
                    task['finished_at'] = _now_iso()
                    changed = True
                self._tasks[task['id']] = task

        self._loaded_key = key
        for job_id in list(self._jobs):
            self._recalculate_job_locked(job_id)
        for task in self._tasks.values():
            if task.get('status') == 'pending':
                self._enqueue_task_locked(task['id'])
        if changed:
            self._persist_locked()
        if self._queued_task_ids:
            self._ensure_worker_locked()

    def _decode_job(self, row: dict) -> dict:
        job = {col: _clean_scalar(row.get(col)) for col in self.JOB_COLUMNS}
        job['params'] = _json_loads(job.get('params'), {})
        job['estimate'] = _json_loads(job.get('estimate'), {})
        job['result'] = _json_loads(job.get('result'), None)
        for key in (
            'progress', 'total_tasks', 'success_tasks', 'failed_tasks',
            'empty_tasks', 'skipped_tasks', 'pending_tasks', 'running_tasks',
            'inserted_rows', 'updated_rows', 'source_rows',
        ):
            job[key] = int(job.get(key) or 0)
        return job

    def _decode_task(self, row: dict) -> dict:
        task = {col: _clean_scalar(row.get(col)) for col in self.TASK_COLUMNS}
        task['attempt_logs'] = _json_loads(task.get('attempt_logs'), [])
        for key in ('seq', 'attempts', 'rows', 'inserted_rows', 'updated_rows'):
            task[key] = int(task.get(key) or 0)
        return task

    def _persist_locked(self):
        jobs_rows = []
        for job in self._jobs.values():
            row = {col: job.get(col) for col in self.JOB_COLUMNS}
            row['params'] = _json_dumps(job.get('params'))
            row['estimate'] = _json_dumps(job.get('estimate'))
            row['result'] = _json_dumps(job.get('result'))
            jobs_rows.append(row)
        tasks_rows = []
        for task in self._tasks.values():
            row = {col: task.get(col) for col in self.TASK_COLUMNS}
            row['attempt_logs'] = _json_dumps(task.get('attempt_logs'))
            tasks_rows.append(row)
        db_manager.write_metadata_frame('data_jobs', pd.DataFrame(jobs_rows, columns=self.JOB_COLUMNS))
        db_manager.write_metadata_frame('data_tasks', pd.DataFrame(tasks_rows, columns=self.TASK_COLUMNS))

    def _ensure_worker_locked(self):
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name='stockquant-data-task-worker',
            daemon=True,
        )
        self._worker.start()

    def _enqueue_task_locked(self, task_id: str):
        if task_id in self._queued_task_ids:
            return
        self._queued_task_ids.add(task_id)
        self._queue.put(task_id)

    def _worker_loop(self):
        while True:
            task_id = self._queue.get()
            with self._lock:
                self._queued_task_ids.discard(task_id)
                self._ensure_loaded_locked()
                task = copy.deepcopy(self._tasks.get(task_id))
                if not task or task.get('status') != 'pending':
                    self._queue.task_done()
                    continue
                self._mark_task_running_locked(task_id)

            try:
                result = self._execute_task(task)
                self._finish_task(task_id, **result)
            except Exception as exc:
                self._finish_task(
                    task_id,
                    status='failed',
                    rows=0,
                    inserted_rows=0,
                    updated_rows=0,
                    error=str(exc),
                )
            finally:
                self._queue.task_done()

    def _mark_task_running_locked(self, task_id: str):
        task = self._tasks[task_id]
        now = _now_iso()
        task.update({
            'status': 'running',
            'attempts': int(task.get('attempts') or 0) + 1,
            'started_at': now,
            'updated_at': now,
            'finished_at': None,
            'error': None,
            'error_summary': None,
            'skip_reason': None,
        })
        job = self._jobs.get(task['job_id'])
        if job and not job.get('started_at'):
            job['started_at'] = now
        self._recalculate_job_locked(task['job_id'])
        self._persist_locked()

    def _execute_task(self, task: dict) -> dict:
        covered, coverage = self._existing_window_covered(task)
        if covered:
            return {
                'status': 'skipped',
                'rows': int(coverage.get('rows') or 0),
                'inserted_rows': 0,
                'updated_rows': 0,
                'error': None,
                'skip_reason': '窗口已有足够完整的本地数据',
            }

        source = data_source_by_name(task['source'])
        if not source.supports(task['market']):
            raise ValueError(f"{task['source']} 不支持 {task['market']} 市场")
        df = source.fetch_5m(FetchRequest(
            market=task['market'],
            stock_code=task['stock_code'],
            interval='5min',
            start_date=task['start_date'],
            end_date=task['end_date'],
        ))
        normalized = _normalize_ohlcv(df)
        if normalized.empty:
            return {
                'status': 'empty',
                'rows': 0,
                'inserted_rows': 0,
                'updated_rows': 0,
                'error': '数据源返回空数据',
                'skip_reason': None,
            }

        if not db_manager.table_exists(task['market'], task['table']):
            db_manager.create_stock_table(task['market'], task['table'])
        stats = db_manager.upsert_data(task['market'], task['table'], normalized)
        return {
            'status': 'success',
            'rows': int(len(normalized)),
            'inserted_rows': int(stats.get('inserted_rows') or 0),
            'updated_rows': int(stats.get('updated_rows') or 0),
            'error': None,
            'skip_reason': None,
        }

    def _existing_window_covered(self, task: dict) -> tuple[bool, dict]:
        stats = db_manager.get_range_stats(
            task['market'],
            task['table'],
            task['start_date'],
            task['end_date'],
        )
        rows = int(stats.get('rows') or 0)
        trading_days = int(stats.get('trading_days') or 0)
        if rows <= 0 or trading_days <= 0:
            return False, stats

        start = pd.Timestamp(task['start_date']).normalize()
        end = pd.Timestamp(task['end_date']).normalize()
        expected_days = len(pd.bdate_range(start, end))
        min_days = max(1, int(expected_days * 0.8))
        expected_bars = self._expected_bars_per_day(task['market'])
        enough_days = trading_days >= min_days
        enough_rows = rows >= int(trading_days * expected_bars * 0.9)
        return bool(enough_days and enough_rows), stats

    def _expected_bars_per_day(self, market: str) -> int:
        if market == 'a':
            return int(Config.AKSHARE_EXPECTED_A_5MIN_BARS)
        if market == 'hk':
            return int(Config.AKSHARE_EXPECTED_HK_5MIN_BARS)
        if market == 'us':
            return 78
        return 1

    def _finish_task(
        self,
        task_id: str,
        *,
        status: str,
        rows: int,
        inserted_rows: int,
        updated_rows: int,
        error: str = None,
        skip_reason: str = None,
    ):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            now = _now_iso()
            summary = _compact_error(error) if error else None
            task.update({
                'status': status,
                'rows': int(rows or 0),
                'inserted_rows': int(inserted_rows or 0),
                'updated_rows': int(updated_rows or 0),
                'error': error,
                'error_summary': summary,
                'skip_reason': skip_reason,
                'finished_at': now,
                'updated_at': now,
            })
            logs = list(task.get('attempt_logs') or [])
            logs.append({
                'attempt': int(task.get('attempts') or 0),
                'source': task.get('source'),
                'status': status,
                'rows': int(rows or 0),
                'inserted_rows': int(inserted_rows or 0),
                'updated_rows': int(updated_rows or 0),
                'error': summary,
                'skip_reason': skip_reason,
                'finished_at': now,
            })
            task['attempt_logs'] = logs
            self._recalculate_job_locked(task['job_id'])
            self._persist_locked()

    def _recalculate_job_locked(self, job_id: str):
        job = self._jobs.get(job_id)
        if not job:
            return
        tasks = self._job_tasks_locked(job_id)
        total = len(tasks)
        success = len([t for t in tasks if t.get('status') == 'success'])
        failed = len([t for t in tasks if t.get('status') == 'failed'])
        empty = len([t for t in tasks if t.get('status') == 'empty'])
        skipped = len([t for t in tasks if t.get('status') == 'skipped'])
        running = len([t for t in tasks if t.get('status') == 'running'])
        pending = len([t for t in tasks if t.get('status') == 'pending'])
        completed = success + skipped

        if total == 0:
            status = 'failed'
        elif running:
            status = 'running'
        elif pending:
            status = 'queued' if completed == 0 and failed == 0 and empty == 0 else 'running'
        elif completed == total:
            status = 'completed'
        elif completed > 0:
            status = 'partial_failed'
        else:
            status = 'failed'

        inserted_rows = sum(int(t.get('inserted_rows') or 0) for t in tasks)
        updated_rows = sum(int(t.get('updated_rows') or 0) for t in tasks)
        source_rows = sum(int(t.get('rows') or 0) for t in tasks)
        progress = int(round((completed / total) * 100)) if total else 0
        now = _now_iso()
        job.update({
            'status': status,
            'progress': 100 if status == 'completed' else progress,
            'total_tasks': total,
            'success_tasks': success,
            'failed_tasks': failed,
            'empty_tasks': empty,
            'skipped_tasks': skipped,
            'pending_tasks': pending,
            'running_tasks': running,
            'inserted_rows': int(inserted_rows),
            'updated_rows': int(updated_rows),
            'source_rows': int(source_rows),
            'updated_at': now,
            'result': self._build_job_result_locked(job, tasks),
        })
        if status in ('completed', 'partial_failed', 'failed') and total:
            if not job.get('finished_at'):
                job['finished_at'] = now
            if status == 'failed' and not job.get('error'):
                job['error'] = '所有 Task 均未成功'
        else:
            job['finished_at'] = None
            job['error'] = None

    def _build_job_result_locked(self, job: dict, tasks: list[dict]) -> dict:
        results = []
        grouped: dict[tuple[str, str], list[dict]] = {}
        for task in tasks:
            grouped.setdefault((task['market'], task['stock_code']), []).append(task)

        for (market, stock_code), group in grouped.items():
            try:
                record = data_status_for_market(market, stock_code)
            except Exception:
                record = {
                    'market': market,
                    'stock_code': stock_code,
                    'display_code': group[0].get('display_code'),
                    'rows': 0,
                }
            failed = [t for t in group if t.get('status') == 'failed']
            empty = [t for t in group if t.get('status') == 'empty']
            skipped = [t for t in group if t.get('status') == 'skipped']
            warning = None
            if failed or empty:
                warning = f"有 {len(failed)} 个窗口任务失败，{len(empty)} 个窗口返回空数据"
            record.update({
                'task_backfill': True,
                'requested_trading_days': (job.get('params') or {}).get('days'),
                'request_mode': 'task_chunks',
                'source_rows': sum(int(t.get('rows') or 0) for t in group),
                'inserted_rows': sum(int(t.get('inserted_rows') or 0) for t in group),
                'updated_rows': sum(int(t.get('updated_rows') or 0) for t in group),
                'skipped_tasks': len(skipped),
                'partial': bool(failed or empty),
                'api_budget': (job.get('estimate') or {}).get('api_budget'),
            })
            if warning:
                record['warning'] = warning
            results.append(record)

        return {
            'success': True,
            'queued': True,
            'job_id': job.get('id'),
            'input': job.get('stock'),
            'count': len(results),
            'results': results,
        }

    def _job_tasks_locked(self, job_id: str) -> list[dict]:
        return sorted(
            [t for t in self._tasks.values() if t.get('job_id') == job_id],
            key=lambda t: int(t.get('seq') or 0),
        )

    def _public_job_locked(self, job_id: str, *, include_tasks: bool) -> dict:
        job = copy.deepcopy(self._jobs[job_id])
        if include_tasks:
            job['tasks'] = [self._public_task(task) for task in self._job_tasks_locked(job_id)]
        return job

    def _public_task(self, task: dict) -> dict:
        payload = copy.deepcopy(task)
        payload['available_sources'] = [
            source for source in list_data_sources(task.get('market'))
            if source.get('supports_market')
        ]
        return payload


data_job_service = DataJobService()
