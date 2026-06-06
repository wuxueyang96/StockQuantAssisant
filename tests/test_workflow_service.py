import os
import pytest
from unittest.mock import MagicMock, patch
from app.services.workflow_service import WorkflowService
from app.config import Config


class TestWorkflowService:
    @pytest.fixture
    def mock_deps(self):
        with patch('app.services.workflow_service.collect_and_store') as mock_collect, \
             patch('app.services.workflow_service.detect_market') as mock_detect, \
             patch('app.services.workflow_service.db_manager') as mock_db:
            mock_db.table_exists.return_value = False
            mock_db.save_workflow = MagicMock()
            mock_db.load_workflows.return_value = {}
            mock_collect.return_value = 200
            yield mock_detect, mock_collect, mock_db

    @pytest.fixture
    def workflow_service(self, mock_deps):
        mock_detect, _, mock_db = mock_deps
        ws = WorkflowService()
        ws.workflows = {}
        return ws

    def test_workflow_id_format(self, workflow_service):
        # 采集层只保留 5min；高粒度由 resample 运行时合成。
        assert workflow_service.get_workflow_id('a', '000001', '5min') == 'A_000001.SZ_5min'
        assert workflow_service.get_workflow_id('hk', '00700', '5min') == 'HK_00700.HK_5min'
        assert workflow_service.get_workflow_id('us', 'AAPL', '5min') == 'US_AAPL.US_5min'

    def test_register_single_market(self, workflow_service, mock_deps):
        mock_detect, mock_collect, mock_db = mock_deps
        mock_detect.return_value = [('a', '000001')]

        result = workflow_service.register_stock('000001')
        assert result['success'] is True
        # 每个市场只创建 1 个 5min 工作流
        assert len(result['workflows']) == 1
        assert mock_db.save_workflow.call_count == 1
        wf_id = result['workflows'][0]
        parts = wf_id.split('_')
        assert parts[0] == 'A'
        assert parts[-1] == '5min'

    def test_register_free_mode_skips_initial_fetch(self, workflow_service, mock_deps):
        mock_detect, mock_collect, mock_db = mock_deps
        mock_detect.return_value = [('a', '000001')]
        mock_db.get_latest_timestamp.return_value = None

        result = workflow_service.register_stock('000001')

        assert result['success'] is True
        mock_collect.assert_not_called()

    def test_register_multi_market(self, workflow_service, mock_deps):
        mock_detect, mock_collect, mock_db = mock_deps
        mock_detect.return_value = [('hk', '09988'), ('us', 'BABA')]

        result = workflow_service.register_stock('阿里巴巴')
        assert result['success'] is True
        # 双市场 → 2 个 5min 工作流
        assert len(result['workflows']) == 2
        assert len(result['markets']) == 2
        assert mock_db.save_workflow.call_count == 2

        hk_workflows = [w for w in result['workflows'] if w.startswith('HK_')]
        us_workflows = [w for w in result['workflows'] if w.startswith('US_')]
        assert len(hk_workflows) == 1
        assert len(us_workflows) == 1
        assert all(w.endswith('_5min') for w in result['workflows'])

    def test_register_duplicate(self, workflow_service, mock_deps):
        mock_detect, mock_collect, mock_db = mock_deps
        mock_detect.return_value = [('a', '000001')]

        workflow_service.register_stock('000001')
        result = workflow_service.register_stock('000001')
        assert '工作流已存在' in result['message']

    def test_get_stock_workflows(self, workflow_service, mock_deps):
        mock_detect, mock_collect, mock_db = mock_deps
        mock_detect.return_value = [('a', '000001')]
        workflow_service.register_stock('000001')
        workflows = workflow_service.get_stock_workflows('000001')
        assert len(workflows) == 1
        assert workflows[0]['interval'] == '5min'

    def test_delete_workflow(self, workflow_service, mock_deps):
        mock_detect, mock_collect, mock_db = mock_deps
        mock_detect.return_value = [('a', '000001')]
        workflow_service.register_stock('000001')
        assert workflow_service.delete_workflow('A_000001.SZ_5min') is True
        assert workflow_service.delete_workflow('A_000001.SZ_5min') is False

    def test_table_name_matches_workflow_id(self, workflow_service, mock_deps):
        mock_detect, mock_collect, mock_db = mock_deps
        mock_detect.return_value = [('a', '000001')]
        result = workflow_service.register_stock('000001')
        for wf_id in result['workflows']:
            wf_data = workflow_service.workflows[wf_id]
            assert wf_data['table'] == wf_id

    def test_multi_market_partial(self, workflow_service, mock_deps):
        mock_detect, mock_collect, mock_db = mock_deps
        mock_detect.return_value = [('hk', '09988'), ('us', 'BABA')]

        r1 = workflow_service.register_stock('阿里巴巴')
        assert len(r1['workflows']) == 2

        r2 = workflow_service.register_stock('阿里巴巴')
        assert '工作流已存在' in r2['message']
