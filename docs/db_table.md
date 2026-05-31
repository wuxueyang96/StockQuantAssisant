# 数据存储结构

数据以 **Parquet 列存格式** 存储在云对象存储（S3 / 阿里云 OSS）上，由 DuckDB 的 httpfs 扩展直接远程读写。不配置 OSS 时，使用本地文件系统作为后备。

当前采集层只保存 **5min 原始行情**。日线、60min、90min、120min 不单独落库，由 `app/services/resample.py` 在分析和图表渲染时运行时合成。

## 概览

| 存储路径 | 用途 | 文件数 |
|----------|------|--------|
| `metadata/stock_codes.parquet` | 股票名称映射 | 1 |
| `metadata/workflows.parquet` | 5min 工作流持久化 | 1 |
| `{market}/{table_name}.parquet` | 5min OHLCV 行情数据 | N（每个市场/股票 1 个） |

完整目录结构：

```text
s3://{bucket}/                         (OSS 模式)  或  {DATA_DIR}/  (本地模式)
├── metadata/
│   ├── stock_codes.parquet
│   └── workflows.parquet
├── a/
│   ├── A_000001.SZ_5min.parquet
│   ├── A_600519.SS_5min.parquet
│   └── ...
├── hk/
│   ├── HK_09988.HK_5min.parquet
│   └── ...
└── us/
    ├── US_BABA.US_5min.parquet
    └── ...
```

## 1. metadata/stock_codes.parquet — 股票名称映射

录入股票中文名称与各市场代码的对应关系。

| 列名 | 类型 | 说明 |
|------|------|------|
| `name` | string | 股票中文名称，如 `阿里巴巴` |
| `a_code` | string/nullable | A 股代码，6 位纯数字，如 `600519` |
| `hk_code` | string/nullable | 港股代码，4-5 位数字，如 `09988` |
| `us_code` | string/nullable | 美股代码，如 `BABA` |

## 2. metadata/workflows.parquet — 工作流持久化

存储所有已注册的数据采集工作流。服务重启时自动加载恢复。

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | string | 工作流唯一标识，格式 `{MARKET}_{CODE}_5min` |
| `market` | string | 市场代码：`a` / `hk` / `us` |
| `stock_code` | string | 内部股票代码，无后缀，如 `09988` |
| `interval` | string | 当前固定为 `5min` |
| `table` | string | 对应 Parquet 文件名（不含扩展名），值与 `id` 相同 |
| `db_path` | string | 保留字段，当前为空字符串 |
| `created_at` | string | 创建时间（ISO 8601） |
| `active` | int/bool | 是否活跃 |

## 3. OHLCV 数据 Parquet 文件

文件路径：`{market}/{MARKET}_{CODE}_5min.parquet`

| 列名 | 类型 | 说明 |
|------|------|------|
| `timestamp` | timestamp | 5min K 线时间戳 |
| `open` | float64 | 开盘价 |
| `high` | float64 | 最高价 |
| `low` | float64 | 最低价 |
| `close` | float64 | 收盘价 |
| `volume` | float64 | 成交量 |
| `dividends` | float64 | 分红 |
| `stock_splits` | float64 | 拆股 |

文件示例：

| 文件路径 | 对应工作流 |
|----------|-----------|
| `a/A_000001.SZ_5min.parquet` | A 股 000001 5min |
| `a/A_600519.SS_5min.parquet` | A 股 600519 5min |
| `hk/HK_09988.HK_5min.parquet` | 港股 09988 5min |
| `us/US_BABA.US_5min.parquet` | 美股 BABA 5min |

## 4. 运行时重采样

`resample_ohlcv(df_5m, target_interval)` 支持：

| 目标周期 | 5min 桶大小 | 说明 |
|----------|-------------|------|
| `daily` | 全日 | 每个交易日 1 根 |
| `60min` | 12 根 | 按日内序号切桶 |
| `90min` | 18 根 | A 股日内最后一根通常为 partial 尾巴 |
| `120min` | 24 根 | A 股一天 2 根 |

重采样结果会新增 `partial_bar` 字段：

- 完整桶：`partial_bar=false`
- 尾部不足桶：`partial_bar=true`
- 默认交易确认会过滤 90min partial 尾巴，展示路径仍可使用

## 5. 读写机制

```text
读:  DuckDB → read_parquet('s3://bucket/a/A_000001.SZ_5min.parquet')
     → 列裁剪 + 谓词下推 → 仅传输需要的行/列

写:  DuckDB → COPY (SELECT * FROM df) TO 's3://...' (FORMAT PARQUET)
     → 直接写 OSS，不经过本地磁盘
```

- **元数据**：启动时从 Parquet 加载到 DuckDB 内存表，写操作即时刷新到 OSS 或本地文件
- **OHLCV**：每次写入时读取已有数据 → 合并去重 → 写回 Parquet
- **本地模式**：不配 `OSS_BUCKET` 时自动使用本地文件路径，行为一致

## 6. 环境变量

| 变量 | 说明 |
|------|------|
| `OSS_BUCKET` | OSS Bucket 名称，不设则使用本地文件 |
| `OSS_ENDPOINT` | S3 兼容 Endpoint |
| `OSS_REGION` | 区域，默认 `us-east-1` |
| `OSS_ACCESS_KEY_ID` | Access Key，不设使用 IAM 角色 |
| `OSS_ACCESS_KEY_SECRET` | Secret Key |
| `STOCKQUANT_DATA_DIR` | 本地数据目录，默认 `~/.stockquant/data/` |
