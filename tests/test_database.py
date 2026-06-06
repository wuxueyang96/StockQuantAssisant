import os
import pandas as pd
import pytest
from app.models.database import DatabaseManager


def _setup_local_config(temp_dir):
    from app.config import Config
    Config.DATA_DIR = temp_dir
    Config.OSS_BUCKET = None


class TestDatabaseManager:
    @pytest.fixture
    def db(self, temp_db_dir):
        _setup_local_config(temp_db_dir)
        mgr = DatabaseManager()
        yield mgr
        mgr.close_all()

    def test_get_connection_same_instance(self, db):
        conn_a = db.get_connection('a')
        conn_hk = db.get_connection('hk')
        assert conn_a is conn_hk

    def test_create_table_is_noop(self, db):
        db.create_stock_table('a', 'A_000001.SZ_daily')
        assert not db.table_exists('a', 'A_000001.SZ_daily')

    def test_table_not_exists(self, db):
        assert not db.table_exists('a', 'nonexistent_table')

    def test_insert_and_get_data(self, db):
        dates = pd.date_range('2024-01-01', periods=10, freq='B')
        df = pd.DataFrame({
            'Open': [10.0 + i for i in range(10)],
            'High': [11.0 + i for i in range(10)],
            'Low': [9.0 + i for i in range(10)],
            'Close': [10.5 + i for i in range(10)],
            'Volume': [1000000] * 10,
            'Dividends': [0.0] * 10,
            'Stock Splits': [0.0] * 10,
        }, index=dates)

        count = db.insert_data('a', 'A_000001.SZ_daily', df)
        assert count == 10

        assert db.table_exists('a', 'A_000001.SZ_daily')

        result = db.get_data('a', 'A_000001.SZ_daily')
        assert len(result) == 10

    def test_insert_deduplication(self, db):
        dates = pd.date_range('2024-01-01', periods=5, freq='B')
        df1 = pd.DataFrame({
            'Open': [10.0] * 5, 'High': [11.0] * 5, 'Low': [9.0] * 5,
            'Close': [10.5] * 5, 'Volume': [1000000] * 5,
            'Dividends': [0.0] * 5, 'Stock Splits': [0.0] * 5,
        }, index=dates)

        db.insert_data('a', 'A_000001.SZ_daily', df1)
        db.insert_data('a', 'A_000001.SZ_daily', df1)
        result = db.get_data('a', 'A_000001.SZ_daily')
        assert len(result) == 5

    def test_upsert_data_backfills_and_updates(self, db):
        dates = pd.date_range('2024-01-03', periods=3, freq='B')
        df1 = pd.DataFrame({
            'Open': [10.0, 11.0, 12.0],
            'High': [11.0, 12.0, 13.0],
            'Low': [9.0, 10.0, 11.0],
            'Close': [10.5, 11.5, 12.5],
            'Volume': [1000, 1000, 1000],
        }, index=dates)
        db.insert_data('a', 'A_000001.SZ_5min', df1)

        dates2 = pd.to_datetime(['2024-01-01', '2024-01-03', '2024-01-08'])
        df2 = pd.DataFrame({
            'Open': [8.0, 10.0, 13.0],
            'High': [9.0, 11.0, 14.0],
            'Low': [7.0, 9.0, 12.0],
            'Close': [8.5, 99.0, 13.5],
            'Volume': [900, 999, 1000],
        }, index=dates2)
        stats = db.upsert_data('a', 'A_000001.SZ_5min', df2)

        assert stats['input_rows'] == 3
        assert stats['inserted_rows'] == 2
        assert stats['updated_rows'] == 1
        assert stats['rows_after'] == 5
        result = db.get_data('a', 'A_000001.SZ_5min', limit=10)
        row = result[pd.to_datetime(result['timestamp']) == pd.Timestamp('2024-01-03')].iloc[0]
        assert row['close'] == 99.0

    def test_get_latest_timestamp(self, db):
        dates = pd.date_range('2024-01-01', periods=5, freq='B')
        df = pd.DataFrame({
            'Open': [10.0] * 5, 'High': [11.0] * 5, 'Low': [9.0] * 5,
            'Close': [10.5] * 5, 'Volume': [1000000] * 5,
            'Dividends': [0.0] * 5, 'Stock Splits': [0.0] * 5,
        }, index=dates)

        db.insert_data('a', 'A_000012.SZ_daily', df)
        latest = db.get_latest_timestamp('a', 'A_000012.SZ_daily')
        assert latest is not None

    def test_get_range_stats(self, db):
        dates = pd.to_datetime(['2024-01-01 09:35', '2024-01-01 09:40', '2024-01-02 09:35'])
        df = pd.DataFrame({
            'Open': [10.0] * 3, 'High': [11.0] * 3, 'Low': [9.0] * 3,
            'Close': [10.5] * 3, 'Volume': [1000000] * 3,
        }, index=dates)
        db.insert_data('a', 'A_RANGE.SZ_5min', df)

        stats = db.get_range_stats('a', 'A_RANGE.SZ_5min', '2024-01-01', '2024-01-01 23:59:59')

        assert stats['rows'] == 2
        assert stats['trading_days'] == 1
        assert stats['first_timestamp']

    def test_empty_insert_returns_zero(self, db):
        df_empty = pd.DataFrame()
        count = db.insert_data('a', 'any_table', df_empty)
        assert count == 0

    def test_drop_table(self, db, temp_db_dir):
        dates = pd.date_range('2024-01-01', periods=2, freq='B')
        df = pd.DataFrame({
            'Open': [10.0] * 2, 'High': [11.0] * 2, 'Low': [9.0] * 2,
            'Close': [10.5] * 2, 'Volume': [1000000] * 2,
            'Dividends': [0.0] * 2, 'Stock Splits': [0.0] * 2,
        }, index=dates)
        db.insert_data('a', 'A_drop_test_daily', df)
        assert db.table_exists('a', 'A_drop_test_daily')
        db.drop_table('a', 'A_drop_test_daily')
        assert not db.table_exists('a', 'A_drop_test_daily')

    def test_unknown_market_raises(self, db):
        with pytest.raises(ValueError, match='未知市场'):
            db.get_connection('jp')


class TestMetadataManager:
    @pytest.fixture
    def db(self, temp_db_dir):
        _setup_local_config(temp_db_dir)
        mgr = DatabaseManager()
        yield mgr
        mgr.close_all()

    def test_upsert_and_get_stock_code(self, db):
        db.upsert_stock_code('test_stock', a_code='000001')
        result = db.get_stock_codes('test_stock')
        assert result == ('000001', None, None)

    def test_upsert_update_existing(self, db):
        db.upsert_stock_code('test_stock', a_code='000001')
        db.upsert_stock_code('test_stock', hk_code='09988')
        result = db.get_stock_codes('test_stock')
        assert result == ('000001', '09988', None)

    def test_get_nonexistent(self, db):
        assert db.get_stock_codes('nonexistent') is None

    def test_get_all_stock_codes(self, db):
        db.upsert_stock_code('a', a_code='1')
        db.upsert_stock_code('b', a_code='2')
        df = db.get_all_stock_codes()
        assert len(df) == 2

    def test_find_stock_name_by_code(self, db):
        db.upsert_stock_code('平安银行', a_code='000001')
        db.upsert_stock_code('腾讯控股', hk_code='00700')
        assert db.find_stock_name_by_code('a', '000001') == '平安银行'
        assert db.find_stock_name_by_code('hk', '00700') == '腾讯控股'
        assert db.find_stock_name_by_code('us', 'AAPL') is None

    def test_delete_stock_code(self, db):
        db.upsert_stock_code('x', a_code='1')
        db.delete_stock_code('x')
        assert db.get_stock_codes('x') is None

    def test_save_and_load_registration(self, db):
        db.save_registration('reg1', {
            'market': 'a', 'stock_code': '000001', 'interval': '5min',
            'table': 'A_000001.SZ_5min',
            'created_at': '2024-01-01', 'active': True,
        })
        registered = db.load_registered_stocks()
        assert 'reg1' in registered
        assert registered['reg1']['market'] == 'a'

    def test_delete_registration(self, db):
        db.save_registration('reg1', {
            'market': 'a', 'stock_code': '000001', 'interval': '5min',
            'table': 'A_000001.SZ_5min',
            'created_at': '2024-01-01', 'active': True,
        })
        db.delete_registration_by_id('reg1')
        assert 'reg1' not in db.load_registered_stocks()

    def test_registration_persists_across_sessions(self, temp_db_dir):
        _setup_local_config(temp_db_dir)
        db1 = DatabaseManager()
        db1.save_registration('reg_persist', {
            'market': 'a', 'stock_code': '000001', 'interval': '5min',
            'table': 'A_000001.SZ_5min',
            'created_at': '2024-01-01', 'active': True,
        })
        db1.close_all()

        db2 = DatabaseManager()
        registered = db2.load_registered_stocks()
        assert 'reg_persist' in registered
        db2.close_all()
