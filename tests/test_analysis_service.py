"""analysis_service.analyze_stock 真实输出结构测试（覆盖 docs/api.md）。

不 mock decision 引擎，而是注入真实 5min OHLCV 数据到 db_manager，让 analysis_service
跑出 resample + 决策的完整链路，验证最终响应字段结构与 api.md 一致。
"""
import numpy as np
import pandas as pd
import pytest


def _make_long_uptrend_5min(n_days: int = 120) -> pd.DataFrame:
    """合成 A 股 n_days 个交易日的单调上涨 5min OHLCV（每天 48 根）。

    日内 48 根 = 上午 24 根（9:30-11:30）+ 下午 24 根（13:00-15:00），不含午休根。
    """
    dates = pd.bdate_range('2024-01-01', periods=n_days)
    am_offsets = pd.timedelta_range(start='9:30:00', periods=24, freq='5min')
    pm_offsets = pd.timedelta_range(start='13:00:00', periods=24, freq='5min')
    idx = []
    for d in dates:
        for off in am_offsets:
            idx.append(d + off)
        for off in pm_offsets:
            idx.append(d + off)
    idx = pd.DatetimeIndex(idx)
    n = len(idx)
    closes = np.linspace(50.0, 150.0, n)
    df = pd.DataFrame({
        'timestamp': idx,
        'open': closes,
        'high': closes * 1.01,
        'low': closes * 0.99,
        'close': closes,
        'volume': [1_000_000] * n,
    })
    return df


def _seed_db(db_manager, market: str, table: str, df: pd.DataFrame):
    db_manager.insert_data(market, table, df)


class TestAnalyzeStockResponseShape:
    @pytest.fixture(autouse=True)
    def setup(self, app, seed_stock_codes):
        from app.models.database import db_manager
        self.db = db_manager
        # 注入 5min 原始数据；analysis_service 会运行时 resample 成目标周期
        df = _make_long_uptrend_5min(n_days=120)
        _seed_db(db_manager, 'a', 'A_000001.SZ_5min', df)

    def test_results_use_nested_decision_signals_standards(self):
        """每条 result 必须严格遵循 api.md 的 decision/signals/standards 嵌套结构。"""
        from app.services.analysis_service import analyze_stock
        resp = analyze_stock('000001', interval='daily')
        assert resp['success'] is True
        assert resp['interval'] == 'integrated'
        assert resp.get('requested_interval') == 'daily'
        assert resp['count'] >= 1
        r = resp['results'][0]

        for k in ('market', 'stock_code', 'display_code', 'timestamp', 'close',
                  'action', 'weight', 'confidence', 'execute_at',
                  'position', 'signals', 'standards', 'view',
                  'next_day_plan', 'trend', 'structure', 'sequence',
                  'decision', 'explanation'):
            assert k in r, f"missing field: {k}"

        assert r['action'] in ('BUY', 'SELL', 'HOLD', 'WAIT')
        assert 0.0 <= r['weight'] <= 1.0
        assert 'state' in r['trend']
        assert 'final_target_position' in r['decision']

        # 决策块
        assert isinstance(r['weight'], (int, float))
        assert r['confidence'] in ('trend', 'core', 'resonance')

        # 仓位对象
        pos = r['position']
        for k in ('current', 'prev', 'label'):
            assert k in pos
        assert pos['label'] in ('满仓', '重仓', '轻仓', '空仓', '冷启动')

        # 信号
        s = r['signals']
        assert s['structure'] in ('none', 'top_75', 'top_100', 'bottom_75', 'bottom_100')
        assert s['sequence'] in ('none', 'high9', 'low9')
        for k in ('structure_active', 'structure_until', 'structure_by_period',
                  'sequence_active', 'sequence_until',
                  'resonance', 'probe'):
            assert k in s
        assert isinstance(s['structure_by_period'], dict)
        assert '60min' in s['structure_by_period']

        # 阈值
        st = r['standards']
        assert 'trend' in st and 'structure' in st
        assert 'structure_by_period' in st
        assert 'structure_reference_period' in st
        for k in ('short_upper', 'short_lower', 'long_upper', 'long_lower'):
            assert k in st['trend']
        for k in ('dif', 'dea', 'cross_price', 'turn_price'):
            assert k in st['structure']
        assert '60min' in st['structure_by_period']

        v = r['view']
        assert v['trend'].get('source') == 'daily'
        assert 'by_period' in v['next_triggers']

    def test_no_legacy_top_level_fields(self):
        """新规范要求顶层不再含旧字段（core_long / position_history 等）。"""
        from app.services.analysis_service import analyze_stock
        resp = analyze_stock('000001', interval='daily')
        r = resp['results'][0]
        legacy = {'core_long', 'core_short', 'resonance_buy', 'resonance_sell',
                  'top_structure_75', 'top_structure_100',
                  'bottom_structure_75', 'bottom_structure_100',
                  'high9_signal', 'low9_signal',
                  'trend_standard', 'structure_standard',
                  'position_history', 'position_label'}
        for k in legacy:
            assert k not in r, f"legacy top-level field leaked: {k}"

    def test_no_data_returns_error_record(self):
        from app.services.analysis_service import analyze_stock
        # 录入新名称但不灌数据
        self.db.upsert_stock_code('腾讯', hk_code='00700')
        resp = analyze_stock('腾讯', interval='daily')
        assert resp['count'] >= 1
        assert any('error' in r for r in resp['results'])

    def test_http_endpoint_returns_nested_decision(self, client):
        """端到端：POST /api/stock/decision 实际跑通 jsonify 链路。"""
        resp = client.post('/api/stock/decision', json={'stock': '000001', 'interval': 'daily'})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        payload = resp.get_json()
        assert payload['success'] is True
        assert payload.get('interval') == 'integrated'
        r = payload['results'][0]
        assert 'signals' in r and 'standards' in r
        assert 'position' in r and isinstance(r['position'], dict)
        assert r['action'] in ('BUY', 'SELL', 'HOLD')
