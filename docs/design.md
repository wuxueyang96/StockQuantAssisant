# StockQuantAssisant 设计文档

## 1. 概述

StockQuantAssisant 是一个基于 Python + Flask 的股票数据采集、量化分析、前端控制台与回测系统，支持 A 股 / 港股 / 美股。数据以 **Parquet 列存格式** 存储在 **云对象存储 (S3 / OSS)** 上，通过 DuckDB 的 httpfs 扩展远程读写，实现 **存算分离** 和无状态部署。

量化决策采用「趋势为王、结构修边、序列纪律」的日频三层状态机。系统每天收盘后生成一次次日交易计划，真实下单比例只来自目标仓位与当前仓位的差值。

## 2. 架构总览

```
┌──────────┐
│  run.py  │
│  Flask   │
└────┬─────┘
     │
┌────▼───────────────────────────────┐
│        Web / API 层                 │
│  templates/index.html + static      │
│  api/routes.py                      │
└────┬───────────────────────────────┘
     │
┌────▼────────────────────▼──────────┐
│           Service 层                │
│ registration_service / stock_service│
│ analysis / chart_data / backtest svc│
│  resample                          │
└────┬───────────────┬───────────────┘
     │               │
┌────▼──────┐  ┌─────▼──────────────┐
│ Algos 层  │  │  Model 层           │
│ trend     │  │  DuckDB httpfs     │
│ structure │  │  + Parquet on OSS   │
│ sequence  │  └─────┬──────────────┘
│ decision  │        │
└───────────┘  ┌─────▼──────────────┐
               │ 外部存储 (S3 / OSS) │
               │ Parquet 列存文件     │
               └────────────────────┘
```

## 3. 模块设计

### 3.1 app/config.py — 全局配置

| 配置项 | 说明 |
|--------|------|
| `DATA_DIR` | 数据目录（默认 `~/.stockquant/data/`）；本地模式下 Parquet 存于此；OSS 模式下存于 S3 |
| `INTERVAL_MAP` | 采集层周期 → (period, interval) 映射；**仅包含 `5min`** |
| `INTERVAL_MINUTES` | 周期 → 分钟数（供 resample 使用） |
| `RESAMPLE_INTERVALS` | 运行时合成支持的目标周期 |
| `DATA_SOURCE` / `ITICK_TOKEN` / `ITICK_BASE_URL` | 主数据源选择；默认 AkShare，iTick token 通过环境变量注入 |
| `AKSHARE_STRICT_BACKFILL` / `AKSHARE_BACKFILL_CHUNK_DAYS` / `AKSHARE_DAILY_CHECK` | AkShare 补历史严格模式、分窗口大小和日线校验开关 |
| `ITICK_FREE_MIN_INTERVAL_SECONDS` / `ITICK_FREE_REFRESH_LIMIT` | iTick 固定免费额度限速与单页刷新配置 |
| `YFINANCE_TICKER_MAP` | 市场 → yfinance ticker 转换函数（兜底用） |
| `TRADING_HOURS` | 各市场交易时段和时区 |
| OSS 配置 | `OSS_BUCKET` / `OSS_ENDPOINT` / `OSS_REGION` / `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` |

### 3.2 API 层 — app/api/routes.py

Flask Blueprint `api_bp`，挂载 `/api` 前缀。根路径 `/` 渲染内置前端控制台，静态资源由 Flask 的 `static` 目录提供。详见 [api.md](api.md)。

| 端点 | 方法 | 功能 |
|------|------|------|
| `/stock/code` | POST | 录入/更新股票名称→代码映射 |
| `/stock/codes` | GET | 查看所有映射 |
| `/stock/register` | POST | 注册股票 |
| `/stock/data-status` | GET | 查询本地数据状态 |
| `/stock/clear-data` | POST | 清理本地 5min 数据，保留注册 |
| `/stock/refresh` | POST | 主动刷新最新数据 |
| `/refresh` | POST | 强制刷新所有已录入且已注册股票 |
| `/stock/backfill-estimate` | GET | 估算补历史 API 请求次数 |
| `/stock/backfill` | POST | 补历史 5min 数据 |
| `/data-jobs` | GET | 查询后台数据 Job 列表 |
| `/data-jobs/<id>` | GET | 查询后台数据 Job 状态 |
| `/data-jobs/<id>/tasks` | GET | 查询 Job 下的 Task |
| `/data-jobs/<id>/tasks/<task_id>/retry` | POST | 重试单个 Task，可指定数据源 |
| `/data-sources` | GET | 查询可选数据源 |
| `/stock/decision` | POST | 查询量化决策结果 |
| `/stock/chart-data` | GET | 返回 WebUI 绘图 JSON |
| `/stock/backtest` | POST | 回测整合决策 |
| `/stock/<code>/registrations` | GET | 查询指定股票注册记录 |
| `/registered-stocks` | GET | 查看所有已注册股票 |
| `/registered-stocks/<id>` | DELETE | 删除注册记录 |
| `/stock/unregister` | POST | 按股票代码或名称取消注册 |
| `/health` | GET | 健康检查 |

### 3.2.1 前端控制台 — templates / static

- `app/templates/index.html`：单页控制台入口，访问 `/`。
- `app/static/css/app.css`：页面布局与视觉样式。
- `app/static/js/app.js`：直接调用现有 API，支持标的分析、注册股票、查看三层次日计划、TradingView Lightweight Charts 行情图、数据 Job/Task 列表、Task 数据源选择重试、回测和原始 JSON。BUY/SELL 信号支持 hover 查看当日决策原因。
- 前端不引入 Node/Vite/Webpack 等构建链，部署方式与原 Flask 服务一致。

### 3.3 Service 层

#### stock_service.py

- `detect_market(input)` — 代码格式识别或名称反查 → `[(market, code), ...]`
- `resolve_stock_name(name)` — 从 `stock_codes` 表反查名称
- `fetch_stock_data(market, code, interval)` — 构造 `FetchRequest` 并交给数据源注册表拉取 5min K 线
- `collect_and_store(market, code, interval)` — 拉取 + 去重写入 Parquet；仅交易时段执行
- `is_trading_time(market)` — 交易时段判断

#### data_sources/

行情数据源被抽象为统一接口：

- `FetchRequest`：市场、代码、周期、历史窗口、分页上限等请求参数。
- `MarketDataSource.fetch_5m(request)`：返回标准 OHLCV DataFrame。
- `MarketDataSource.fetch_5m_strict(request)`：严格补历史入口，默认包装一次 `fetch_5m`；AkShare 实现会分窗口抓取、重试、合并去重并输出质量报告。
- `MarketDataSource.estimate_api_usage(...)`：返回统一 API 预算结构。
- `registry.fetch_5m`：按 `STOCKQUANT_DATA_SOURCE` 选择主源并执行 fallback。

当前实现：

| 数据源 | 文件 | 覆盖市场 | 说明 |
|--------|------|----------|------|
| akshare | `akshare_source.py` | A / HK | 默认主数据源；补历史默认启用严格分窗口、分钟条数检查和日线聚合校验 |
| iTick | `itick.py` | A / HK / US | 可选源；支持分页、固定免费额度限速和请求预算 |
| yfinance | `yfinance_source.py` | A / HK / US | 最后兜底；5min 历史窗口受 Yahoo 限制 |

**数据源策略**：业务层只依赖 `MarketDataSource` 接口，所有源都输出标准 5min OHLCV。60min / 90min / 120min / daily 由 `resample` 运行时合成。默认顺序为 akshare → yfinance；显式设置 `STOCKQUANT_DATA_SOURCE=itick` 时才使用 iTick，并在 token 缺失或请求失败时回退 akshare / yfinance。AkShare 的补历史接口默认走 `fetch_5m_strict`，`days` 被解释为交易日数量，并换算成保守自然日窗口后拆分请求；返回结果会报告空窗口、失败窗口、每日 5min bar 数和日线校验差异。

#### registration_service.py

`RegistrationService` 单例，采集层只保留 5min 注册记录：

- `register_stock(input)` → `detect_market` → 为每个市场创建 1 个 5min 注册记录
- `get_stock_registrations / get_all_registered_stocks` → 查询
- `delete_registration(registration_id)` → 从内存和 DB 中删除
- `unregister_stock(stock_input, clear_data=False)` → 按股票代码或名称取消注册，可选同时清理本地 5min 数据
- 注册信息持久化到 `metadata/registered_stocks.parquet`，启动时自动加载恢复
- 注册不会自动请求外部数据源；数据只通过刷新/补历史接口维护

#### analysis_service.py

`analyze_stock(stock_input, interval='daily')`：
1. 解析输入 → 读 `*_5min` → `resample` 出日线 + 60/90/120
2. `DecisionEngine.summary_integrated(df_daily, intraday)`：趋势与序列只看日线，结构看 60/90/120 并合并
3. `_enrich_resonance_integrated`：使用已经通过交易确认过滤的计划结果输出 60/90/120 共振
4. 返回 `interval: integrated`

#### data_service.py

- `get_data_status` — 返回本地 5min 数据行数、起止时间、日线数量和注册状态。
- `clear_stock_data` — 清理已注册股票的本地 5min 数据，保留注册记录。
- `refresh_data` — 面向已注册股票，拉取最近小窗口并通过 `insert_data` 增量写入。
- `refresh_all_registered` — 强制刷新所有已录入代码映射且已注册的股票。
- `backfill_data` — 面向已注册股票，拉取指定历史窗口并通过 `upsert_data` 合并补齐旧数据；默认 AkShare 严格模式会附带 `strict_report` 和 `quality_report`。

#### data_job_service.py

- `enqueue_backfill` — 将一次补历史请求创建为一个持久化 Job，并按 `AKSHARE_BACKFILL_CHUNK_DAYS` 拆成多个窗口 Task。
- Worker 顺序执行 Task；执行前先检查本地窗口数据，已有足够完整数据时标记 `skipped` 并跳过外部 API；Task 成功后立即 `upsert_data` 写入本地 5min 表，并更新 Job 成功数和进度。
- Task 失败或空返回会保留错误摘要，可通过 API/WebUI 单独重试；重试时可以指定数据源，显式重试不走 fallback。
- Job/Task 元数据持久化到 `metadata/data_jobs.parquet` 和 `metadata/data_tasks.parquet`，服务重启后可查看历史任务；重启时遗留的 running Task 会标记为失败。

#### resample.py

`resample_ohlcv(df_5m, target_interval)`：按交易日分组、按 K 线序号切桶。聚合：`Open=first, High=max, Low=min, Close=last, Volume=sum`。桶内不足时输出尾巴 K 线并标记 `partial_bar`。默认交易确认会过滤 90min 的 partial 尾巴。

#### chart_data_service.py

- `build_chart_data` — 输出浏览器绘图用 JSON。
- 日线：K 线、短/长期趋势通道边界、MACD、九转序列、逐日决策点。
- 60/90/120min：K 线、MACD、结构 75/100 信号和 active 状态。

#### backtest_service.py

- `run_backtest` — 解析标的并对每个市场回测。
- `simulate_backtest` — T 日信号、T+1 Open 撮合。
- 输出收益曲线、回撤、仓位、交易明细、买入持有基准和绩效指标。

### 3.4 Algos 层 — 量化算法

位于 `app/algos/`，核心模块对应 [algorithm.md](algorithm.md) 的三层状态机。

#### config.py — 策略配置与枚举

`StrategyConfig` 集中管理趋势通道、ATR 质量指标、过渡衰减、结构有效期、90min partial-bar 交易过滤、次日高开/低开执行保护等配置。`TrendState` / `StructureBias` / `Action` / `ConfidenceLabel` 用 enum 表达核心状态，减少魔法字符串。

#### trend.py — 趋势量化

`TrendChannel(short_period=26, long_period=90)`

通道公式：`EMA(RollingMax(Close, N), N)` / `EMA(RollingMin(Close, N), N)`。短期和长期是两个独立通道，每个通道各有上/下边界。默认使用收盘价极值；`channel_price_source='high_low'` 时可切回旧的 High/Low 极值口径。趋势层不内置 offset；突破后的缓冲、确认和执行过滤由 decision 层处理。

趋势状态：`UP_STRONG` / `UP_PULLBACK` / `RANGE` / `DOWN_REBOUND` / `DOWN_STRONG` / `UNKNOWN`。趋势层输出 `base_target_position`、`position_cap`、`position_floor`、`trend_reason` 和质量指标。突破判定使用 T 日收盘价对比 T-1 已确定轨线，避免未来函数。

#### structure.py — 结构量化

`MACDStructure(fast=12, slow=26, signal=9, smooth_k=2, eps=0.001, effective_horizon=5)`

状态机：`normal → top_divergence → top_75 → top_100 → reset → normal`（底部对称）。关键修复：peak_dif 逐根更新、100% 后立即 reset、带符号相对阈值比较、75% 需连续 K=2 根确认。

多周期共振只在 60/90/120min 上统计，`resonance.level` 为 1.0 / 1.5 / 2.0。
90min 尾部不足 18 根 5min 的 K 线标记为 `partial_bar`，默认仅展示、不参与交易确认。

#### sequence.py — 序列量化

`NineSequence(effective_horizon=5)`：高九（连续 9 根 `Close[i] > Close[i-4]`）、低九（对称）。有效期内价格突破 9 区间极值则立即失效，超 `H=5` 根自动失效。仅做 Setup 9，不做 Countdown 13。

序列层默认只输出执行纪律：高九对应 `NO_CHASE`，低九对应 `NO_PANIC_SELL`。`is_near_historical_extreme` 只作为 probe / warning 字段，不参与真实交易动作。

#### integrated_decision.py — 三层计划整合

`build_daily_trading_plan()` 是新的核心入口：趋势定战略仓位，结构在 cap/floor 内修边，序列输出执行规则，最后生成 `DailyTradingPlan`。

`final_target_position = clamp(base_target_position + structure_adjustment, position_floor, position_cap)`。

`order_weight = clamp(abs(final_target_position - actual_position) / 10, 0, 1)`。结构与序列只影响修边、提示、展示强度，不得无上限放大真实 `weight`。

#### decision.py — 决策引擎

`DecisionEngine` 三层职责：

| 级别 | 模块 | 职责 |
|------|------|------|
| 第一级 | TrendChannel | 战略方向、base_target_position、cap/floor |
| 第二级 | MACDStructure（3 周期并行 + 共振） | 仅在趋势边界内修边，输出 adjustment/warnings/resonance |
| 第三级 | NineSequence | 执行纪律，不直接改变 final_target_position |

核心规则：`final_target = clamp(base_target + structure_adjustment, floor, cap)`；`action` 由 `actual_position` 与 `final_target` 的差值推导。`weight = order_weight = |final_target - actual_position| / 10`，结构/序列不得乘大真实下单比例；`signal_strength` 仅用于展示和排序。

`summary_integrated(df_daily, intraday)` 返回：`decision`（action/weight/confidence/execute_at）、`signals`（structure/sequence/resonance）、`standards`（趋势通道边界 + structure 阈值）、`view`（人话态势 + 触发价位）。

### 3.4.1 Schema 层 — app/schemas/decision.py

`DailyTradingPlan` / `TrendContext` / `StructureContext` / `SequenceContext` / `DecisionContext` 使用 dataclass 表达对外嵌套结构。API 旧字段继续保留，新字段通过 `next_day_plan` 以及顶层 `trend` / `structure` / `sequence` / `decision` / `explanation` 暴露。

### 3.5 Model 层 — app/models/database.py

`DatabaseManager` 基于 DuckDB 内存引擎 + httpfs 扩展，实现 Parquet on OSS 存算分离：

- **OHLCV 数据**：每个 stock/market 一个 Parquet 文件，路径 `{market}/{table_name}.parquet`
- **元数据**（stock_codes / registered_stocks / data_jobs / data_tasks）：存储于 `metadata/` 目录下 Parquet 文件，启动时加载到 DuckDB 内存表或任务服务内存状态，写操作实时刷新
- 方法：`table_exists` / `insert_data` / `get_data` / `upsert_stock_code` / `save_registration` / `load_registered_stocks` 等

### 3.6 部署 — Parquet on OSS

#### 存储结构

```
s3://{bucket}/
├── metadata/
│   ├── stock_codes.parquet
│   ├── registered_stocks.parquet
│   ├── data_jobs.parquet
│   └── data_tasks.parquet
├── a/
│   ├── A_600519.SS_5min.parquet
│   └── A_000001.SZ_5min.parquet
├── hk/
│   └── HK_09988.HK_5min.parquet
└── us/
    └── US_BABA.US_5min.parquet
```

采集层只保留 5min；高粒度由 resample 运行时合成。

#### 读写流程

```
采集: fetch → DataFrame → merge → COPY TO 's3://...' (Parquet)
分析: read_parquet('s3://...') → DuckDB 谓词下推/列裁剪 → DataFrame → algo
```

#### 环境变量

| 变量 | 说明 |
|------|------|
| `OSS_BUCKET` | Bucket 名称，不设使用本地 Parquet |
| `OSS_ENDPOINT` | S3 兼容 Endpoint |
| `OSS_REGION` | 区域（默认 `us-east-1`） |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | 不设使用 IAM 角色 |
| `STOCKQUANT_DATA_DIR` | 本地数据目录 |

#### 认证

- IAM 角色：不设 AK/SK，DuckDB 从云平台自动获取
- 手动 AK/SK：设置 `OSS_ACCESS_KEY_ID` + `OSS_ACCESS_KEY_SECRET`

## 4. 数据流

```
POST /api/stock/register {"stock": "阿里巴巴"}
  ├─ detect_market("阿里巴巴") → resolve_stock_name → [('hk','09988'), ('us','BABA')]
  ├─ _register_one_market('hk', '09988') → create 5min table → save_registration
  ├─ _register_one_market('us', 'BABA') → create 5min table → save_registration
  └─ 返回 2 个 registration id；不隐式拉历史

POST /api/stock/backfill {"stock": "阿里巴巴", "days": 200, "queued": true}
  ├─ enqueue data job
  ├─ data_sources.registry → akshare / yfinance（或显式 iTick）→ standard 5min OHLCV
  ├─ upsert_data by timestamp
  └─ /api/data-jobs/<id> 轮询进度

POST /api/stock/decision {"stock": "阿里巴巴", "interval": "daily"}
  ├─ detect_market → [('hk','09988'), ('us','BABA')]
  ├─ get_data → 5min DataFrame → resample → daily + 60/90/120
  │   └─ DecisionEngine.summary_integrated → {action, weight, confidence, ...}
  └─ 返回两个市场决策结果（interval: integrated）
```

## 5. 关键技术决策

| 决策 | 理由 |
|------|------|
| DuckDB + Parquet on OSS (存算分离) | Parquet 列存直存 OSS，httpfs 远程查询，零本地盘 |
| 数据源接口 + iTick/akshare/yfinance 实现 | 业务层稳定依赖 `MarketDataSource`；后续换源只新增实现和注册表策略 |
| 仅采集 5min，高粒度运行时合成 | 规避 Yahoo 不支持 120m、港股小时线为空、A 股时区错配 |
| DIF 带符号相对阈值比较 | 统一顶/底背离判定，解决负 DIF 和 |DIF|<1 的语义反转 |
| 结构状态机 100% 后立即 reset | 避免单股生命周期只产一次信号 |
| 钝化区间 DIF 极值逐根更新 | 修复 DIF 在两峰之间漏检 |
| 趋势通道默认使用 Close 极值 | 上下边界口径一致，避免 High/Low 影线扭曲趋势通道；可通过 `channel_price_source='high_low'` 切回旧口径 |
| 趋势定仓、结构修边、序列纪律 | 避免结构/序列反客为主；真实 `weight` 只来自仓位差，`signal_strength` 仅展示 |
| cap/floor 边界内修边 | 结构不能反转趋势方向，避免仓位 4/6 附近的多空逻辑冲突 |
| 信号 T+1 计划执行 | 趋势用 T 日收盘判定，下一工作日执行，避免未来函数 |

## 6. 项目结构

```
StockQuantAssisant/
├── run.py
├── requirements.txt
├── pyproject.toml
├── docs/
│   ├── api.md
│   ├── server.md
│   ├── algorithm.md
│   ├── design.md
│   └── db_table.md
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── algos/
│   │   ├── config.py
│   │   ├── trend.py
│   │   ├── structure.py
│   │   ├── sequence.py
│   │   ├── integrated_decision.py
│   │   └── decision.py
│   ├── schemas/
│   │   ├── decision.py
│   │   └── backtest.py
│   ├── api/
│   │   └── routes.py
│   ├── templates/
│   │   └── index.html
│   ├── static/
│   │   ├── css/app.css
│   │   └── js/app.js
│   ├── models/
│   │   └── database.py
│   └── services/
│       ├── data_sources/
│       ├── stock_service.py
│       ├── registration_service.py
│       ├── analysis_service.py
│       ├── chart_data_service.py
│       ├── data_service.py
│       ├── data_job_service.py
│       ├── backtest_service.py
│       └── resample.py
├── tests/
│   ├── conftest.py
│   ├── test_algorithm.py
│   ├── test_analysis_service.py
│   ├── test_api.py
│   ├── test_backtest_service.py
│   ├── test_chart.py
│   ├── test_data_service.py
│   ├── test_database.py
│   ├── test_integrated_decision.py
│   ├── test_integration.py
│   ├── test_parquet_store.py
│   ├── test_resample.py
│   ├── test_resample_partial_bar.py
│   ├── test_sequence_execution_rules.py
│   ├── test_stock_service.py
│   ├── test_structure_boundaries.py
│   ├── test_trend_no_lookahead.py
│   └── test_registration_service.py
└── e2e/
    ├── run.py
    └── test_parquet.py
```

## 7. 测试

基于 pytest，覆盖：股票识别、数据源接口、Parquet CRUD、注册记录管理、API 端点、前端首页、WebUI 图表数据、回测、三个量化算法、三层决策引擎、多周期合成、90min partial-bar 过滤和集成流程。

```bash
pytest tests/ -v                        # 单元测试
python3 e2e/test_parquet.py             # Parquet 本地 E2E
python3 e2e/test_parquet.py --minio     # Parquet on OSS E2E
```
