# StockQuantAssisant - 股票量化助手

基于 Python + Flask 的股票数据采集、量化分析与 WebUI 控制台，支持 A 股/港股/美股的 5min 历史数据维护、运行时多周期合成、量化决策与回测输出。

数据以 **Parquet 列存格式** 直接存储在云对象存储（S3 / 阿里云 OSS）上，DuckDB 通过 httpfs 扩展远程读写，实现真正的存算分离和零本地盘部署。

系统当前采用「趋势为王、结构修边、序列纪律」的日频三层决策模型：每天收盘后生成一次次日计划，输出次日目标仓位、可执行动作、交易原则、禁止动作、失效条件和解释文本。

## 项目结构

```
StockQuantAssisant/
├── pyproject.toml                 # 包元数据（pip install）
├── run.py                         # 启动入口 / WSGI callable
├── requirements.txt               # 依赖
├── docs/
│   ├── api.md                     # API 文档
│   ├── server.md                  # 服务端需求文档
│   ├── algorithm.md               # 量化算法文档
│   ├── design.md                  # 系统设计文档
│   └── db_table.md                # 数据存储结构文档
├── app/
│   ├── __init__.py                # Flask 工厂函数
│   ├── config.py                  # 配置文件
│   ├── algos/
│   │   ├── config.py              # 策略配置与枚举
│   │   ├── trend.py               # 趋势量化（双 EMA 通道）
│   │   ├── structure.py           # 结构量化（MACD 背离判定）
│   │   ├── sequence.py            # 序列量化（九转序列）
│   │   ├── integrated_decision.py # 三层次日计划整合
│   │   └── decision.py            # 决策引擎（三层整合）
│   ├── schemas/
│   │   ├── decision.py            # DailyTradingPlan dataclass schema
│   │   └── backtest.py            # Backtest dataclass schema
│   ├── api/
│   │   └── routes.py              # REST API 路由
│   ├── templates/
│   │   └── index.html             # 前端控制台页面
│   ├── static/
│   │   ├── css/app.css            # 前端样式
│   │   └── js/app.js              # 前端交互
│   ├── models/
│   │   └── database.py            # DuckDB httpfs 引擎（Parquet on OSS）
│   └── services/
│       ├── data_sources/          # 行情数据源接口与 iTick / akshare / yfinance 实现
│       ├── stock_service.py       # 股票识别、数据拉取与入库
│       ├── registration_service.py # 已注册股票管理
│       ├── analysis_service.py    # 量化分析服务
│       ├── chart_data_service.py  # WebUI 图表数据（K线/趋势/MACD/信号）
│       ├── data_service.py        # 数据状态、主动刷新与历史补数据
│       ├── data_job_service.py    # 后台数据任务队列
│       ├── backtest_service.py    # 决策回测、撮合与绩效指标
│       └── resample.py            # 多周期 OHLCV 重采样（5min → 60/90/120/daily）
├── tests/                         # 单元测试
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
│   ├── test_registration_service.py
│   ├── test_resample.py
│   ├── test_resample_partial_bar.py
│   ├── test_sequence_execution_rules.py
│   ├── test_stock_service.py
│   ├── test_structure_boundaries.py
│   ├── test_trend_no_lookahead.py
└── e2e/
    ├── run.py                     # API 全链路 E2E
    └── test_parquet.py            # Parquet on OSS E2E
```

## 快速开始

### 方式一：pip install（推荐）
```bash
pip install .
```

安装后直接用命令行启动：
```bash
stockquant-server start --host 0.0.0.0 --port 5000
```

停止服务：
```bash
stockquant-server stop
```

可选参数：
- `--host` : 监听地址（默认 `0.0.0.0`）
- `--port` : 监听端口（默认 `5000`）
- `--debug` : 开启调试模式

WSGI 部署（gunicorn）：
```bash
gunicorn "run:app" -w 4 -b 0.0.0.0:5000
```

### 方式二：源码运行
```bash
pip install -r requirements.txt
python run.py start --host 0.0.0.0 --port 5000
```

### 配置

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `STOCKQUANT_DATA_DIR` | 本地数据目录 | `~/.stockquant/data/` |
| `OSS_BUCKET` | OSS Bucket 名称（不设则使用本地 Parquet） | — |
| `OSS_ENDPOINT` | S3 兼容 Endpoint | — |
| `OSS_REGION` | 区域 | `us-east-1` |
| `OSS_ACCESS_KEY_ID` | Access Key（不设使用 IAM 角色） | — |
| `OSS_ACCESS_KEY_SECRET` | Secret Key | — |
| `STOCKQUANT_DATA_SOURCE` | 主数据源，可选 `akshare` / `itick` / `yfinance`；当前默认 AkShare | `akshare` |
| `AKSHARE_STRICT_BACKFILL` | AkShare 补历史是否启用分窗口严格模式 | `true` |
| `AKSHARE_BACKFILL_CHUNK_DAYS` | AkShare 严格补数的单个请求自然日窗口 | `30` |
| `AKSHARE_BACKFILL_RETRIES` | AkShare 单窗口失败后的重试次数 | `2` |
| `AKSHARE_BACKFILL_RETRY_SLEEP_SECONDS` | AkShare 单窗口重试等待秒数 | `1.0` |
| `AKSHARE_BACKFILL_CHUNK_DELAY_SECONDS` | AkShare 窗口之间的等待秒数 | `0.2` |
| `AKSHARE_DAILY_CHECK` | AkShare 补历史后是否用日线数据校验分钟聚合结果 | `true` |
| `AKSHARE_DAILY_PRICE_TOLERANCE` | 分钟聚合日线与日线接口价格允许误差 | `0.03` |
| `AKSHARE_DAILY_VOLUME_REL_TOLERANCE` | 分钟聚合日线与日线接口成交量相对误差阈值 | `0.05` |
| `AKSHARE_EXPECTED_A_5MIN_BARS` / `AKSHARE_EXPECTED_HK_5MIN_BARS` | 每个交易日最低预期 5min bar 数量 | `48` / `66` |
| `ITICK_TOKEN` / `ITICK_API_KEY` | iTick API token | — |
| `ITICK_BASE_URL` | iTick 股票 REST 地址；免费体验可用 `https://api-free.itick.org/stock`，生产可设 `https://api.itick.org/stock` | `https://api-free.itick.org/stock` |
| `ITICK_PAGE_LIMIT` | iTick 单页 K 线条数 | `1000` |
| `ITICK_MAX_PAGES` | iTick 单次拉取最多分页数 | `30` |
| `ITICK_RETRIES` | iTick HTTP 重试次数 | `3` |
| `ITICK_FREE_MIN_INTERVAL_SECONDS` | iTick REST 请求最小间隔；当前固定按免费额度限速 | `13` |
| `ITICK_FREE_REFRESH_LIMIT` | iTick 单次刷新请求的 K 线条数 | `1000` |

> **stateless 部署**：设置 `OSS_BUCKET` 后，所有数据以 Parquet 格式直接存在 OSS 上，服务可随时启停，无需持久化本地磁盘。

### 运行测试

```bash
pytest tests/ -v                    # 单元测试
python3 e2e/test_parquet.py         # Parquet 本地 E2E
python3 e2e/test_parquet.py --minio # Parquet on OSS E2E（需 MinIO）
```

## 主要功能

### 数据采集与维护

- 支持 A 股 / 港股 / 美股三市场，代码或名称输入
- 名称自动匹配多市场（如"阿里巴巴"→ 港股 09988.HK + 美股 BABA.US）
- 每个股票注册一个 5min 数据表，高周期通过运行时重采样生成
- 默认使用 AkShare 拉取 5min 数据；补历史时按自然日分窗口逐段请求，并用日线数据校验分钟聚合结果；美股会 fallback 到 yfinance
- iTick 仍可通过 `STOCKQUANT_DATA_SOURCE=itick` 启用，并固定按免费额度限速
- 注册只保存标的，不自动拉取历史；数据完全通过“刷新数据”或“补历史数据”手动维护
- 已注册股票持久化，服务重启自动恢复

### 量化决策引擎

- **趋势层**：日线双通道（短周期 26 / 长周期 90，基于收盘价滚动极值）决定战略方向、基础目标仓位、仓位上限/下限；突破过滤和执行保护由 decision 层处理。
- **结构层**：60/90/120min MACD 背离结构只做仓位边界内的修边，不反转趋势方向，不放大真实下单比例。
- **序列层**：日线九转只输出执行纪律，如 `NO_CHASE` / `NO_PANIC_SELL`，默认不改变最终目标仓位。
- **次日计划**：`weight = order_weight = abs(final_target - actual_position) / 10`，恒在 `[0, 1]`；`signal_strength` 仅用于展示和排序。

## REST API

详见 [docs/api.md](docs/api.md)

核心端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/stock/code` | 录入股票名称 → 市场代码映射 |
| `GET` | `/api/stock/codes` | 查看所有已录入映射 |
| `POST` | `/api/stock/register` | 注册股票 |
| `GET` | `/api/stock/data-status` | 查看本地数据起止、行数和日线数量 |
| `POST` | `/api/stock/refresh` | 主动刷新已注册股票的最新 5min 数据 |
| `POST` | `/api/refresh` | 强制刷新所有已录入且已注册的股票 |
| `GET` | `/api/stock/backfill-estimate` | 估算补历史需要的数据源 API 请求次数 |
| `POST` | `/api/stock/backfill` | 为已注册股票补历史 5min 数据 |
| `GET` | `/api/data-jobs/<id>` | 查询后台数据任务状态 |
| `POST` | `/api/stock/decision` | 查询量化决策结果 |
| `GET` | `/api/stock/chart-data` | 获取 WebUI 绘图数据 |
| `POST` | `/api/stock/backtest` | 回测整合决策结果 |
| `GET` | `/api/stock/<code>/registrations` | 查看指定股票注册记录 |
| `GET` | `/api/registered-stocks` | 查看所有已注册股票 |
| `DELETE` | `/api/registered-stocks/<id>` | 删除注册记录 |
| `GET` | `/api/health` | 健康检查 |

## 前端控制台

启动服务后访问根路径即可打开内置控制台，无需额外前端构建步骤：

```bash
http://127.0.0.1:5000/
```

页面直接调用现有 API，支持输入标的分析、注册股票、查看次日交易计划、TradingView 风格行情图、回测收益曲线、交易明细和原始 JSON。行情图上的 BUY/SELL 信号支持 hover 查看当日决策原因。

## 回测系统

- 信号源：`evaluate_integrated_dataframe`，逐日使用截至 T 日的数据生成决策。
- 撮合语义：T 日收盘后生成计划，T+1 开盘价成交。
- 仓位映射：策略 `0-10` 仓位映射为 `0%-100%` 资金暴露。
- 成本模型：支持手续费率、滑点 bps、初始资金和最小日线数量。
- 输出：收益曲线、回撤、仓位、交易明细、信号点、买入持有基准和绩效指标。

## 注册股票机制

- **唯一标识**：`{市场}_{股票代码}_5min`（如 `A_000001.SZ_5min`）
- **市场代码**：`A`（A 股）、`HK`（港股）、`US`（美股）
- **采集周期**：`5min`（唯一采集粒度，高周期在运行时通过重采样生成：60min / 90min / 120min / daily）

注册新股票时，系统只创建并持久化唯一 5min 注册记录，不隐式请求外部数据源。用户通过“刷新数据”或“补历史数据”显式维护 5min 数据；高周期（60min / 90min / 120min / daily）在量化分析时通过重采样动态生成。

已注册股票支持两类手动数据维护：

- **刷新最新数据**：`POST /api/stock/refresh`，拉取最近小窗口并只写入最新增量；`POST /api/refresh` 可批量刷新所有已录入且已注册的股票。
- **补历史数据**：`POST /api/stock/backfill`，`days` 表示向前补的交易日数量；系统会换算为更大的自然日请求窗口。默认 AkShare 严格模式会按 `AKSHARE_BACKFILL_CHUNK_DAYS` 拆成多个请求，合并去重后按实际交易日截断，并返回窗口执行情况、分钟条数检查和日线校验报告。

注意：AkShare 是免费数据源，分钟历史能否完整返回取决于 AkShare 与其上游接口；严格模式会尽量慢速分段请求并把空窗口、失败窗口和校验差异明确返回。iTick 会返回每个市场常规交易时段内的 5min K 线；A 股可能包含开盘集合/开盘 K，系统会保留常规时段数据并由重采样层处理尾部 partial bar。未配置 iTick token 或 iTick 请求失败时，系统会回退到 akshare / yfinance，其中 yfinance 5m 常限制最近 60 天。

## 数据存储

- **格式**：Parquet 列存（列裁剪 + 谓词下推，远程查询只传输需要的行/列）
- **元数据**：`metadata/stock_codes.parquet` + `metadata/registered_stocks.parquet`
- **OHLCV**：`{market}/{table_name}.parquet`，每股票一个文件（5min 粒度）
- **读取**：DuckDB `read_parquet('s3://...')` 远程直读
- **写入**：DuckDB `COPY ... TO 's3://...' (FORMAT PARQUET)` 远程直写

详见 [docs/db_table.md](docs/db_table.md)

## 数据源

- 默认主数据源：AkShare，覆盖 A 股 / 港股 5min K 线；美股 fallback 到 yfinance。
- 可选数据源：[iTick](https://docs.itick.org/zh-cn/rest-api/stocks/stock-kline)，覆盖 A 股 / 港股 / 美股 5min K 线，固定按免费额度限速。
- 数据源统一实现 `MarketDataSource.fetch_5m(FetchRequest)`，业务层只消费标准 OHLCV DataFrame。
- 兼容兜底：未配置 iTick token 或 iTick 请求失败时，A 股 / 港股仍可回退 akshare，美股回退 yfinance。

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | Flask |
| 查询引擎 | DuckDB (httpfs 扩展) |
| 存储格式 | Parquet |
| 对象存储 | S3 / 阿里云 OSS (兼容 S3 API) |
| 数据处理 | Pandas / NumPy |
| 图表渲染 | TradingView Lightweight Charts（行情图 + 回测图）+ Canvas fallback |
| 行情数据 | AkShare 默认，iTick 可选，yfinance 兜底 |
| 测试 | pytest |
