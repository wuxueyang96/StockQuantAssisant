import time

import pandas as pd


def _register_stock(app):
    from app.models.database import db_manager
    from app.services.registration_service import registration_service

    registration_id = 'A_000001.SZ_5min'
    registration_service.registered_stocks[registration_id] = {
        'market': 'a',
        'stock_code': '000001',
        'interval': '5min',
        'table': registration_id,
        'created_at': '2024-01-01T00:00:00',
        'active': True,
    }
    db_manager.save_registration(registration_id, registration_service.registered_stocks[registration_id])
    return registration_id


def _wait_job(service, job_id):
    for _ in range(80):
        job = service.get_job(job_id)
        if job and job['status'] in ('completed', 'partial_failed', 'failed'):
            return job
        time.sleep(0.05)
    raise AssertionError(f'job {job_id} did not finish')


def _one_row(request):
    ts = pd.Timestamp(request.start_date).normalize() + pd.Timedelta(hours=9, minutes=35)
    return pd.DataFrame({
        'Open': [10.0],
        'High': [10.5],
        'Low': [9.8],
        'Close': [10.2],
        'Volume': [100],
    }, index=pd.DatetimeIndex([ts]))


def _full_a_rows(start='2024-01-01', days=2):
    dates = pd.bdate_range(start, periods=days)
    am = pd.timedelta_range(start='9:30:00', periods=24, freq='5min')
    pm = pd.timedelta_range(start='13:00:00', periods=24, freq='5min')
    idx = []
    for day in dates:
        idx.extend([day + off for off in am])
        idx.extend([day + off for off in pm])
    return pd.DataFrame({
        'Open': [10.0] * len(idx),
        'High': [10.5] * len(idx),
        'Low': [9.8] * len(idx),
        'Close': [10.2] * len(idx),
        'Volume': [100] * len(idx),
    }, index=pd.DatetimeIndex(idx))


def test_backfill_job_splits_windows_and_persists_tasks(app, mocker):
    from app.services.data_job_service import data_job_service

    _register_stock(app)
    data_job_service.reset_for_tests()
    source = mocker.Mock(name='akshare')
    source.name = 'akshare'
    source.free_mode = False
    source.supports.return_value = True
    source.fetch_5m.side_effect = _one_row
    mocker.patch('app.services.data_job_service.data_source_by_name', return_value=source)
    mocker.patch('app.services.data_job_service.Config.AKSHARE_BACKFILL_CHUNK_DAYS', 3)

    job = data_job_service.enqueue_backfill(
        '000001',
        days=5,
        start_date='2024-01-01',
        end_date='2024-01-07',
        source='akshare',
    )
    finished = _wait_job(data_job_service, job['id'])

    assert finished['status'] == 'completed'
    assert finished['total_tasks'] == 3
    assert finished['success_tasks'] == 3
    assert finished['progress'] == 100
    assert finished['inserted_rows'] == 3
    assert source.fetch_5m.call_count == 3
    persisted = data_job_service.get_job(job['id'])
    assert [task['status'] for task in persisted['tasks']] == ['success', 'success', 'success']


def test_retry_failed_task_can_change_source(app, mocker):
    from app.services.data_job_service import data_job_service

    _register_stock(app)
    data_job_service.reset_for_tests()
    akshare = mocker.Mock(name='akshare')
    akshare.name = 'akshare'
    akshare.free_mode = False
    akshare.supports.return_value = True
    akshare.fetch_5m.side_effect = RuntimeError('akshare ssl failed')
    yfinance = mocker.Mock(name='yfinance')
    yfinance.name = 'yfinance'
    yfinance.free_mode = False
    yfinance.supports.return_value = True
    yfinance.fetch_5m.side_effect = _one_row

    def source_by_name(name):
        return {'akshare': akshare, 'yfinance': yfinance}[name]

    mocker.patch('app.services.data_job_service.data_source_by_name', side_effect=source_by_name)
    mocker.patch('app.services.data_job_service.Config.AKSHARE_BACKFILL_CHUNK_DAYS', 30)

    job = data_job_service.enqueue_backfill(
        '000001',
        days=5,
        start_date='2024-01-01',
        end_date='2024-01-02',
        source='akshare',
    )
    failed = _wait_job(data_job_service, job['id'])
    assert failed['status'] == 'failed'
    failed_task = failed['tasks'][0]
    assert failed_task['status'] == 'failed'

    data_job_service.retry_task(job['id'], failed_task['id'], source='yfinance')
    completed = _wait_job(data_job_service, job['id'])

    assert completed['status'] == 'completed'
    task = completed['tasks'][0]
    assert task['source'] == 'yfinance'
    assert task['retry_source'] == 'yfinance'
    assert task['attempts'] == 2
    assert task['status'] == 'success'
    assert yfinance.fetch_5m.call_count == 1


def test_backfill_task_skips_when_window_data_already_exists(app, mocker):
    from app.models.database import db_manager
    from app.services.data_job_service import data_job_service

    registration_id = _register_stock(app)
    db_manager.insert_data('a', registration_id, _full_a_rows())
    data_job_service.reset_for_tests()
    source = mocker.Mock(name='akshare')
    source.name = 'akshare'
    source.free_mode = False
    source.supports.return_value = True
    source.fetch_5m.side_effect = AssertionError('should not fetch covered window')
    mocker.patch('app.services.data_job_service.data_source_by_name', return_value=source)
    mocker.patch('app.services.data_job_service.Config.AKSHARE_BACKFILL_CHUNK_DAYS', 30)

    job = data_job_service.enqueue_backfill(
        '000001',
        days=2,
        start_date='2024-01-01',
        end_date='2024-01-02 15:00:00',
        source='akshare',
    )
    finished = _wait_job(data_job_service, job['id'])

    assert finished['status'] == 'completed'
    assert finished['success_tasks'] == 0
    assert finished['skipped_tasks'] == 1
    assert finished['progress'] == 100
    task = finished['tasks'][0]
    assert task['status'] == 'skipped'
    assert task['skip_reason']
    assert source.fetch_5m.call_count == 0
