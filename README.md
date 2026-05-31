# StockQuantAssisant - 股票量化助手

基于 Python + Flask 的股票数据采集、量化分析与调度服务，支持 A 股/港股/美股的多周期历史数据自动同步与量化决策输出。

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
│   │   └── decision.py            # DailyTradingPlan dataclass schema
│   ├── api/
│   │   └── routes.py              # REST API 路由
│   ├── templates/
│   │   └── index.html             # 前端控制台页面
│   ├── static/
│   │   ├── css/app.css            # 前端样式
│   │   └── js/app.js              # 前端交互
│   ├── models/
│   │   └── database.py            # DuckDB httpfs 引擎（Parquet on OSS）
│   ├── scheduler/
│   │   └── job_scheduler.py       # APScheduler 任务调度
│   └── services/
│       ├── stock_service.py       # 股票识别、数据拉取（akshare / yfinance）
│       ├── workflow_service.py    # 工作流注册与管理
│       ├── analysis_service.py    # 量化分析服务
│       ├── chart_service.py       # K 线图表渲染（mplfinance）
│       └── resample.py            # 多周期 OHLCV 重采样（5min → 60/90/120/daily）
├── tests/                         # 单元测试（232 个）
│   ├── conftest.py
│   ├── test_algorithm.py
│   ├── test_analysis_service.py
│   ├── test_api.py
│   ├── test_chart.py
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
│   └── test_workflow_service.py
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

> **stateless 部署**：设置 `OSS_BUCKET` 后，所有数据以 Parquet 格式直接存在 OSS 上，服务可随时启停，无需持久化本地磁盘。

### 运行测试

```bash
pytest tests/ -v                    # 单元测试（232 个）
python3 e2e/test_parquet.py         # Parquet 本地 E2E
python3 e2e/test_parquet.py --minio # Parquet on OSS E2E（需 MinIO）
```

## 主要功能

### 数据采集与调度

- 支持 A 股 / 港股 / 美股三市场，代码或名称输入
- 名称自动匹配多市场（如"阿里巴巴"→ 港股 09988.HK + 美股 BABA.US）
- 每个股票注册一个 5 分钟工作流定期增量采集，高周期通过运行时重采样生成
- 初次注册拉取约 200 周期历史数据，之后定时增量更新
- 工作流持久化，服务重启自动恢复

### 量化决策引擎

- **趋势层**：日线双通道（短周期 26 / 长周期 90，默认 3% 偏移）决定战略方向、基础目标仓位、仓位上限/下限。
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
| `POST` | `/api/stock/register` | 注册股票数据工作流 |
| `POST` | `/api/stock/decision` | 查询量化决策结果 |
| `GET` | `/api/stock/chart` | 渲染 K 线图表（集成/单周期模式） |
| `GET` | `/api/stock/<code>/workflows` | 查看指定股票工作流 |
| `GET` | `/api/workflows` | 查看所有工作流 |
| `DELETE` | `/api/workflows/<id>` | 删除工作流 |
| `GET` | `/api/health` | 健康检查 |

## 前端控制台

启动服务后访问根路径即可打开内置控制台，无需额外前端构建步骤：

```bash
http://127.0.0.1:5000/
```

页面直接调用现有 API，支持输入标的分析、注册工作流、查看次日交易计划、集成图表和原始 JSON。

## 工作流机制

- **唯一标识**：`{市场}_{股票代码}_5min`（如 `A_000001.SZ_5min`）
- **市场代码**：`A`（A 股）、`HK`（港股）、`US`（美股）
- **采集周期**：`5min`（唯一采集粒度，高周期在运行时通过重采样生成：60min / 90min / 120min / daily）

注册新股票时，工作流会拉取约 200 个完整周期的 5 分钟历史数据写入 Parquet，之后每个 5 分钟定时增量更新，仅在交易时段执行。高周期（60min / 90min / 120min / daily）数据在量化分析时通过重采样动态生成。

## 数据存储

- **格式**：Parquet 列存（列裁剪 + 谓词下推，远程查询只传输需要的行/列）
- **元数据**：`metadata/stock_codes.parquet` + `metadata/workflows.parquet`
- **OHLCV**：`{market}/{table_name}.parquet`，每股票一个文件（5min 粒度）
- **读取**：DuckDB `read_parquet('s3://...')` 远程直读
- **写入**：DuckDB `COPY ... TO 's3://...' (FORMAT PARQUET)` 远程直写

详见 [docs/db_table.md](docs/db_table.md)

## 数据源

- A 股：[akshare](https://github.com/akfamily/akshare)（不可用时自动回退 yfinance）
- 港股 / 美股：[yfinance](https://github.com/ranaroussi/yfinance)

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | Flask |
| 查询引擎 | DuckDB (httpfs 扩展) |
| 存储格式 | Parquet |
| 对象存储 | S3 / 阿里云 OSS (兼容 S3 API) |
| 数据处理 | Pandas / NumPy |
| 图表渲染 | matplotlib / mplfinance |
| 任务调度 | APScheduler |
| A 股数据 | akshare |
| 港股/美股数据 | yfinance |
| 测试 | pytest |
