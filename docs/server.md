# 服务端程序需求与当前实现

StockQuantAssisant 是一个 Python + Flask 服务，提供内置前端控制台、REST API、行情数据采集、任务调度和日频量化决策。当前实现以 **5min 采集 + 运行时重采样 + 三层次日计划** 为核心。

---

## 1. Web 与 API 行为

- `GET /`：返回内置前端控制台。
- `POST /api/stock/decision`：输入股票代码或名称，返回最新三层次日交易计划。
- `POST /api/stock/register`：注册数据同步工作流。
- `GET /api/stock/chart`：返回集成或单周期 PNG 行情图。
- `POST /api/stock/code` / `GET /api/stock/codes`：维护股票名称与市场代码映射。
- `GET /api/health`：健康检查。

输入支持：

- A 股：`000001`、`000001.SZ`、`600519.SS`、已录入中文名称
- 港股：`00700`、`00700.HK`、已录入中文名称
- 美股：`AAPL`、`AAPL.US`、已录入中文名称

名称解析通过 `stock_codes` 元数据表完成。若名称匹配多个市场，则一次返回多个市场结果。

---

## 2. 工作流唯一标识

当前采集层只创建 **1 个 5min 工作流**。高周期 K 线不落库，由 `resample` 在决策时从 5min 数据运行时合成。

格式：`{市场}_{股票代码}_5min`

| 市场 | 示例 |
|------|------|
| A 股 | `A_000001.SZ_5min`、`A_600519.SS_5min` |
| 港股 | `HK_00700.HK_5min` |
| 美股 | `US_AAPL.US_5min` |

历史的 `daily` / `120min` / `90min` / `60min` 工作流不再创建。

---

## 3. 数据库与表结构

- 数据以 **Parquet 列存格式** 存储在 OSS / S3 / MinIO，未配置 OSS 时使用本地目录。
- 每个 5min 工作流对应一个 Parquet 文件：`{market}/{table_name}.parquet`。
- 元数据（`stock_codes` / `workflows`）存储在 `metadata/` 目录下。
- DuckDB 通过 httpfs 远程读写 Parquet，实现存算分离。

目录结构：

```text
s3://{bucket}/
├── metadata/
│   ├── stock_codes.parquet
│   └── workflows.parquet
├── a/
│   └── A_000001.SZ_5min.parquet
├── hk/
│   └── HK_00700.HK_5min.parquet
└── us/
    └── US_AAPL.US_5min.parquet
```

---

## 4. 工作流注册时的初始化

注册新股票时：

1. 解析输入市场和代码。
2. 为每个市场检查唯一 5min 工作流。
3. 若对应 Parquet 文件不存在，则创建表。
4. 若表为空，则立即拉取初始 5min 历史数据。
5. 保存工作流元数据，并由调度器按 5 分钟间隔增量采集。

若工作流已存在但数据表为空，系统会尝试补拉初始数据。

---

## 5. 调度与更新策略

| 工作流周期 | 执行间隔 | 数据用途 |
|------------|----------|----------|
| 5min | 每 5 分钟 | 原始行情采集；决策时合成 daily / 60min / 90min / 120min |

- 每次采集会与已有数据按时间戳去重合并。
- 调度任务在非交易时段跳过。
- 高周期 K 线由 `app/services/resample.py` 合成：按交易日分组、按日内 K 线序号切桶，不跨日合并。
- 90min 最后一根不足 18 根 5min 时标记 `partial_bar=true`，默认只用于展示，不参与结构交易确认。

---

## 6. 量化决策服务

`analyze_stock(stock_input, interval='daily')` 的 `interval` 参数仅作为 `requested_interval` 回显。算法固定为 `integrated`：

1. 读取 `*_5min` 数据。
2. 合成日线、60min、90min、120min。
3. 日线趋势层输出 `base_target_position`、`position_cap`、`position_floor`。
4. 60/90/120min 结构层输出修边、风险提示、共振信息。
5. 日线序列层输出执行纪律。
6. 生成 `DailyTradingPlan`，并映射成向后兼容的 API 字段。

真实下单比例：

```text
order_delta = final_target_position - actual_position
weight = order_weight = clamp(abs(order_delta) / 10, 0, 1)
```

结构/序列不会再乘大真实 `weight`；`signal_strength` 仅用于展示和排序。

---

## 7. 工作流持久化

- 工作流元数据写入 `metadata/workflows.parquet`。
- 服务启动时加载已有工作流。
- 注册接口会返回已存在的工作流，避免重复创建。
- Stateless 部署模式下，多个实例可共享同一个 OSS 存储。

---

## 8. Stateless 部署 (Parquet on OSS)

数据以 Parquet 格式直接存储在 OSS 上，DuckDB 通过 httpfs 扩展远程读写：

- **零本地业务数据依赖**：OHLCV 数据和元数据均可在 OSS 中持久化。
- **远程查询**：`read_parquet('s3://...')` 支持列裁剪和谓词下推。
- **直接写入**：`COPY ... TO 's3://...' (FORMAT PARQUET)` 直接写 OSS。

环境变量：

| 变量 | 说明 |
|------|------|
| `OSS_BUCKET` | Bucket 名称，不设则使用本地 Parquet |
| `OSS_ENDPOINT` | S3 兼容 Endpoint |
| `OSS_REGION` | 区域，默认 `us-east-1` |
| `OSS_ACCESS_KEY_ID` | Access Key，不设可使用 IAM 角色 |
| `OSS_ACCESS_KEY_SECRET` | Secret Key |
| `STOCKQUANT_DATA_DIR` | 本地数据目录，默认 `~/.stockquant/data/` |

---

## 9. 技术要求

- Python + Flask
- DuckDB + Parquet on OSS
- APScheduler 任务调度
- akshare + yfinance 数据源
- pandas / NumPy 数据处理
- matplotlib / mplfinance 图表渲染
- 无构建链前端：Flask template + static CSS/JS
