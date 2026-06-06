import os
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock
from app.config import Config


class TestCollectAndStore:
    @pytest.fixture
    def mock_yf_data(self):
        dates = pd.date_range('2024-01-01', periods=30, freq='B')
        df = pd.DataFrame({
            'Open': [10.0 + i * 0.1 for i in range(30)],
            'High': [11.0 + i * 0.1 for i in range(30)],
            'Low': [9.0 + i * 0.1 for i in range(30)],
            'Close': [10.5 + i * 0.1 for i in range(30)],
            'Volume': [1000000] * 30,
            'Dividends': [0.0] * 30,
            'Stock Splits': [0.0] * 30,
        }, index=dates)
        return df

    @pytest.fixture
    def setup_collect(self, temp_db_dir, mock_yf_data):
        from app.models.database import db_manager

        Config.DATA_DIR = temp_db_dir
        Config.OSS_BUCKET = None
        db_manager.close_all()
        db_manager.__init__()

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_yf_data

        with patch('app.services.stock_service.is_trading_time', return_value=True), \
             patch('app.services.stock_service._is_akshare_available', return_value=False), \
             patch('app.services.stock_service.yf.Ticker', return_value=mock_ticker):
            yield
        db_manager.close_all()

    def test_collect_creates_table(self, setup_collect):
        from app.services.stock_service import collect_and_store
        from app.models.database import db_manager

        # 采集层只写 5min 表（daily 等高粒度由 resample 运行时合成）
        rows = collect_and_store('a', '000001', '5min')
        assert rows > 0
        assert db_manager.table_exists('a', 'A_000001.SZ_5min')

    def test_collect_skip_non_trading(self, setup_collect):
        from app.services.stock_service import collect_and_store
        with patch('app.services.stock_service.is_trading_time', return_value=False):
            assert collect_and_store('a', '000001', '5min') == 0

    def test_collect_dedup(self, setup_collect):
        from app.services.stock_service import collect_and_store
        from app.models.database import db_manager
        rows1 = collect_and_store('a', '000001', '5min')
        collect_and_store('a', '000001', '5min')
        data = db_manager.get_data('a', 'A_000001.SZ_5min')
        assert len(data) == rows1

    def test_collect_redirects_legacy_interval_to_5min(self, setup_collect):
        """旧调用方传 'daily' 也只写 5min 表，不再产生 *_daily 表。"""
        from app.services.stock_service import collect_and_store
        from app.models.database import db_manager
        for interval in ['daily', '60min']:
            collect_and_store('a', '000001', interval)
        assert db_manager.table_exists('a', 'A_000001.SZ_5min')
        assert not db_manager.table_exists('a', 'A_000001.SZ_daily')
        assert not db_manager.table_exists('a', 'A_000001.SZ_60min')


class TestDatabaseMetadata:
    @pytest.fixture
    def meta_db(self, temp_db_dir):
        Config.DATA_DIR = temp_db_dir
        Config.OSS_BUCKET = None
        from app.models.database import db_manager
        db_manager.close_all()
        db_manager.__init__()
        yield db_manager
        db_manager.close_all()

    def test_upsert_and_get(self, meta_db):
        meta_db.upsert_stock_code('阿里巴巴', hk_code='09988', us_code='BABA')
        row = meta_db.get_stock_codes('阿里巴巴')
        assert row == (None, '09988', 'BABA')

    def test_upsert_update(self, meta_db):
        meta_db.upsert_stock_code('阿里巴巴', hk_code='09988')
        meta_db.upsert_stock_code('阿里巴巴', us_code='BABA')
        row = meta_db.get_stock_codes('阿里巴巴')
        assert row == (None, '09988', 'BABA')

    def test_get_not_found(self, meta_db):
        assert meta_db.get_stock_codes('nonexistent') is None

    def test_upsert_full(self, meta_db):
        meta_db.upsert_stock_code('比亚迪', a_code='002594', hk_code='01211')
        row = meta_db.get_stock_codes('比亚迪')
        assert row == ('002594', '01211', None)

    def test_get_all(self, meta_db):
        meta_db.upsert_stock_code('阿里巴巴', hk_code='09988', us_code='BABA')
        meta_db.upsert_stock_code('贵州茅台', a_code='600519')
        df = meta_db.get_all_stock_codes()
        assert len(df) == 2

    def test_delete(self, meta_db):
        meta_db.upsert_stock_code('test', a_code='000001')
        meta_db.delete_stock_code('test')
        assert meta_db.get_stock_codes('test') is None


class TestIntegration:
    @pytest.fixture
    def full_setup(self, temp_db_dir):
        from app.models.database import db_manager

        Config.DATA_DIR = temp_db_dir
        Config.OSS_BUCKET = None
        db_manager.close_all()
        db_manager.__init__()

        dates = pd.date_range('2024-01-01', periods=300, freq='B')
        df = pd.DataFrame({
            'Open': [10.0 + i * 0.1 for i in range(300)],
            'High': [11.0 + i * 0.1 for i in range(300)],
            'Low': [9.0 + i * 0.1 for i in range(300)],
            'Close': [10.5 + i * 0.1 for i in range(300)],
            'Volume': [1000000] * 300,
            'Dividends': [0.0] * 300,
            'Stock Splits': [0.0] * 300,
        }, index=dates)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df

        with patch('app.services.stock_service.is_trading_time', return_value=True), \
             patch('app.services.stock_service._is_akshare_available', return_value=False), \
             patch('app.services.stock_service.yf.Ticker', return_value=mock_ticker):
            yield

        db_manager.close_all()

    def test_full_register_single(self, full_setup):
        from app.services.registration_service import registration_service
        from app.models.database import db_manager

        registration_service.registered_stocks = {}
        result = registration_service.register_stock('000001')
        assert result['success'] is True
        # 单市场仅创建 1 个 5min 注册记录
        assert len(result['registration_ids']) == 1
        assert result['registration_ids'][0].endswith('_5min')

    def test_code_register_then_stock_register(self, full_setup):
        from app.models.database import db_manager
        from app.services.registration_service import registration_service

        db_manager.upsert_stock_code('阿里巴巴', hk_code='09988', us_code='BABA')
        registration_service.registered_stocks = {}
        result = registration_service.register_stock('阿里巴巴')
        assert result['success'] is True
        # 双市场 → 2 个 5min 注册记录（HK + US）
        assert len(result['registration_ids']) == 2
        assert all(x.endswith('_5min') for x in result['registration_ids'])
