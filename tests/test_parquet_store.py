import os
import sys
import tempfile

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.models.database import DatabaseManager


def _make_df(start_date, periods):
    dates = pd.date_range(start_date, periods=periods, freq='B')
    df = pd.DataFrame({
        'Open': [10.0] * periods,
        'High': [11.0] * periods,
        'Low': [9.0] * periods,
        'Close': [10.5] * periods,
        'Volume': [1000000] * periods,
        'Dividends': [0.0] * periods,
        'Stock Splits': [0.0] * periods,
    }, index=dates)
    df.index.name = 'timestamp'
    return df


class TestParquetStoreLocal:
    """Test Parquet storage logic with local files (no OSS needed)."""

    @pytest.fixture(autouse=True)
    def setup_local(self, temp_db_dir):
        from app.config import Config
        Config.DATA_DIR = temp_db_dir
        Config.OSS_BUCKET = None
        yield

    def test_insert_and_read_market_data(self):
        db = DatabaseManager()
        df = _make_df('2024-01-01', 10)
        count = db.insert_data('a', 'A_TEST_daily', df)
        assert count == 10
        assert db.table_exists('a', 'A_TEST_daily')

        result = db.get_data('a', 'A_TEST_daily')
        assert len(result) == 10
        assert 'timestamp' in result.columns
        db.close_all()

    def test_dedup_across_inserts(self):
        db = DatabaseManager()
        df1 = _make_df('2024-01-01', 5)
        df2 = _make_df('2024-01-01', 8)

        db.insert_data('a', 'A_DEDUP_daily', df1)
        count2 = db.insert_data('a', 'A_DEDUP_daily', df2)
        assert count2 == 3
        result = db.get_data('a', 'A_DEDUP_daily')
        assert len(result) == 8
        db.close_all()

    def test_metadata_persists_across_sessions(self):
        db = DatabaseManager()
        db.upsert_stock_code('test_name', a_code='000001', hk_code='09988')
        db.save_registration('reg1', {
            'market': 'a', 'stock_code': '000001', 'interval': '5min',
            'table': 'A_000001.SZ_5min',
            'created_at': '2024-01-01', 'active': True,
        })
        db.close_all()

        db2 = DatabaseManager()
        assert db2.get_stock_codes('test_name') == ('000001', '09988', None)
        registered = db2.load_registered_stocks()
        assert 'reg1' in registered
        db2.close_all()

    def test_read_nonexistent_returns_empty(self):
        db = DatabaseManager()
        assert not db.table_exists('a', 'nonexistent')
        df = db.get_data('a', 'nonexistent')
        assert df.empty
        assert db.get_latest_timestamp('a', 'nonexistent') is None
        db.close_all()

    def test_roundtrip_full_flow(self):
        db = DatabaseManager()
        db.upsert_stock_code('茅台', a_code='600519')

        df = _make_df('2024-06-01', 20)
        db.insert_data('a', 'A_600519_daily', df)

        codes = db.get_all_stock_codes()
        assert len(codes) == 1
        assert codes.iloc[0]['name'] == '茅台'

        result = db.get_data('a', 'A_600519_daily')
        assert len(result) == 20

        latest = db.get_latest_timestamp('a', 'A_600519_daily')
        assert latest is not None
        db.close_all()

    def test_drop_clears_data(self):
        db = DatabaseManager()
        df = _make_df('2024-01-01', 3)
        db.insert_data('a', 'A_DROP_daily', df)
        assert db.table_exists('a', 'A_DROP_daily')
        db.drop_table('a', 'A_DROP_daily')
        assert not db.table_exists('a', 'A_DROP_daily')
        db.close_all()

    def test_insert_respects_column_order(self):
        db = DatabaseManager()
        dates = pd.date_range('2024-01-01', periods=2, freq='B')
        df = pd.DataFrame({
            'close': [10.5, 11.5],
            'high': [11.0, 12.0],
            'low': [9.0, 10.0],
            'open': [10.0, 11.0],
            'volume': [1000, 2000],
            'dividends': [0.0, 0.0],
            'stock_splits': [0.0, 0.0],
            'extra_col': ['ignored', 'ignored'],
        }, index=dates)
        df.index.name = 'timestamp'

        count = db.insert_data('a', 'A_COLORDER_daily', df)
        assert count == 2
        result = db.get_data('a', 'A_COLORDER_daily')
        assert len(result) == 2
        assert 'timestamp' in result.columns
        db.close_all()
