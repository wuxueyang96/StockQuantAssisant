"""决策引擎（参见 docs/algorithm.md §四）。

三层优先级：
- **趋势**：战略方向与 base_target_position / cap / floor
- **结构**：仅在趋势边界内修边（structure_adjustment）
- **序列**：执行纪律（execution_rules），默认不改变 final_target

`weight` = `order_weight` = |final_target − actual| / 10，clamp [0, 1]。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from app.algos.config import POSITION_LABEL, TrendState
from app.algos.trend import TrendChannel
from app.algos.structure import (
    MACDStructure,
    _structure_enum_from_row,
    aggregate_structure_context,
    compute_structure_adjustment,
    enrich_structure_row_with_trading_validity,
)
from app.algos.sequence import NineSequence
from app.algos.integrated_decision import (
    INTRADAY_STRUCTURE_INTERVALS,
    build_daily_trading_plan,
    collect_structure_by_period,
    merge_trend_structure_sequence,
    plan_to_legacy_summary,
)


class DecisionEngine:
    def __init__(self, trend=None, structure=None, sequence=None):
        self.trend = trend or TrendChannel()
        self.structure = structure or MACDStructure()
        self.sequence = sequence or NineSequence()

    # ------------------------------------------------------------------ evaluate
    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        """单周期 DataFrame 逐日整合（可用于回测列输出）。"""
        trend_r = self.trend.evaluate(df)
        struct_r = self.structure.evaluate(df)
        seq_r = self.sequence.evaluate(df)

        result = df.copy()
        n = len(result)

        for col in ('top_structure_75', 'top_structure_100',
                    'bottom_structure_75', 'bottom_structure_100',
                    'top_structure_active', 'bottom_structure_active',
                    'structure_effective_until'):
            result[col] = struct_r[col]

        for col in ('high9_signal', 'low9_signal', 'high9_active', 'low9_active',
                    'sequence_effective_until'):
            result[col] = seq_r[col]

        result['trend_state'] = trend_r['trend_state']
        result['base_target_position'] = trend_r['base_target_position']
        result['position_cap'] = trend_r['position_cap']
        result['position_floor'] = trend_r['position_floor']
        result['position'] = trend_r['position']
        result['prev_position'] = trend_r['previous_position']
        result['final_target_position'] = np.nan
        result['actual_position'] = np.nan
        result['order_delta'] = np.nan
        result['order_weight'] = 0.0
        result['action'] = 'WAIT'
        result['weight'] = 0.0
        result['confidence'] = 'trend'
        result['confidence_label'] = 'trend'
        result['signal_strength'] = 0.0
        result['structure_adjustment'] = 0
        result['position_label'] = ''
        result['core_long'] = False
        result['core_short'] = False
        result['resonance_buy'] = False
        result['resonance_sell'] = False
        result['probe'] = False

        exec_at = list(result.index[1:]) + [pd.NaT]
        result['execute_at'] = exec_at

        carried_actual: Optional[float] = None

        for i in range(n):
            idx = result.index[i]
            trend_row = trend_r.iloc[i]
            trend_row = trend_row.copy()
            trend_row['high9_active'] = seq_r['high9_active'].iloc[i]
            trend_row['low9_active'] = seq_r['low9_active'].iloc[i]

            as_of = pd.Timestamp(idx)
            struct_row = enrich_structure_row_with_trading_validity(
                struct_r.iloc[i].copy(), 'daily', self.trend.config,
            )
            struct_row.name = idx
            struct_ctx = aggregate_structure_context(
                {'daily': struct_row}, as_of, self.trend.config,
            )
            struct_adj, struct_bias, struct_warnings = compute_structure_adjustment(
                str(trend_row.get('trend_state', TrendState.UNKNOWN.value)),
                struct_ctx,
                self.trend.config,
            )

            from app.algos.sequence import compute_sequence_execution_rules
            seq_rules = compute_sequence_execution_rules(
                trend_state=str(trend_row.get('trend_state')),
                high9_active=bool(trend_row['high9_active']),
                low9_active=bool(trend_row['low9_active']),
                top_structure_active=struct_ctx.get('top_active', False),
                bottom_structure_active=struct_ctx.get('bottom_active', False),
            )

            _, _, _, decision, _, _ = merge_trend_structure_sequence(
                trend_row,
                struct_ctx,
                struct_adj,
                struct_bias.value if hasattr(struct_bias, 'value') else str(struct_bias),
                struct_warnings,
                seq_rules,
                actual_position=carried_actual,
                config=self.trend.config,
            )

            pos_label = POSITION_LABEL.get(
                float(decision.final_target_position), '冷启动',
            ) if decision.final_target_position is not None and pd.notna(
                decision.final_target_position,
            ) else '冷启动'

            result.at[idx, 'final_target_position'] = decision.final_target_position
            result.at[idx, 'actual_position'] = decision.actual_position
            result.at[idx, 'position'] = decision.final_target_position
            result.at[idx, 'prev_position'] = decision.actual_position
            result.at[idx, 'order_delta'] = decision.order_delta
            result.at[idx, 'order_weight'] = decision.order_weight
            result.at[idx, 'weight'] = decision.order_weight
            result.at[idx, 'action'] = decision.action
            result.at[idx, 'confidence_label'] = decision.confidence_label
            result.at[idx, 'confidence'] = (
                'core' if decision.confidence_label == 'structure_edge'
                else 'resonance' if decision.confidence_label == 'resonance'
                else 'trend'
            )
            result.at[idx, 'signal_strength'] = decision.signal_strength
            result.at[idx, 'structure_adjustment'] = struct_adj
            result.at[idx, 'position_label'] = pos_label
            result.at[idx, 'core_long'] = (
                decision.final_target_position is not None
                and pd.notna(decision.final_target_position)
                and float(decision.final_target_position) >= 6
                and struct_ctx.get('bottom_active')
            )
            result.at[idx, 'core_short'] = (
                decision.final_target_position is not None
                and pd.notna(decision.final_target_position)
                and float(decision.final_target_position) <= 4
                and struct_ctx.get('top_active')
            )
            result.at[idx, 'resonance_buy'] = (
                decision.confidence_label == 'resonance' and decision.action == 'BUY'
            )
            result.at[idx, 'resonance_sell'] = (
                decision.confidence_label == 'resonance' and decision.action == 'SELL'
            )

            if decision.final_target_position is not None and pd.notna(decision.final_target_position):
                carried_actual = float(decision.final_target_position)

        return result

    # ------------------------------------------------------------------ view
    _POSITION_TO_TREND_LABEL = {
        10.0: '上升',
        8.0: '上升',
        6.0: '横盘',
        4.0: '横盘',
        0.0: '下降',
    }

    _TREND_STATE_LABEL = {
        TrendState.UP_STRONG.value: '上升',
        TrendState.UP_PULLBACK.value: '上升',
        TrendState.UP_WEAK.value: '上升',
        TrendState.RANGE.value: '横盘',
        TrendState.DOWN_REBOUND.value: '下降',
        TrendState.DOWN_STRONG.value: '下降',
    }

    def _extrapolate_next(self, series: pd.Series, window: int = 5):
        s = series.dropna()
        if len(s) < 2:
            return None
        n = min(window, len(s))
        recent = s.iloc[-n:]
        if len(recent) < 2:
            return float(recent.iloc[-1])
        slope = (recent.iloc[-1] - recent.iloc[0]) / (len(recent) - 1)
        return round(float(recent.iloc[-1] + slope), 4)

    def _make_view(self, df_daily: pd.DataFrame, summary_dict: dict,
                   plan_explanation: list) -> dict:
        pos = summary_dict.get('position', {}).get('current')
        position_label = summary_dict.get('position', {}).get('label')
        close = summary_dict.get('close')
        trend_std = summary_dict.get('standards', {}).get('trend', {}) or {}
        struct_std = summary_dict.get('standards', {}).get('structure', {}) or {}
        ndp = summary_dict.get('next_day_plan') or {}
        trend_state = (ndp.get('trend') or {}).get('state')
        trend_label = self._TREND_STATE_LABEL.get(
            trend_state,
            self._POSITION_TO_TREND_LABEL.get(float(pos), '冷启动') if pos is not None else '冷启动',
        )

        channels = self.trend.compute_all(df_daily)
        tomorrow_up = self._extrapolate_next(channels['short_upper'])
        tomorrow_dn = self._extrapolate_next(channels['short_lower'])

        seq_df = self.sequence.evaluate(df_daily)
        h9_count = max(0, min(9, int(seq_df['high9_count'].iloc[-1])))
        l9_count = max(0, min(9, int(seq_df['low9_count'].iloc[-1])))

        by_period = (summary_dict.get('view') or {}).get('next_triggers', {}).get('by_period', {})
        if not by_period:
            by_period = {}
            for itv, st in (summary_dict.get('standards', {}).get('structure_by_period') or {}).items():
                by_period[itv] = {
                    'macd_75_at_close': st.get('turn_price'),
                    'macd_100_at_close': st.get('cross_price'),
                }

        rationale = plan_explanation[0] if plan_explanation else summary_dict.get('explanation', [''])[0]

        return {
            'trend': {
                'label': trend_label,
                'position_label': position_label,
                'source': 'daily',
                'today_break_up': trend_std.get('short_upper'),
                'today_break_down': trend_std.get('short_lower'),
                'tomorrow_break_up': tomorrow_up,
                'tomorrow_break_down': tomorrow_dn,
            },
            'next_triggers': {
                'macd_75_at_close': struct_std.get('turn_price'),
                'macd_100_at_close': struct_std.get('cross_price'),
                'structure_reference_period': summary_dict.get('standards', {}).get(
                    'structure_reference_period',
                ),
                'by_period': by_period,
                'high9_progress': f'{h9_count}/9',
                'low9_progress': f'{l9_count}/9',
                'sequence_source': 'daily',
            },
            'rationale': rationale if isinstance(rationale, str) else '；'.join(plan_explanation),
        }

    # ------------------------------------------------------------------ summary_integrated
    def summary_integrated(
        self,
        df_daily: pd.DataFrame,
        intraday: dict[str, pd.DataFrame],
    ) -> dict:
        """多周期整合快照：趋势(日线) + 结构(60/90/120) + 序列(日线)。"""
        if len(df_daily) == 0:
            return {'standards': {'trend': {}, 'structure': {}, 'structure_by_period': {}}}

        as_of = pd.Timestamp(df_daily.index[-1])
        struct_rows, display_by_period = collect_structure_by_period(
            intraday, self.structure, as_of, self.trend.config,
        )

        # 标准阈值
        trend_thresholds = self.trend.next_day_thresholds(df_daily)
        ch = self.trend.compute_all(df_daily)
        last_ch = ch.iloc[-1]
        prev_ch = ch.iloc[-2] if len(ch) > 1 else last_ch
        trend_thresholds['short_upper_prev'] = round(float(prev_ch['short_upper']), 4)
        trend_thresholds['short_lower_prev'] = round(float(prev_ch['short_lower']), 4)
        trend_thresholds['long_upper_prev'] = round(float(prev_ch['long_upper']), 4)
        trend_thresholds['long_lower_prev'] = round(float(prev_ch['long_lower']), 4)

        # 竞争胜出周期（120 > 90 > 60）
        best_period: Optional[str] = None
        best_score = -1
        best_pri = -1
        from app.algos.config import STRUCTURE_PERIOD_PRIORITY, _STRUCTURE_ENUM_SCORE
        for itv in INTRADAY_STRUCTURE_INTERVALS:
            r = struct_rows.get(itv)
            if r is None:
                continue
            en = _structure_enum_from_row(r)
            if en == 'none':
                continue
            sc = _STRUCTURE_ENUM_SCORE[en]
            pri = STRUCTURE_PERIOD_PRIORITY.get(itv, 0)
            if sc > best_score or (sc == best_score and pri > best_pri):
                best_score, best_pri, best_period = sc, pri, itv

        standards_by_period = {}
        for itv in INTRADAY_STRUCTURE_INTERVALS:
            df_i = intraday.get(itv)
            if df_i is None or len(df_i) < 30:
                continue
            th = self.structure.next_period_thresholds(df_i)
            standards_by_period[itv] = {
                'dif': th.get('dif'),
                'dea': th.get('dea'),
                'cross_price': th.get('macd_dif_cross_dea_price'),
                'turn_price': th.get('macd_dif_turn_price'),
            }

        ref_itv = best_period
        if ref_itv is None:
            for itv in INTRADAY_STRUCTURE_INTERVALS:
                if intraday.get(itv) is not None and len(intraday[itv]) >= 30:
                    ref_itv = itv
                    break

        if ref_itv and intraday.get(ref_itv) is not None:
            th0 = self.structure.next_period_thresholds(intraday[ref_itv])
            standards_structure = {
                'dif': th0.get('dif'),
                'dea': th0.get('dea'),
                'cross_price': th0.get('macd_dif_cross_dea_price'),
                'turn_price': th0.get('macd_dif_turn_price'),
            }
        else:
            standards_structure = {
                'dif': None, 'dea': None, 'cross_price': None, 'turn_price': None,
            }

        # 用上一日 final 作为 actual 代理：先跑两日计划取 carried
        carried = None
        if len(df_daily) >= 2:
            sub = df_daily.iloc[:-1]
            sub_intra = {k: v.loc[v.index <= sub.index[-1]] for k, v in intraday.items()
                         if v is not None and len(v) >= 30}
            try:
                prev_plan = build_daily_trading_plan(
                    sub, sub_intra,
                    config=self.trend.config,
                    trend=self.trend,
                    structure=self.structure,
                    sequence=self.sequence,
                )
                fp = prev_plan.decision.final_target_position
                if fp is not None and pd.notna(fp):
                    carried = float(fp)
            except ValueError:
                pass

        plan = build_daily_trading_plan(
            df_daily,
            intraday,
            actual_position=carried,
            config=self.trend.config,
            trend=self.trend,
            structure=self.structure,
            sequence=self.sequence,
        )

        result = plan_to_legacy_summary(
            plan,
            display_by_period=display_by_period,
            standards_trend=trend_thresholds,
            standards_structure=standards_structure,
            standards_by_period=standards_by_period,
            structure_reference_period=ref_itv,
            timestamp=pd.Timestamp(df_daily.index[-1]).isoformat(),
        )

        result['view'] = self._make_view(df_daily, result, plan.explanation)
        return result

    # ------------------------------------------------------------------ summary
    def summary(self, df: pd.DataFrame) -> dict:
        """单周期最新快照（与 api.md 对齐）。"""
        if len(df) == 0:
            return {'standards': {'trend': {}, 'structure': {}}}

        plan = build_daily_trading_plan(
            df,
            {},
            config=self.trend.config,
            trend=self.trend,
            structure=self.structure,
            sequence=self.sequence,
        )
        trend_thresholds = self.trend.next_day_thresholds(df)
        struct_thresholds = self.structure.next_period_thresholds(df)
        standards_structure = {
            'dif': struct_thresholds.get('dif'),
            'dea': struct_thresholds.get('dea'),
            'cross_price': struct_thresholds.get('macd_dif_cross_dea_price'),
            'turn_price': struct_thresholds.get('macd_dif_turn_price'),
        }

        result = plan_to_legacy_summary(
            plan,
            standards_trend=trend_thresholds,
            standards_structure=standards_structure,
            timestamp=pd.Timestamp(df.index[-1]).isoformat(),
        )
        result['view'] = self._make_view(df, result, plan.explanation)
        return result
