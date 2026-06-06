"""Chart-data API tests.

Charts are rendered in the WebUI now; the backend exposes JSON series instead
of matplotlib/mplfinance PNG bytes.
"""
import numpy as np
import pandas as pd
import pytest


def _make_uptrend_5min(n_days: int = 120) -> pd.DataFrame:
    dates = pd.bdate_range('2024-01-01', periods=n_days)
    am = pd.timedelta_range(start='9:30:00', periods=24, freq='5min')
    pm = pd.timedelta_range(start='13:00:00', periods=24, freq='5min')
    idx = []
    for d in dates:
        for off in am:
            idx.append(d + off)
        for off in pm:
            idx.append(d + off)
    idx = pd.DatetimeIndex(idx)
    n = len(idx)
    closes = np.linspace(50.0, 150.0, n)
    return pd.DataFrame({
        'timestamp': idx,
        'open': closes,
        'high': closes * 1.01,
        'low': closes * 0.99,
        'close': closes,
        'volume': [1_000_000] * n,
    })


class TestChartDataEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self, app):
        from app.models.database import db_manager
        db_manager.insert_data('a', 'A_000001.SZ_5min', _make_uptrend_5min())

    def test_chart_data_returns_json_series(self, client):
        resp = client.get('/api/stock/chart-data?stock=000001&bars=60')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data['success'] is True
        result = data['results'][0]
        assert result['display_code'] == '000001.SZ'
        assert len(result['daily']['candles']) == 60
        assert len(result['daily']['trend']) == 60
        assert len(result['daily']['sequence']) == 60
        assert len(result['daily']['decisions']) == 60
        assert len(result['daily']['macd']) == 60
        assert {'60min', '90min', '120min'} <= set(result['intraday'])
        assert result['intraday']['60min']['macd']
        assert result['intraday']['60min']['structure']

    def test_chart_data_90min_and_120min_are_distinct(self, client):
        resp = client.get('/api/stock/chart-data?stock=000001&bars=40')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        result = resp.get_json()['results'][0]
        candles_90 = result['intraday']['90min']['candles']
        candles_120 = result['intraday']['120min']['candles']
        macd_90 = result['intraday']['90min']['macd']
        macd_120 = result['intraday']['120min']['macd']

        assert candles_90 != candles_120
        assert macd_90 != macd_120

    def test_chart_data_clamps_bars(self, client):
        resp = client.get('/api/stock/chart-data?stock=000001&bars=9999')
        assert resp.status_code == 200
        result = resp.get_json()['results'][0]
        assert len(result['daily']['candles']) <= 500

    def test_chart_legacy_alias_returns_json(self, client):
        resp = client.get('/api/stock/chart?stock=000001&bars=30')
        assert resp.status_code == 200
        assert resp.mimetype == 'application/json'
        assert resp.get_json()['results'][0]['daily']['candles']

    def test_chart_data_missing_stock(self, client):
        resp = client.get('/api/stock/chart-data')
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_chart_data_invalid_bars(self, client):
        resp = client.get('/api/stock/chart-data?stock=000001&bars=abc')
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_chart_data_no_data_returns_error_record(self, client):
        resp = client.get('/api/stock/chart-data?stock=00700')
        assert resp.status_code == 200
        result = resp.get_json()['results'][0]
        assert 'error' in result
