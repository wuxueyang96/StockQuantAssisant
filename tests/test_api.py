import os
import pytest
from unittest.mock import patch, MagicMock


class TestAPI:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch('app.api.routes.registration_service') as mock_registration, \
             patch('app.api.routes.db_manager') as mock_db, \
             patch('app.api.routes.analyze_stock') as mock_analyze:
            self.mock_registration = mock_registration
            self.mock_db = mock_db
            self.mock_analyze = mock_analyze
            yield

    def test_health_check(self, client):
        resp = client.get('/api/health')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'ok'

    def test_frontend_index(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert 'StockQuant' in text
        assert '/static/css/app.css' in text
        assert '/static/js/app.js' in text
        assert 'chartDaysInput' in text
        assert '交易日' in text
        assert 'lightweight-charts' in text
        assert 'priceChart' in text
        assert 'chartLegend' in text
        assert 'refreshBtn' in text
        assert 'backfillDataBtn' in text
        assert '已注册股票' in text
        assert 'stockNameInput' in text
        assert 'registerByNameBtn' in text
        assert '补历史数据' in text
        assert '代码映射' not in text

    def test_register_stock_success(self, client):
        self.mock_registration.register_stock.return_value = {
            'success': True, 'message': '股票已注册',
            'registration_ids': ['A_000001.SZ_5min'],
        }
        resp = client.post('/api/stock/register', json={'stock': '000001'})
        assert resp.status_code == 200
        assert len(resp.get_json()['registration_ids']) == 1

    def test_register_multi_market(self, client):
        self.mock_registration.register_stock.return_value = {
            'success': True, 'message': '股票已注册',
            'registration_ids': ['HK_09988.HK_5min', 'US_BABA.US_5min'],
            'markets': [{'market': 'hk', 'stock_code': '09988'},
                        {'market': 'us', 'stock_code': 'BABA'}],
        }
        resp = client.post('/api/stock/register', json={'stock': '阿里巴巴'})
        data = resp.get_json()
        assert len(data['registration_ids']) == 2
        assert len(data['markets']) == 2

    def test_register_by_name_and_code(self, client):
        self.mock_registration.register_stock.return_value = {
            'success': True, 'message': '股票已注册',
            'registration_ids': ['HK_00700.HK_5min'],
            'markets': [{'market': 'hk', 'stock_code': '00700'}],
        }
        resp = client.post('/api/stock/register', json={
            'name': '腾讯控股',
            'market': 'hk',
            'code': '700',
        })
        assert resp.status_code == 200
        self.mock_db.upsert_stock_code.assert_called_once_with(
            name='腾讯控股',
            hk_code='00700',
        )
        self.mock_registration.register_stock.assert_called_once_with('腾讯控股')

    def test_register_already_exists(self, client):
        self.mock_registration.register_stock.return_value = {
            'success': True, 'message': '股票已存在',
            'registration_ids': ['A_000001.SZ_5min'],
        }
        resp = client.post('/api/stock/register', json={'stock': '000001'})
        assert '股票已存在' in resp.get_json()['message']

    def test_register_missing_param(self, client):
        resp = client.post('/api/stock/register', json={})
        assert resp.status_code == 400

    def test_register_empty_stock(self, client):
        resp = client.post('/api/stock/register', json={'stock': '  '})
        assert resp.status_code == 400

    def test_register_value_error(self, client):
        self.mock_registration.register_stock.side_effect = ValueError('无法识别')
        resp = client.post('/api/stock/register', json={'stock': 'invalid'})
        assert resp.status_code == 400

    def test_register_server_error(self, client):
        self.mock_registration.register_stock.side_effect = RuntimeError('crash')
        resp = client.post('/api/stock/register', json={'stock': '000001'})
        assert resp.status_code == 500

    def test_upsert_stock_code_new(self, client):
        resp = client.post('/api/stock/code', json={
            'name': '阿里巴巴', 'hk': '09988', 'us': 'BABA'
        })
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_upsert_stock_code_update(self, client):
        client.post('/api/stock/code', json={'name': '阿里巴巴', 'hk': '09988'})
        resp = client.post('/api/stock/code', json={'name': '阿里巴巴', 'us': 'BABA'})
        assert resp.status_code == 200

    def test_upsert_stock_code_no_market(self, client):
        resp = client.post('/api/stock/code', json={'name': 'test'})
        assert resp.status_code == 400

    def test_upsert_stock_code_missing_name(self, client):
        resp = client.post('/api/stock/code', json={})
        assert resp.status_code == 400

    def test_get_stock_codes(self, client):
        resp = client.get('/api/stock/codes')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_get_stock_registrations(self, client):
        self.mock_registration.get_stock_registrations.return_value = [
            {'id': 'A_000001.SZ_5min', 'market': 'a', 'interval': '5min'}
        ]
        resp = client.get('/api/stock/000001/registrations')
        assert resp.status_code == 200

    def test_get_all_registered_stocks(self, client):
        self.mock_registration.get_all_registered_stocks.return_value = [
            {'id': 'A_000001.SZ_5min'},
            {'id': 'A_600519.SS_5min'},
        ]
        resp = client.get('/api/registered-stocks')
        assert resp.get_json()['count'] == 2

    def test_delete_success(self, client):
        self.mock_registration.delete_registration.return_value = True
        resp = client.delete('/api/registered-stocks/A_000001.SZ_5min')
        assert resp.status_code == 200

    def test_delete_not_found(self, client):
        self.mock_registration.delete_registration.return_value = False
        resp = client.delete('/api/registered-stocks/nonexistent')
        assert resp.status_code == 404

    def test_registration_ids_follow_format(self, client):
        self.mock_registration.register_stock.return_value = {
            'success': True, 'message': '股票已注册',
            'registration_ids': ['A_000001.SZ_5min'],
        }
        resp = client.post('/api/stock/register', json={'stock': '000001'})
        for registration_id in resp.get_json()['registration_ids']:
            parts = registration_id.split('_')
            assert parts[0] in ('A', 'HK', 'US')
            assert parts[2] == '5min'

    def test_stock_decision_success(self, client):
        self.mock_analyze.return_value = {
            'success': True, 'input': '000001', 'interval': 'daily',
            'count': 1,
            'results': [{
                'market': 'a', 'market_label': 'A', 'stock_code': '000001',
                'display_code': '000001.SZ', 'position': 10.0,
                'position_label': '满仓', 'core_long': False, 'core_short': False,
                'resonance_buy': False, 'resonance_sell': False,
            }]
        }
        resp = client.post('/api/stock/decision', json={'stock': '000001'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['count'] == 1
        assert data['results'][0]['position'] == 10.0

    def test_stock_decision_missing_param(self, client):
        resp = client.post('/api/stock/decision', json={})
        assert resp.status_code == 400

    def test_stock_decision_invalid_interval(self, client):
        resp = client.post('/api/stock/decision', json={'stock': '000001', 'interval': '5min'})
        assert resp.status_code == 400

    def test_stock_decision_value_error(self, client):
        self.mock_analyze.side_effect = ValueError('无法识别')
        resp = client.post('/api/stock/decision', json={'stock': 'invalid'})
        assert resp.status_code == 400
