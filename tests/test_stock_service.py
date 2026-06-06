import pytest
import pandas as pd
from app.services.stock_service import (
    detect_market, resolve_stock_name, format_stock_code,
    get_workflow_id, get_table_name, get_yfinance_ticker, is_trading_time,
)


class TestDetectMarket:
    def test_a_stock_with_sz_suffix(self):
        assert detect_market('000001.SZ') == [('a', '000001')]

    def test_a_stock_with_ss_suffix(self):
        assert detect_market('600519.SS') == [('a', '600519')]

    def test_hk_stock_with_hk_suffix(self):
        assert detect_market('00700.HK') == [('hk', '00700')]

    def test_hk_stock_with_hk_suffix_4digit(self):
        assert detect_market('0005.HK') == [('hk', '00005')]

    def test_us_stock_with_us_suffix(self):
        assert detect_market('AAPL.US') == [('us', 'AAPL')]

    def test_a_stock_6digit_code(self):
        assert detect_market('000001') == [('a', '000001')]

    def test_a_stock_shanghai_6digit(self):
        assert detect_market('600519') == [('a', '600519')]

    def test_hk_stock_5digit_code(self):
        assert detect_market('00700') == [('hk', '00700')]

    def test_us_stock_pure_letters(self):
        assert detect_market('AAPL') == [('us', 'AAPL')]

    def test_case_insensitive(self):
        assert detect_market('aapl') == [('us', 'AAPL')]

    def test_invalid_code_raises(self):
        with pytest.raises(ValueError):
            detect_market('ABC.XX')

    def test_unknown_name_not_in_db(self, app):
        with pytest.raises(ValueError, match='stock_codes'):
            detect_market('不存在的股票')


class TestResolveStockName:
    def test_resolve_multi_market(self, app, seed_stock_codes):
        result = resolve_stock_name('阿里巴巴')
        assert len(result) == 2
        assert ('hk', '09988') in result
        assert ('us', 'BABA') in result

    def test_resolve_single_market(self, app, seed_stock_codes):
        result = resolve_stock_name('贵州茅台')
        assert result == [('a', '600519')]

    def test_resolve_hk_only(self, app, seed_stock_codes):
        result = resolve_stock_name('小米')
        assert result == [('hk', '01810')]

    def test_resolve_us_only(self, app, seed_stock_codes):
        result = resolve_stock_name('苹果')
        assert result == [('us', 'AAPL')]

    def test_resolve_unknown_name(self, app):
        with pytest.raises(ValueError, match='stock_codes'):
            resolve_stock_name('完全不存在的股票名')


class TestFormatStockCode:
    def test_a_stock_shanghai(self):
        assert format_stock_code('a', '600519') == '600519.SS'

    def test_a_stock_shenzhen(self):
        assert format_stock_code('a', '000001') == '000001.SZ'

    def test_hk_stock(self):
        assert format_stock_code('hk', '00700') == '00700.HK'

    def test_us_stock(self):
        assert format_stock_code('us', 'AAPL') == 'AAPL.US'


class TestGetWorkflowId:
    def test_a_stock_daily(self):
        assert get_workflow_id('a', '000001', 'daily') == 'A_000001.SZ_daily'

    def test_hk_stock_120min(self):
        assert get_workflow_id('hk', '00700', '120min') == 'HK_00700.HK_120min'

    def test_us_stock_60min(self):
        assert get_workflow_id('us', 'AAPL', '60min') == 'US_AAPL.US_60min'

    def test_table_name_matches_workflow_id(self):
        for interval in ['daily', '120min', '90min', '60min']:
            wf_id = get_workflow_id('a', '000001', interval)
            tbl = get_table_name('a', '000001', interval)
            assert tbl == wf_id


class TestGetYfinanceTicker:
    def test_a_stock_shanghai(self):
        assert get_yfinance_ticker('a', '600519') == '600519.SS'

    def test_a_stock_shenzhen(self):
        assert get_yfinance_ticker('a', '000001') == '000001.SZ'

    def test_hk_stock(self):
        assert get_yfinance_ticker('hk', '00700') == '0700.HK'

    def test_us_stock(self):
        assert get_yfinance_ticker('us', 'AAPL') == 'AAPL'


class TestITickSource:
    def test_itick_region_code_mapping(self):
        from app.services.stock_service import get_itick_region_code

        assert get_itick_region_code('a', '600519') == ('SH', '600519')
        assert get_itick_region_code('a', '300274') == ('SZ', '300274')
        assert get_itick_region_code('hk', '00700') == ('HK', '700')
        assert get_itick_region_code('us', 'aapl') == ('US', 'AAPL')

    def test_normalize_itick_kline(self):
        from app.services.stock_service import _normalize_itick_kline

        preopen_ts = int(pd.Timestamp('2024-01-02T01:25:00Z').timestamp() * 1000)
        ts = int(pd.Timestamp('2024-01-02T01:35:00Z').timestamp() * 1000)
        df = _normalize_itick_kline([{
            't': preopen_ts,
            'o': 9,
            'h': 9,
            'l': 9,
            'c': 9,
            'v': 10,
        }, {
            't': ts,
            'o': 10,
            'h': 11,
            'l': 9,
            'c': 10.5,
            'v': 1000,
        }], 'a')

        assert len(df) == 1
        assert df.index[0].tz is not None
        assert df.index[0].tz_convert('Asia/Shanghai').hour == 9
        assert df.iloc[0]['Open'] == 10
        assert df.iloc[0]['Close'] == 10.5

    def test_fetch_stock_data_prefers_itick_when_token_is_configured(self, mocker):
        from app.services import stock_service

        df = pd.DataFrame({
            'Open': [1.0],
            'High': [1.1],
            'Low': [0.9],
            'Close': [1.0],
            'Volume': [100],
        }, index=pd.DatetimeIndex([pd.Timestamp('2024-01-02 09:35', tz='Asia/Shanghai')]))
        mock_fetch = mocker.patch.object(stock_service, 'fetch_5m_from_sources', return_value=df)
        mocker.patch.object(stock_service.Config, 'DATA_SOURCE', 'itick')
        mocker.patch.object(stock_service.Config, 'ITICK_TOKEN', 'token')

        result = stock_service.fetch_stock_data('a', '300274', history_days=7)

        assert result is df
        mock_fetch.assert_called_once()
        request = mock_fetch.call_args.args[0]
        assert request.market == 'a'
        assert request.stock_code == '300274'
        assert request.interval == '5min'
        assert request.history_days == 7

    def test_fetch_5m_itick_uses_kline_pages(self, mocker):
        from app.services import stock_service

        t1 = int(pd.Timestamp('2024-01-02T01:35:00Z').timestamp() * 1000)
        t2 = int(pd.Timestamp('2024-01-02T01:40:00Z').timestamp() * 1000)
        mocker.patch.object(stock_service.Config, 'ITICK_TOKEN', 'token')
        mocker.patch.object(stock_service.Config, 'ITICK_PAGE_LIMIT', 1000)
        mocker.patch.object(stock_service.Config, 'ITICK_MAX_PAGES', 2)
        mock_request = mocker.patch.object(stock_service, '_itick_request', return_value={
            'code': 0,
            'data': [
                {'t': t2, 'o': 10.2, 'h': 10.5, 'l': 10.1, 'c': 10.3, 'v': 200},
                {'t': t1, 'o': 10.0, 'h': 10.4, 'l': 9.9, 'c': 10.2, 'v': 100},
            ],
        })

        df = stock_service._fetch_5m_itick(
            'a',
            '300274',
            start_date='2024-01-02 09:35:00',
            end_date='2024-01-02 09:45:00',
        )

        assert len(df) == 2
        assert list(df['Close']) == [10.2, 10.3]
        mock_request.assert_called_once()
        assert mock_request.call_args.args[:2] == ('SZ', '300274')

    def test_fetch_5m_itick_keeps_partial_page_when_rate_limited(self, mocker):
        from app.services import stock_service

        t1 = int(pd.Timestamp('2024-01-02T01:35:00Z').timestamp() * 1000)
        t2 = int(pd.Timestamp('2024-01-02T01:40:00Z').timestamp() * 1000)
        mocker.patch.object(stock_service.Config, 'ITICK_TOKEN', 'token')
        mocker.patch.object(stock_service.Config, 'ITICK_PAGE_LIMIT', 1000)
        mocker.patch.object(stock_service.Config, 'ITICK_MAX_PAGES', 2)
        mocker.patch.object(stock_service.Config, 'ITICK_PAGE_DELAY_SECONDS', 0)
        mock_request = mocker.patch.object(stock_service, '_itick_request', side_effect=[
            {
                'code': 0,
                'data': [
                    {'t': t2, 'o': 10.2, 'h': 10.5, 'l': 10.1, 'c': 10.3, 'v': 200},
                    {'t': t1, 'o': 10.0, 'h': 10.4, 'l': 9.9, 'c': 10.2, 'v': 100},
                ],
            },
            stock_service.ITickRateLimitError('limited'),
        ])

        df = stock_service._fetch_5m_itick(
            'a',
            '300274',
            start_date='2024-01-01 09:30:00',
            end_date='2024-01-02 09:45:00',
        )

        assert len(df) == 2
        assert list(df['Close']) == [10.2, 10.3]
        assert mock_request.call_count == 2

    def test_itick_api_usage_estimate_for_free_mode(self, mocker):
        from app.services import stock_service

        mocker.patch.object(stock_service.Config, 'ITICK_FREE_MODE', True)
        mocker.patch.object(stock_service.Config, 'ITICK_FREE_MIN_INTERVAL_SECONDS', 13)
        mocker.patch.object(stock_service.Config, 'ITICK_PAGE_LIMIT', 1000)

        estimate = stock_service.estimate_itick_api_usage('a', trading_days=200)

        assert estimate['request_count'] == 10
        assert estimate['bars_per_trading_day'] == 48
        assert estimate['estimated_seconds'] == 117

    def test_fetch_5m_itick_allows_one_page_limit_override(self, mocker):
        from app.services import stock_service

        t1 = int(pd.Timestamp('2024-01-02T01:35:00Z').timestamp() * 1000)
        mocker.patch.object(stock_service.Config, 'ITICK_TOKEN', 'token')
        mocker.patch.object(stock_service.Config, 'ITICK_FREE_MODE', False)
        mock_request = mocker.patch.object(stock_service, '_itick_request', return_value={
            'code': 0,
            'data': [
                {'t': t1, 'o': 10.0, 'h': 10.4, 'l': 9.9, 'c': 10.2, 'v': 100},
            ],
        })

        stock_service._fetch_5m_itick(
            'a',
            '300274',
            start_date='2024-01-02 09:30:00',
            end_date='2024-01-02 15:00:00',
            max_pages=1,
            limit=1000,
        )

        assert mock_request.call_count == 1
        assert mock_request.call_args.kwargs['limit'] == 1000


class TestInitialHistoryWindow:
    def test_config_uses_200_days_for_5min(self):
        from app.config import Config

        assert Config.INITIAL_5MIN_HISTORY_DAYS == 200
        assert Config.INTERVAL_MAP['5min']['period'] == '200d'

    def test_yfinance_uses_configured_period(self, mocker):
        from app.services import stock_service

        mock_ticker = mocker.Mock()
        mock_ticker.history.return_value = mocker.Mock(empty=True)
        ticker_cls = mocker.patch.object(stock_service.yf, 'Ticker', return_value=mock_ticker)

        stock_service._fetch_5m_yfinance('us', 'AAPL')

        ticker_cls.assert_called_once_with('AAPL')
        mock_ticker.history.assert_called_once_with(period='200d', interval='5m')


class TestIsTradingTime:
    def test_returns_bool(self):
        assert isinstance(is_trading_time('a'), bool)

    def test_unknown_market(self):
        assert is_trading_time('jp') is False


class TestDetectMarketMultiMarket:
    def test_detect_name_multi_market(self, app, seed_stock_codes):
        results = detect_market('阿里巴巴')
        assert len(results) == 2

    def test_detect_name_single_market(self, app, seed_stock_codes):
        results = detect_market('贵州茅台')
        assert len(results) == 1
