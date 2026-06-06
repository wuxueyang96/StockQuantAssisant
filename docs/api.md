# API 文档

API Base URL: `/api`，所有 API 响应为 JSON，包含 `success` 字段。内置前端控制台位于根路径 `/`。

## 目录

- [前端控制台](#前端控制台)
- [健康检查](#健康检查)
- [股票代码映射](#股票代码映射)
- [工作流注册](#工作流注册)
- [数据维护](#数据维护)
- [量化决策](#量化决策)
- [行情图表](#行情图表)
- [回测](#回测)
- [工作流查询](#工作流查询)

---

## 前端控制台

### GET /

返回内置单页控制台 HTML。页面静态资源由 Flask `static` 目录提供，无需单独构建前端。

主要能力：

- 输入股票代码或名称并调用 `POST /api/stock/decision` 获取三层次日计划
- 调用 `POST /api/stock/register` 注册 5min 数据工作流
- 展示 `next_day_plan`、趋势/结构/序列分层信息、执行纪律、WebUI 图表、回测结果和原始 JSON

**响应** `200` — `text/html`

---

## 健康检查

### GET /health

**响应** `200`
```json
{ "status": "ok", "scheduler_running": true }
```

---

## 股票代码映射

### POST /stock/code

录入或更新股票名称与各市场代码的映射。已存在的名称只更新提供的字段。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 股票中文名称 |
| `a` | string | 否 | A 股代码（6 位纯数字） |
| `hk` | string | 否 | 港股代码（4-5 位纯数字） |
| `us` | string | 否 | 美股代码 |

至少提供 `a`、`hk`、`us` 中的一个。

**响应** `200`
```json
{ "success": true, "message": "股票映射 \"阿里巴巴\" 已保存" }
```

**响应** `400`
```json
{ "success": false, "message": "缺少 name 参数" }
```

### GET /stock/codes

**响应** `200`
```json
{
  "success": true,
  "count": 3,
  "codes": [
    { "name": "阿里巴巴", "a_code": null, "hk_code": "09988", "us_code": "BABA" },
    { "name": "贵州茅台", "a_code": "600519", "hk_code": null, "us_code": null },
    { "name": "苹果", "a_code": null, "hk_code": null, "us_code": "AAPL" }
  ]
}
```

---

## 工作流注册

### POST /stock/register

根据股票代码或名称注册数据同步工作流。系统自动识别输入类型：

- **代码**：按格式匹配市场（如 `000001.SZ` → A 股，`00700.HK` → 港股，`AAPL` → 美股）
- **名称**：从 `stock_codes` 表反查，匹配多个市场时一次性注册所有市场

每个市场注册 **1 个 5min 工作流**。高粒度 K 线由 `resample` 运行时合成。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stock` | string | 是 | 股票代码或名称 |

**响应** `200` — 新创建（单市场）
```json
{
  "success": true,
  "message": "工作流已创建",
  "workflows": ["A_000001.SZ_5min"],
  "markets": [{ "market": "a", "stock_code": "000001" }]
}
```

**响应** `200` — 已存在：`"message": "工作流已存在"`，其余同新创建。

**响应** `400` — 名称未录入：提示先调用 `POST /api/stock/code` 录入映射。

---

## 数据维护

### GET /stock/data-status

查看本地 5min Parquet 数据状态。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stock` | string | 是 | 股票代码或名称 |

**响应** `200`
```json
{
  "success": true,
  "input": "300274",
  "count": 1,
  "results": [{
    "display_code": "300274.SZ",
    "registered": true,
    "table": "A_300274.SZ_5min",
    "rows": 1488,
    "daily_bars": 31,
    "first_timestamp": "2026-04-21T09:35:00+08:00",
    "last_timestamp": "2026-06-05T14:55:00+08:00",
    "data_source": "itick",
    "itick_free_mode": true,
    "refresh_api_budget": {
      "request_count": 1,
      "estimated_seconds": 0
    }
  }]
}
```

### POST /stock/refresh

主动刷新已注册股票的最新 5min 数据。当前主数据源为 iTick 且 `ITICK_FREE_MODE=true` 时固定只请求最新一页，并通过本地 `latest_timestamp` 过滤新增行；关闭 free mode 后才使用 `history_days` 窗口刷新。该接口通过统一数据源接口拉取数据，未来切换主数据源时 API 语义不变。

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `stock` | string | 是 | — | 股票代码或名称 |
| `history_days` | int | 否 | 7 | 本次刷新向前请求的数据窗口 |

### POST /refresh

强制刷新所有已录入代码映射且已经注册 5min 工作流的股票。该接口来自 PR #2 的全量刷新能力，当前实现复用 `data_service.refresh_market`，因此会遵守统一数据源接口、free mode、数据状态和 API 预算统计。

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `history_days` | int | 否 | 7 | 关闭 free mode 后的刷新窗口；free mode 下仍只请求最新一页 |

**响应** `200`
```json
{
  "success": true,
  "total_stocks": 3,
  "total_queries": 3,
  "total_rows_inserted": 42,
  "errors": 0,
  "api_budget": {
    "data_source": "itick",
    "free_mode": true,
    "request_count": 3,
    "estimated_seconds": 26
  },
  "results": []
}
```

### GET /stock/backfill-estimate

估算补历史会消耗的数据源 API 请求次数，不会请求外部数据源。iTick 会返回分页、free mode 限速和预计秒数；其他数据源返回统一的预算结构。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `stock` | string | 是 | — | 股票代码或名称 |
| `days` | int | 否 | 200 | 向前补的交易日数量 |

**响应** `200`
```json
{
  "success": true,
  "input": "300274",
  "count": 1,
  "api_budget": {
    "free_mode": true,
    "request_count": 10,
    "min_interval_seconds": 13,
    "estimated_seconds": 117
  }
}
```

### POST /stock/backfill

为已注册股票补历史 5min 数据。该接口使用 upsert 合并，不受 `insert_data` 只写入 `latest` 之后数据的限制。

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `stock` | string | 是 | — | 股票代码或名称 |
| `days` | int | 否 | 200 | 向前补的交易日数量；系统会换算为更大的自然日请求窗口并按实际交易日截断 |
| `start_date` | string | 否 | — | 指定历史开始日期 |
| `end_date` | string | 否 | — | 指定历史结束日期 |
| `queued` | bool | 否 | false | 为 true 时创建后台数据任务并立即返回 `job_id` |

返回字段包含 `requested_trading_days`、`request_calendar_days`、`inserted_rows`、`updated_rows`、`rows_before`、`rows_after`、`source_first_timestamp`、`source_last_timestamp`、`source_trading_days` 和 `partial`。

### GET /data-jobs/{job_id}

查询后台数据任务状态。WebUI 在数据源 free mode 下用它轮询补历史任务。

**响应** `200`
```json
{
  "success": true,
  "job": {
    "id": "1f2e...",
    "type": "backfill",
    "status": "running",
    "progress": 10,
    "estimate": {
      "api_budget": {
        "request_count": 10,
        "estimated_seconds": 117
      }
    }
  }
}
```

---

## 量化决策

### POST /stock/decision

返回最新日频三层次日交易计划。决策语义以 [algorithm.md §四](algorithm.md) 为权威源。

响应分四块：**决策**（`action` / `weight` / `confidence` / `execute_at`）→ **信号**（`signals`）→ **阈值**（`standards`）→ **人话视图**（`view`）。另附 **三层计划**（`next_day_plan` 或分拆的 `trend` / `structure` / `sequence` / `decision` / `explanation`）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stock` | string | 是 | 股票代码或名称 |
| `interval` | string | 否 | **仅回显**至 `requested_interval`，不参与计算。算法固定为 `integrated`（日线趋势 + 60/90/120 结构 + 日线序列） |

**响应** `200`
```json
{
  "success": true,
  "input": "000001",
  "interval": "integrated",
  "requested_interval": "daily",
  "count": 1,
  "results": [{
    "market": "a",
    "stock_code": "000001",
    "display_code": "000001.SZ",
    "timestamp": "2026-04-24T00:00:00+08:00",
    "close": 10.41,
    "action": "SELL",
    "weight": 0.4,
    "confidence": "trend",
    "execute_at": "2026-04-27T00:00:00+08:00",
    "position": { "current": 6.0, "prev": 10.0, "label": "重仓" },
    "signals": {
      "structure": "none",
      "structure_active": false,
      "structure_until": null,
      "structure_by_period": {
        "60min":  { "structure": "none", "structure_active": false, "structure_until": null, "partial_bar": false },
        "90min":  { "structure": "none", "structure_active": false, "structure_until": null, "partial_bar": true },
        "120min": { "structure": "none", "structure_active": false, "structure_until": null, "partial_bar": false }
      },
      "sequence": "none",
      "sequence_active": false,
      "sequence_until": null,
      "resonance": null,
      "probe": false
    },
    "standards": {
      "trend":     { "short_upper": 10.85, "short_lower": 10.12, "long_upper": 10.62, "long_lower": 9.88 },
      "structure": { "dif": 0.15, "dea": 0.12, "cross_price": 10.35, "turn_price": 10.28 },
      "structure_reference_period": "60min",
      "structure_by_period": {
        "60min":  { "dif": 0.15, "dea": 0.12, "cross_price": 10.35, "turn_price": 10.28 },
        "90min":  { "dif": 0.14, "dea": 0.11, "cross_price": 10.30, "turn_price": 10.25 },
        "120min": { "dif": 0.13, "dea": 0.10, "cross_price": 10.28, "turn_price": 10.22 }
      }
    },
    "view": {
      "trend": {
        "label": "上升",
        "position_label": "重仓",
        "source": "daily",
        "today_break_up": 10.85,
        "today_break_down": 10.12,
        "tomorrow_break_up": 10.90,
        "tomorrow_break_down": 10.08
      },
      "next_triggers": {
        "macd_75_at_close": 10.28,
        "macd_100_at_close": 10.35,
        "structure_reference_period": "60min",
        "by_period": {
          "60min":  { "macd_75_at_close": 10.28, "macd_100_at_close": 10.35 },
          "90min":  { "macd_75_at_close": 10.25, "macd_100_at_close": 10.30 },
          "120min": { "macd_75_at_close": 10.22, "macd_100_at_close": 10.28 }
        },
        "high9_progress": "0/9",
        "low9_progress": "0/9",
        "sequence_source": "daily"
      },
      "rationale": "收盘 10.41 在短轨 [10.12, 10.85] 区间内；趋势判定 上升（重仓）；本根触发 SELL（trend 置信度）。"
    },
    "next_day_plan": {
      "date": "2026-04-24",
      "symbol": "000001.SZ",
      "close": 10.41,
      "execute_date": "2026-04-27",
      "execute_at": "2026-04-27T00:00:00+08:00",
      "trend": {
        "state": "UP_PULLBACK",
        "base_target_position": 6.0,
        "position_cap": 8.0,
        "position_floor": 4.0,
        "previous_position": 10.0
      },
      "structure": {
        "adjustment": 0,
        "bias": "NEUTRAL",
        "active_periods": [],
        "strongest_event": "none",
        "highest_timeframe_event": "none",
        "resonance_count": 0,
        "resonance_weight": 0.0,
        "warnings": []
      },
      "sequence": {
        "high9_active": false,
        "low9_active": false,
        "probe": false,
        "near_historical_extreme": false,
        "execution_rules": []
      },
      "decision": {
        "actual_position": 10.0,
        "final_target_position": 6.0,
        "order_delta": -4.0,
        "order_weight": 0.4,
        "action": "SELL",
        "signal_strength": 0.4,
        "confidence_label": "trend",
        "principle": "次日按目标仓位减仓。",
        "forbidden_actions": [],
        "no_trade_condition": "",
        "invalidation": "若收盘重新站上短上轨且长趋势转强，减仓节奏可放缓。"
      },
      "explanation": ["日线趋势：上升趋势回调，战略仓位 6.0。"]
    },
    "trend": { "...": "同 next_day_plan.trend" },
    "structure": { "...": "同 next_day_plan.structure" },
    "sequence": { "...": "同 next_day_plan.sequence" },
    "decision": { "...": "同 next_day_plan.decision" },
    "explanation": ["日线趋势：上升趋势回调，战略仓位 6.0。"]
  }]
}
```

多市场时（如 "阿里巴巴" 港股+美股），`count` 为市场数量，`results` 数组每项对应一个市场。部分市场无数据时，对应项含 `error` 字段说明原因。

### 字段说明

**决策块**（下单链路只需消费这 3 个字段）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `action` | enum | `BUY` / `SELL` / `HOLD` / `WAIT`，由 `final_target` 与 `actual_position` 差值推导 |
| `weight` | float | **真实下单比例** `order_weight` = clamp(\|final−actual\|/10, 0, 1)；**不含**结构/序列倍率放大 |
| `confidence` | enum | `trend` / `core` / `resonance`（映射自 `decision.confidence_label`） |
| `execute_at` | string/null | 可执行日期/时点，默认下一工作日可执行时段；具体开盘撮合由执行系统处理 |
| `position.current` | float/null | **次日目标仓位** `final_target_position` |
| `position.prev` | float/null | **当前仓位代理** `actual_position`（默认=上一日 final_target） |
| `next_day_plan` | object | 完整 `DailyTradingPlan`（含 principle、forbidden_actions、invalidation） |
| `trend` / `structure` / `sequence` / `decision` | object | 与 `next_day_plan` 内同名块一致，便于前端分拆消费 |
| `explanation` | string[] | 每日决策解释条目 |
| `position.label` | string | 中文：满仓 / 重仓 / 轻仓 / 空仓 / 冷启动 |

**信号块 `signals`**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `structure` | enum | 三周期合并主视图：`none` / `top_75` / `top_100` / `bottom_75` / `bottom_100` |
| `structure_active` | bool | 任一 60/90/120min 结构有效即为 true |
| `structure_until` | string/null | 各 active 周期 effective_until 的最大值 |
| `structure_by_period` | object | 各周期 `{structure, structure_active, structure_until, partial_bar}`；90min partial 默认仅展示、不参与交易确认 |
| `sequence` | enum | 日线九转事件：`none` / `high9` / `low9` |
| `sequence_active` | bool | 序列有效状态（H=5 根） |
| `sequence_until` | string/null | 序列有效期截止时间戳 |
| `resonance` | object/null | 60/90/120min 共振：`{level: 1.0|1.5|2.0, periods: [...]}`；无共振为 null |
| `probe` | bool | 左侧试探预警（序列形成但结构未成），仅参考 |

**阈值块 `standards`**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `trend.short_upper` / `short_lower` | float | 短期通道上/下轨 |
| `trend.long_upper` / `long_lower` | float | 长期通道上/下轨 |
| `structure.dif` / `dea` | float | 参考周期下一根 DIF/DEA |
| `structure.cross_price` | float | 收盘触发 DIF/DEA 交叉的价位（结构 100%） |
| `structure.turn_price` | float | 收盘触发 DIF 拐头的价位（结构 75%） |
| `structure_reference_period` | string | 上述四价所属周期（60/90/120min） |
| `structure_by_period` | object | 各周期完整阈值 |

**人话视图 `view`**（从 standards / signals 派生）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `view.trend.label` | enum | `上升` / `下降` / `横盘` / `冷启动`（日线） |
| `view.trend.position_label` | string | 仓位中文 |
| `view.trend.today_break_up` / `today_break_down` | float | 下一根日线突破/破位价 |
| `view.trend.tomorrow_break_up` / `tomorrow_break_down` | float/null | 再下一根日线外推价格 |
| `view.next_triggers.macd_75_at_close` | float/null | 下一根参考周期 K 触发 75% 的收盘价 |
| `view.next_triggers.macd_100_at_close` | float/null | 同上，100% 交叉价 |
| `view.next_triggers.structure_reference_period` | string | 参考周期 |
| `view.next_triggers.by_period` | object | 三周期分别触发价位 |
| `view.next_triggers.high9_progress` / `low9_progress` | string | 日线九转进度 `"N/9"` |
| `view.rationale` | string | 一句话态势总结 |

> **执行语义**：所有交易信号每天收盘后生成一次，用于下一工作日计划。页面或 API 展示的盘中图表不代表盘中高频重算信号。

**响应** `400`
```json
{ "success": false, "message": "interval 必须是 daily/120min/90min/60min" }
```
```json
{ "success": false, "message": "缺少 stock 参数" }
```

---

## 行情图表

### GET /stock/chart-data

返回 WebUI 绘图用 JSON。后端不再渲染 PNG；浏览器基于该接口用 TradingView Lightweight Charts 绘制 K 线、成交量、趋势通道、MACD、结构信号、序列信号和决策点。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `stock` | string | 是 | — | 股票代码或名称，含中文请 URL-encode |
| `bars` | int | 否 | 180 | 最近 N 根，夹到 `[20, 500]` |

**响应** `200`
```json
{
  "success": true,
  "input": "000001",
  "count": 1,
  "results": [{
    "display_code": "000001.SZ",
    "daily": {
      "candles": [{ "time": "2024-01-01T09:30:00", "open": 10, "high": 11, "low": 9, "close": 10.5 }],
      "trend": [{ "time": "2024-01-01T09:30:00", "short_upper": 11, "short_lower": 9 }],
      "macd": [{ "time": "2024-01-01T09:30:00", "dif": 0.1, "dea": 0.05, "hist": 0.1 }],
      "sequence": [{ "time": "2024-01-01T09:30:00", "high9_signal": false }],
      "decisions": [{ "time": "2024-01-01T09:30:00", "action": "BUY", "target_position": 10 }]
    },
    "intraday": {
      "60min": {
        "candles": [],
        "macd": [],
        "structure": []
      }
    }
  }]
}
```

**示例**
```bash
curl "http://127.0.0.1:5555/api/stock/chart-data?stock=300274&bars=120"
```

---

## 回测

### POST /stock/backtest

对整合决策进行日频回测。撮合规则固定为 T 日收盘后生成信号，T+1 开盘成交；`final_target_position` 的 `0-10` 映射为 `0%-100%` 仓位。

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `stock` | string | 是 | — | 股票代码或名称 |
| `start_date` | string | 否 | 全量 | `YYYY-MM-DD` |
| `end_date` | string | 否 | 全量 | `YYYY-MM-DD` |
| `initial_cash` | number | 否 | 100000 | 初始资金 |
| `commission_rate` | number | 否 | 0.0003 | 单边手续费率 |
| `slippage_bps` | number | 否 | 5 | 单边滑点 bps |
| `min_bars` | int | 否 | 90 | 最少日线数量 |
| `lot_size` | int | 否 | 1 | 下单数量向下取整的最小单位 |

**响应** `200`
```json
{
  "success": true,
  "input": "000001",
  "count": 1,
  "results": [{
    "display_code": "000001.SZ",
    "metrics": {
      "total_return": 0.12,
      "annual_return": 0.18,
      "max_drawdown": -0.08,
      "sharpe": 1.2,
      "trade_count": 8,
      "benchmark_total_return": 0.09,
      "excess_return": 0.03
    },
    "equity_curve": [],
    "drawdown": [],
    "positions": [],
    "trades": [],
    "signals": []
  }]
}
```

---

## 工作流查询

### GET /stock/\<code\>/workflows

| 参数 | 位置 | 说明 |
|------|------|------|
| `code` | path | 股票内部代码（纯数字，如 `000001`） |

**响应** `200`
```json
{
  "success": true,
  "stock_code": "000001",
  "workflows": [{
    "id": "A_000001.SZ_5min",
    "market": "a",
    "stock_code": "000001",
    "display_code": "000001.SZ",
    "interval": "5min",
    "table": "A_000001.SZ_5min",
    "active": true,
    "created_at": "2026-04-25T12:00:00"
  }]
}
```

### GET /workflows

获取所有已注册工作流。

**响应** `200`
```json
{
  "success": true,
  "count": 1,
  "workflows": [{ "id": "A_000001.SZ_5min", "market": "a", ... }]
}
```

### DELETE /workflows/\<workflow_id\>

| 参数 | 位置 | 说明 |
|------|------|------|
| `workflow_id` | path | 工作流唯一标识 |

**响应** `200`
```json
{ "success": true, "message": "工作流 A_000001.SZ_5min 已删除" }
```

**响应** `404`
```json
{ "success": false, "message": "工作流 xxx 不存在" }
```

---

## 工作流标识格式

`{市场}_{股票代码}_{周期}`

| 市场 | 前缀 | 示例 |
|------|------|------|
| A 股 | `A` | `A_000001.SZ_5min`、`A_600519.SS_5min` |
| 港股 | `HK` | `HK_00700.HK_5min` |
| 美股 | `US` | `US_AAPL.US_5min` |

采集层只保留 5min；决策时 `daily` / `120min` / `90min` / `60min` 由 `resample` 从 5min 表运行时合成。

## 支持输入格式

| 市场 | 示例输入 |
|------|----------|
| A 股 | `000001`、`000001.SZ`、`600519`、`600519.SS`、`贵州茅台`（需先录入映射） |
| 港股 | `00700`、`00700.HK`、`阿里巴巴`（需先录入映射） |
| 美股 | `AAPL`、`AAPL.US`、`苹果`（需先录入映射） |
