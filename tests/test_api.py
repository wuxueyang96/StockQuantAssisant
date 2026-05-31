import os
import pytest
from unittest.mock import patch, MagicMock


class TestAPI:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch('app.api.routes.job_scheduler') as mock_scheduler, \
             patch('app.api.routes.workflow_service') as mock_ws, \
             patch('app.api.routes.db_manager') as mock_db, \
             patch('app.api.routes.analyze_stock') as mock_analyze:
            mock_scheduler.scheduler.running = True
            self.mock_scheduler = mock_scheduler
            self.mock_ws = mock_ws
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

    def test_register_stock_success(self, client):
        self.mock_ws.register_stock.return_value = {
            'success': True, 'message': '工作流已创建',
            'workflows': ['A_000001.SZ_daily', 'A_000001.SZ_120min',
                          'A_000001.SZ_90min', 'A_000001.SZ_60min'],
        }
        resp = client.post('/api/stock/register', json={'stock': '000001'})
        assert resp.status_code == 200
        assert len(resp.get_json()['workflows']) == 4

    def test_register_multi_market(self, client):
        self.mock_ws.register_stock.return_value = {
            'success': True, 'message': '工作流已创建',
            'workflows': ['HK_09988.HK_daily', 'HK_09988.HK_120min',
                          'HK_09988.HK_90min', 'HK_09988.HK_60min',
                          'US_BABA.US_daily', 'US_BABA.US_120min',
                          'US_BABA.US_90min', 'US_BABA.US_60min'],
            'markets': [{'market': 'hk', 'stock_code': '09988'},
                        {'market': 'us', 'stock_code': 'BABA'}],
        }
        resp = client.post('/api/stock/register', json={'stock': '阿里巴巴'})
        data = resp.get_json()
        assert len(data['workflows']) == 8
        assert len(data['markets']) == 2

    def test_register_already_exists(self, client):
        self.mock_ws.register_stock.return_value = {
            'success': True, 'message': '工作流已存在',
            'workflows': ['A_000001.SZ_daily'],
        }
        resp = client.post('/api/stock/register', json={'stock': '000001'})
        assert '工作流已存在' in resp.get_json()['message']

    def test_register_missing_param(self, client):
        resp = client.post('/api/stock/register', json={})
        assert resp.status_code == 400

    def test_register_empty_stock(self, client):
        resp = client.post('/api/stock/register', json={'stock': '  '})
        assert resp.status_code == 400

    def test_register_value_error(self, client):
        self.mock_ws.register_stock.side_effect = ValueError('无法识别')
        resp = client.post('/api/stock/register', json={'stock': 'invalid'})
        assert resp.status_code == 400

    def test_register_server_error(self, client):
        self.mock_ws.register_stock.side_effect = RuntimeError('crash')
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

    def test_get_stock_workflows(self, client):
        self.mock_ws.get_stock_workflows.return_value = [
            {'id': 'A_000001.SZ_daily', 'market': 'a', 'interval': 'daily'}
        ]
        resp = client.get('/api/stock/000001/workflows')
        assert resp.status_code == 200

    def test_get_all_workflows(self, client):
        self.mock_ws.get_all_workflows.return_value = [
            {'id': 'A_000001.SZ_daily'},
            {'id': 'A_000001.SZ_120min'},
        ]
        resp = client.get('/api/workflows')
        assert resp.get_json()['count'] == 2

    def test_delete_success(self, client):
        self.mock_ws.delete_workflow.return_value = True
        resp = client.delete('/api/workflows/A_000001.SZ_daily')
        assert resp.status_code == 200

    def test_delete_not_found(self, client):
        self.mock_ws.delete_workflow.return_value = False
        resp = client.delete('/api/workflows/nonexistent')
        assert resp.status_code == 404

    def test_workflow_ids_follow_format(self, client):
        self.mock_ws.register_stock.return_value = {
            'success': True, 'message': '工作流已创建',
            'workflows': ['A_000001.SZ_daily', 'A_000001.SZ_120min',
                          'A_000001.SZ_90min', 'A_000001.SZ_60min'],
        }
        resp = client.post('/api/stock/register', json={'stock': '000001'})
        for wf_id in resp.get_json()['workflows']:
            parts = wf_id.split('_')
            assert parts[0] in ('A', 'HK', 'US')
            assert parts[2] in ('daily', '120min', '90min', '60min')

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
