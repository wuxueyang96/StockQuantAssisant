import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def disable_strict_backfill_by_default(mocker):
    mocker.patch('app.services.data_service.Config.AKSHARE_STRICT_BACKFILL', False)


def _make_5min(n_days: int = 40, start: str = '2024-01-01') -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n_days)
    am = pd.timedelta_range(start='9:30:00', periods=24, freq='5min')
    pm = pd.timedelta_range(start='13:00:00', periods=24, freq='5min')
    idx = []
    for d in dates:
        idx.extend([d + off for off in am])
        idx.extend([d + off for off in pm])
    closes = np.linspace(10.0, 20.0, len(idx))
    return pd.DataFrame({
        'timestamp': pd.DatetimeIndex(idx),
        'open': closes,
        'high': closes + 0.5,
        'low': closes - 0.5,
        'close': closes,
        'volume': [1000] * len(idx),
    })


@pytest.fixture
def registered_stock(app):
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
    db_manager.insert_data('a', registration_id, _make_5min(n_days=40))
    yield registration_id
    registration_service.registered_stocks.pop(registration_id, None)


def test_data_status_endpoint(client, registered_stock):
    resp = client.get('/api/stock/data-status?stock=000001')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()['results'][0]
    assert result['registered'] is True
    assert result['rows'] == 40 * 48
    assert result['daily_bars'] == 40
    assert result['first_timestamp']
    assert result['last_timestamp']


def test_refresh_endpoint_uses_collect_and_store(client, registered_stock, mocker):
    source = mocker.Mock(free_mode=False)
    mocker.patch('app.services.data_service.active_data_source', return_value=source)
    mock_collect = mocker.patch('app.services.data_service.collect_and_store', return_value=5)
    resp = client.post('/api/stock/refresh', json={'stock': '000001', 'history_days': 3})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()['results'][0]
    assert result['rows_inserted'] == 5
    assert result['refresh_days'] == 3
    mock_collect.assert_called_once()
    assert mock_collect.call_args.kwargs['history_days'] == 3


def test_refresh_all_endpoint_refreshes_registered_code(client, registered_stock, mocker):
    from app.models.database import db_manager

    db_manager.upsert_stock_code('测试股票', a_code='000001')
    source = mocker.Mock(free_mode=False)
    mocker.patch('app.services.data_service.active_data_source', return_value=source)
    mock_collect = mocker.patch('app.services.data_service.collect_and_store', return_value=7)

    resp = client.post('/api/refresh', json={'history_days': 2})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload['success'] is True
    assert payload['total_queries'] == 1
    assert payload['total_rows_inserted'] == 7
    mock_collect.assert_called_once()
    assert mock_collect.call_args.kwargs['history_days'] == 2


def test_refresh_endpoint_free_mode_uses_one_latest_page(client, registered_stock, mocker):
    df = _make_5min(n_days=1, start='2024-03-15')
    source = mocker.Mock(free_mode=True, refresh_limit=1000)
    mocker.patch('app.services.data_service.active_data_source', return_value=source)
    mocker.patch('app.services.data_service.Config.ITICK_FREE_REFRESH_LIMIT', 1000)
    mock_fetch = mocker.patch('app.services.data_service.fetch_stock_data', return_value=df)

    resp = client.post('/api/stock/refresh', json={'stock': '000001'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()['results'][0]
    assert result['refresh_mode'] == 'free_latest_page'
    assert result['rows_inserted'] == len(df)
    assert result['api_budget']['request_count'] == 1
    mock_fetch.assert_called_once()
    assert mock_fetch.call_args.kwargs['max_pages'] == 1
    assert mock_fetch.call_args.kwargs['limit'] == 1000
    assert mock_fetch.call_args.kwargs['start_date'] is not None


def test_backfill_endpoint_upserts_history(client, registered_stock, mocker):
    df = _make_5min(n_days=5, start='2023-12-25')
    mock_fetch = mocker.patch('app.services.data_service.fetch_stock_data', return_value=df)

    resp = client.post('/api/stock/backfill', json={'stock': '000001', 'days': 200})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()['results'][0]
    assert result['input_rows'] == len(df)
    assert result['inserted_rows'] > 0
    assert result['rows_after'] > result['rows_before']
    assert result['source_trading_days'] == 5
    assert result['request_mode'] == 'backfill_before_first_timestamp'
    assert result['requested_trading_days'] == 200
    assert result['request_calendar_days'] > 200
    assert mock_fetch.call_args.kwargs['start_date'] is not None
    assert mock_fetch.call_args.kwargs['end_date'] is not None
    assert mock_fetch.call_args.kwargs['history_days'] == 200


def test_backfill_days_mean_trading_days_and_trim_result(client, registered_stock, mocker):
    df = _make_5min(n_days=220, start='2023-01-02')
    mocker.patch('app.services.data_service.fetch_stock_data', return_value=df)

    resp = client.post('/api/stock/backfill', json={'stock': '000001', 'days': 200})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()['results'][0]
    assert result['requested_days'] == 200
    assert result['requested_trading_days'] == 200
    assert result['source_trading_days'] == 200
    assert result['input_rows'] == 200 * 48
    assert result['partial'] is False


def test_backfill_empty_source_keeps_existing_data_as_warning(client, registered_stock, mocker):
    mocker.patch('app.services.data_service.fetch_stock_data', return_value=pd.DataFrame())

    resp = client.post('/api/stock/backfill', json={'stock': '000001', 'days': 200})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()['results'][0]
    assert 'error' not in result
    assert result['warning']
    assert result['no_data'] is True
    assert result['requested_trading_days'] == 200
    assert result['request_calendar_days'] > 200
    assert result['inserted_rows'] == 0
    assert result['updated_rows'] == 0
    assert result['rows_after'] == result['rows_before']


def test_backfill_akshare_strict_chunks_returns_quality_report(client, registered_stock, mocker):
    df = _make_5min(n_days=3, start='2023-12-25')
    source = mocker.Mock(name='akshare', free_mode=False)
    source.name = 'akshare'
    source.supports.return_value = True
    source.fetch_5m_strict.return_value = {
        'df': df.set_index('timestamp'),
        'data_source': 'akshare',
        'strict': True,
        'request_count': 3,
        'minute_request_count': 2,
        'daily_request_count': 1,
        'window_count': 2,
        'completed_windows': 2,
        'failed_windows': [],
        'empty_windows': [],
        'windows': [],
        'quality_report': {
            'issue_count': 0,
            'minute': {'issue_count': 0},
            'daily_check': {'checked': True, 'issue_count': 0},
        },
    }
    mocker.patch('app.services.data_service.Config.AKSHARE_STRICT_BACKFILL', True)
    mocker.patch('app.services.data_service.active_data_source', return_value=source)

    resp = client.post('/api/stock/backfill', json={'stock': '000001', 'days': 30})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()['results'][0]
    assert result['strict_backfill'] is True
    assert result['strict_report']['request_count'] == 3
    assert result['api_budget']['daily_request_count'] == 1
    assert result['quality_report']['issue_count'] == 0
    assert result['request_mode'] == 'backfill_before_first_timestamp_strict_chunks'
    source.fetch_5m_strict.assert_called_once()
    request = source.fetch_5m_strict.call_args.args[0]
    assert request.market == 'a'
    assert request.stock_code == '000001'
    assert request.start_date is not None
    assert request.end_date is not None


def test_backfill_estimate_akshare_strict_aggregate_includes_window_counts(client, mocker):
    mocker.patch('app.services.data_service.Config.AKSHARE_STRICT_BACKFILL', True)
    mocker.patch('app.services.data_service.Config.AKSHARE_DAILY_CHECK', True)
    mocker.patch('app.services.data_service.Config.AKSHARE_BACKFILL_CHUNK_DAYS', 30)

    resp = client.get('/api/stock/backfill-estimate?stock=300274&days=200')

    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload['api_budget']['strict'] is True
    assert payload['api_budget']['request_count'] == 12
    assert payload['api_budget']['window_count'] == 11
    assert payload['api_budget']['daily_request_count'] == 1
    detail = payload['results'][0]['api_budget']
    assert detail['request_count'] == 12
    assert detail['window_count'] == 11


def test_refresh_unregistered_returns_error(client):
    resp = client.post('/api/stock/refresh', json={'stock': '000001'})

    assert resp.status_code == 200
    result = resp.get_json()['results'][0]
    assert 'error' in result
