### 一、趋势量化算法（双均线通道）

**目标**：用两条通道客观定义趋势方向，并自动生成战略仓位标准。趋势层是唯一可以决定大幅仓位变化的层级，输出 `base_target_position`、`position_cap`、`position_floor` 和 `trend_state`。

**核心原则**：趋势只看本周期收盘价（默认日线）。一切尽在收盘，收盘价是趋势判定的唯一标准。**T 日收盘后判定，T+1 日按目标仓位执行**，以避免未来函数。

#### 1. 通道构建规则

设短期周期 `N_s = 26`，长期周期 `N_l = 90`。

**短期通道**
*   上边界：`EMA( RollingMax(Close, N_s), N_s )`
*   下边界：`EMA( RollingMin(Close, N_s), N_s )`

**长期通道**
*   上边界：`EMA( RollingMax(Close, N_l), N_l )`
*   下边界：`EMA( RollingMin(Close, N_l), N_l )`

> **公式语义**：短期和长期是两个独立通道，不是"短上/短下/长上/长下四个通道"。每个通道的上下边界都基于收盘价极值序列：上边界追踪最近 N 根 K 线的收盘高点中枢，下边界追踪最近 N 根 K 线的收盘低点中枢。这样趋势突破、破位和实际收盘价口径一致，避免 High/Low 影线把通道边界虚胖或虚矮。若直接对 Close 求 EMA，会退化成普通均线，丧失通道支撑 / 阻力语义。

趋势层不再定义 offset。趋势线只表达当前按收盘价计算出来的短期 / 长期通道边界；突破之后是否需要缓冲、过滤、确认或延迟执行，统一交给 decision 层处理。

**设计依据**：徐小明"多空通道"指标。短期通道基于近 26 周期收盘高点 / 收盘低点的 EMA，长期通道基于近 90 周期收盘高点 / 收盘低点的 EMA。"分久必合，合久必分"，双通道的收敛与发散反映趋势强弱切换。

#### 2. 趋势判断与仓位量化

每个交易日**收盘后**，用 T 日收盘价 `Close_T` 与 **T-1 已经确定的短期/长期通道边界**比较。禁止用 T 日生成的 `channel_T` 来判断 T 日是否突破自身通道；`channel_T` 只作为状态更新和 T+1 参考线。

| trend_state | 条件（均使用 T-1 轨线） | base | cap | floor | 含义 |
|-------------|--------------------------|------|-----|-------|------|
| `UP_STRONG` | `Close_T > short_upper_{T-1}` 且 `Close_T > long_upper_{T-1}` | 10 | 10 | 8 | 强上升趋势 |
| `DOWN_STRONG` | `Close_T < short_lower_{T-1}` 且 `Close_T < long_lower_{T-1}` | 0 | 0 | 0 | 强下降趋势 |
| `UP_PULLBACK` | 长趋势仍向上，或 `Close_T > long_mid_{T-1}` 且长中轨/短中轨转强 | 8 | 10 | 6 | 上升趋势回调，多头未破坏 |
| `UP_WEAK` | `Close_T > long_mid_{T-1}` 且中轨转强，但强度不足以判为回调恢复 | 6 | 8 | 4 | 弱多头延续 |
| `DOWN_REBOUND` | `Close_T > short_upper_{T-1}` 且仍未恢复长中轨/长上轨强势 | 2 | 4 | 0 | 下降趋势中的短期反弹 |
| `RANGE` | 其他可判定状态 | 继承或衰减至 `range_target_position`（默认 4） | 6 | 0 | 震荡 / 无明确趋势 |
| `UNKNOWN` | 冷启动或数据不足 | — | — | — | 不产生交易动作 |

**冷启动**：首次有效仓位形成之前，`position = NaN`，不产生 BS 点。

**突破有效性**：趋势层只判断收盘价是否跨越 T-1 已确定轨线。额外的突破缓冲、ATR 过滤、连续确认或执行保护不写入趋势线本身，应由 decision 层在生成最终动作时处理。

**趋势质量指标**：趋势层同时输出 `short_mid_slope`、`long_mid_slope`、`channel_width_pct`、`atr_pct` 和 ATR 风控线。若 `Close_T > long_mid_{T-1}` 且 `long_mid_slope > 0`，不得直接判为普通 `RANGE`，至少应进入 `UP_WEAK` 或 `UP_PULLBACK`。

**过渡衰减**：`RANGE` 不会永久黏住旧仓位。连续 `transition_decay_days`（默认 3）日处于震荡/过渡后，基础仓位衰减至 `range_target_position`。

#### 3. 信号执行时点

*   当根 K 线的仓位判定，**基于 T 日收盘价**。
*   实际换仓动作在 **T+1 开盘** 执行（或 T+1 任一可执行时点），以避免未来函数。
*   回测撮合价格统一采用 `T+1 Open`。

---

### 二、结构量化算法（MACD 背离判定）

**目标**：基于 MACD 的背离，量化"趋势衰竭"的概率及转折点，作为对趋势仓位跃迁的"修边"修饰。

**核心原则**：结构在 **4 个周期** 上独立判定 — **60 分钟、90 分钟、120 分钟、日线**。任一周期形成顶部 / 底部结构，即构成该级别信号；周期越大或共振周期越多，级别越高（见 §5）。

**前提**：标准 MACD 参数 `(12, 26, 9)`，按本周期收盘价计算：
*   `EMA_fast = EMA(Close, 12)`
*   `EMA_slow = EMA(Close, 26)`
*   `DIF     = EMA_fast − EMA_slow`
*   `DEA     = EMA(DIF, 9)`
*   `MACD柱  = 2 × (DIF − DEA)`

#### 1. 钝化（Divergence）确认

钝化是结构形成的必要前置。状态机为每个方向维护一个"钝化跟踪窗口"，窗口起点由首次钝化触发的 K 线决定：

**顶部钝化**（上行段，DIF 通常 > 0）

在窗口内**每根 K 线**都同时更新两个累计极值：
*   `peak_price`：窗口内 `High` 的最大值。
*   `peak_dif`：窗口内 `DIF` 的最大值（**保留正负号**，不取绝对值）。

判定（当根 K 线 `High` 创窗口新高时）：
*   **形成**：`DIF_now` 未能创窗口新高（即 `DIF_now ≤ peak_dif`，按 §3 的容差比较）→ 当根标记为顶部钝化。
*   **破坏**：`DIF_now` 也创窗口新高 → 钝化失效，状态机回 `normal`，从下一根 K 线开始重新跟踪。

> 与原版的关键区别：`peak_dif` 在**每根 K 线**都尝试更新，而不是只在"价格创新高时"更新。这样钝化区间内 DIF 的真实极值不会被遗漏，避免"DIF 在两个价格峰之间偷偷创新高但未被检测"的漏报。

**底部钝化**（下行段，DIF 通常 < 0）

跟踪：
*   `valley_price`：窗口内 `Low` 的最小值。
*   `valley_dif`：窗口内 `DIF` 的最小值（**保留正负号**，越负越小）。

判定（当根 K 线 `Low` 创窗口新低时）：
*   **形成**：`DIF_now > valley_dif`（即 DIF 没有再创窗口新低）→ 当根标记为底部钝化。
*   **破坏**：`DIF_now` 也创窗口新低 → 钝化失效，回 `normal`。

#### 2. 结构形成判定（两步确认）

**第一步：75% 形成（DIF 转向）**

在钝化持续过程中：
*   **顶部 75%**：DIF 连续 `K = 2` 根下行（`DIF_{i} < DIF_{i-1} < DIF_{i-2}`），且 `DIF_now < peak_dif`。
*   **底部 75%**：DIF 连续 `K = 2` 根上行，且 `DIF_now > valley_dif`。

`K = 2` 用以过滤单根抖动，可配置。此步追求信号速度，作为初步预警。

**第二步：100% 完成（DIF 与 DEA 交叉）**

在 75% 形成之后：
*   **顶部 100%**：上一根 `DIF ≥ DEA` 且当根 `DIF < DEA` → 死叉，顶部结构确认。
*   **底部 100%**：上一根 `DIF ≤ DEA` 且当根 `DIF > DEA` → 金叉，底部结构确认。

此步追求信号稳定性，作为最终确认。

**100% 完成后立即重置**：

100% 触发的当根 K 线之后，立刻把状态机回到 `normal`，并清空 `peak_dif / valley_dif / peak_price / valley_price`，从下一根 K 线开始重新跟踪下一轮钝化。

> 此条修正了"状态机进入 100% 后无路径回到 normal、整个生命周期最多产生一次结构信号"的实现缺陷。

#### 3. DIF 数值比较规则

为过滤微小数值扰动，DIF 数值比较使用 **带符号的相对阈值**，**弃用**原"数量级前两位取整"规则（后者对负值与 |DIF| < 1 的边界存在语义反转与除零问题）：

```
strictly_greater(a, b, eps = 0.001):
    return (a - b) > eps * max(|a|, |b|, 1e-6)
```

*   顶部"DIF 创新高"：`strictly_greater(DIF_now, peak_dif)`。
*   底部"DIF 创新低"：`strictly_greater(valley_dif, DIF_now)`。
*   `eps` 仅作为"避免假相等 / 浮点扰动"的小容差，实现默认 **0.001（0.1%）**；上限可放宽到 2% 但会让稳定上涨场景下合理增长被误判为钝化，因此默认取小值，可配置。

#### 4. 结构级别（单周期内）

每个 100% 完成的结构记录两个维度，仅用于排序、解释与展示强度，不作硬阈值，不直接放大真实下单比例：

*   `duration`：从首次钝化出现到 100% 完成所经过的 K 线根数。越长，趋势衰竭酝酿越充分。
*   `decel`：钝化区间内 MACD 柱高度是否单调递减（顶部连续递减红柱，底部连续递减绿柱）。把柱高按时间均分 3 段，若后两段均值依次低于第一段则记 1，否则记 0。

综合级别：`level = duration × (1 + decel)`。

#### 5. 多周期共振规则

**周期合成（统一从 5min 源运行时合成，跨日不合并；A 股一日 48 根 5min 横跨午休按"日内序号"连续计数）：**

| 周期 | 桶大小（5min 根数） | A 股一日产出 |
|------|-------------------|--------------|
| 60min | 12 | 4 |
| 90min | 18 | 3（最后一根为 60min 尾巴，标记 `partial_bar=true`） |
| 120min | 24 | 2 |
| daily | 全天 | 1 |

合成 OHLCV 规则：`Open = 首根 Open`、`High = max(High)`、`Low = min(Low)`、`Close = 尾根 Close`、`Volume = Σ Volume`。

`partial_bar` 仅允许展示；默认不参与交易确认。特别是 90min 尾巴 K 线不会单独触发结构交易信号，交易确认路径会取最后一根完整 90min K 线。

> **采集策略**：所有市场只采集 5min K 线，业务层通过 `MarketDataSource.fetch_5m(FetchRequest)` 消费标准 OHLCV。当前默认主源为 iTick `/stock/kline`，akshare / yfinance 作为兼容兜底。高粒度由 `app/services/resample.py` 在决策时合成。这样避免直接依赖 Yahoo 的 120m、港股小时线和 A 股时区切片限制。

**共振级别**：在 **60min / 90min / 120min** 三根周期上，同一方向（顶/底）同时处于结构有效期内时：

| 共振 | 触发条件 | 级别权重 |
|------|---------|---------|
| 单周期 | 上述三根中仅 1 根结构 active | 1.0 |
| 二级共振 | ≥ 2 根同时结构 active | 1.5 |
| 三级共振 | 三根同时结构 active | 2.0 |

`resonance_weight ≥ 2.0` 时归类为高级别结构，可提升展示用 `signal_strength` / `confidence_label`，但不乘入真实 `order_weight`。（日线不参与结构共振计数；趋势由日线单独给出。）

#### 6. 结构信号有效期

*   75% 与 100% 信号在事件触发的那一根 K 线为 `True`，但其"有效状态"在后续周期内部 `event_valid_bars`（默认 5 根）内保持；active 期内必须保留事件类型（如 `top_100`）和事件发生时间，供整合层判断。
*   日频交易计划另按 `trading_valid_days` 判断结构是否仍影响次日计划：60min=1 个交易日、90min=1 个交易日、120min=2 个交易日、daily=5 个交易日。
*   出现以下情况之一，结构有效状态立即失效：
    *   出现反向钝化（如顶部 100% 形成后出现底部钝化）。
    *   趋势仓位反向跃迁（如 10 → 0、6 → 0）。
    *   周期内部 `event_valid_bars` 或日频 `trading_valid_days` 已过。

---

### 三、序列量化算法（九转序列）

**目标**：捕捉连续 9 个周期上的特定收盘价关系，作为**左侧择时的辅助级**信号。

**核心定义**：连续 9 根 K 线，每根 K 线的收盘价与其往前数第 4 根 K 线的收盘价进行比较，形成连续的上升或下降关系（即简化版 TD Setup）。

#### 1. 高九卖出序列

*   **基础计数**：从某根 K 线开始记为第 1 根；若 `Close[i] > Close[i-4]` 则计数 +1；否则计数中断并归零，从下一根 K 线重新开始。
*   **形成条件**：连续计数达到 9，且强度确认满足：

```
max(High[bar8], High[bar9]) ≥ max(High[bar6], High[bar7])
```

（与 Tom DeMark 原版口径一致，使用 `≥`，不再使用严格 `>`。）

*   **信号**：高 9 卖出，作为左侧减仓 / 不宜追高的参考。

#### 2. 低九买入序列

*   **基础计数**：`Close[i] < Close[i-4]` 计数 +1，否则归零。
*   **形成条件**：连续 9 + 强度确认：

```
min(Low[bar8], Low[bar9]) ≤ min(Low[bar6], Low[bar7])
```

*   **信号**：低 9 买入，作为左侧加仓 / 不宜杀跌的参考。

#### 3. 失效与有效期

*   完整形成 9 后，信号在 `H = 5` 根 K 线内保持有效，用于与结构共振和执行纪律提示。
*   失效条件：
    *   高 9 形成后，价格向下跌破"9 区间内最低 Low" → 立即失效。
    *   低 9 形成后，价格向上突破"9 区间内最高 High" → 立即失效。
    *   超过有效期 `H` 根 K 线 → 自动失效。
*   本算法仅实现 Setup 9，不做 Countdown 13；定位为执行纪律信号，不单独驱动真实交易动作。

#### 4. 动态显示规则

*   计数 1~8 阶段为**动态**，盘中可消失、回退；只有完整形成 9 才作为锁定的、无未来函数的历史信号供决策使用。
*   序列 9 的可靠性强依赖趋势方向：上升趋势中低 9 更有效，下降趋势中高 9 更有效。这是 **"趋势为王"** 的核心体现。

---

### 四、系统整合：三层状态机（趋势为王、结构修边、序列纪律）

**执行语义**：T 日收盘后生成一次决策，T+1 可执行时点下单；遇到周末时顺延至下一工作日的可执行时段。**不做高频、不用未来函数**。

**突破判定（趋势）**：T 日 `Close` 仅与 **T-1 已确定通道边界**比较；T 日生成的 `channel_T` 不参与 T 日突破判定。

**API / 周期分工**

- **趋势**：仅 **日线** → `base_target_position`、`position_cap` / `position_floor`、`trend_state`。
- **结构**：**60min / 90min / 120min** MACD 结构；通用周期优先级 **daily > 120 > 90 > 60**，API 共振统计仅用 **120 > 90 > 60**；事件完成度优先级 **100% > 75%**；`90min` 末根 `partial_bar` 默认不参与交易确认。
- **序列**：默认 **日线** 九转 → `execution_rules`（如 `NO_CHASE`、`NO_PANIC_SELL`），**默认不改变** `final_target_position`。
- **对外快照**：`timestamp` / `close` / `execute_at` 对齐日线最后一根；`interval` 固定 `integrated`。

#### 第一层：趋势层（战略仓位）

`trend_state`：`UP_STRONG` / `UP_PULLBACK` / `UP_WEAK` / `RANGE` / `DOWN_REBOUND` / `DOWN_STRONG` / `UNKNOWN`。

| 状态 | base | cap | floor |
|------|------|-----|-------|
| UP_STRONG | 10 | 10 | 8 |
| UP_PULLBACK | 8 | 10 | 6 |
| UP_WEAK | 6 | 8 | 4 |
| RANGE | 继承或衰减至 `range_target_position`（默认 4） | 6 | 0 |
| DOWN_REBOUND | 2 | 4 | 0 |
| DOWN_STRONG | 0 | 0 | 0 |
| UNKNOWN | — | — | — |

`RANGE` 连续 `transition_decay_days`（默认 3）日后衰减至区间目标仓位，避免永久黏滞旧仓。

#### 第二层：结构层（修边，不反客为主）

```
structure_adjustment ∈ [-max_structure_adjustment, +max_structure_adjustment]  （默认 ±2）
final_target = clamp(base_target + structure_adjustment, position_floor, position_cap)
```

- 顶部结构：**不得**在上升趋势中反手做空至 0；仅降 cap 内目标或输出 `NO_CHASE` / `TRIM_ALLOWED`。
- 底部结构：**不得**在 `DOWN_STRONG` 直接 BUY；可 `BOTTOM_WATCH` / `NO_PANIC_SELL`。
- `UP_STRONG + 顶部结构` 最多 `10 -> 8`；`UP_PULLBACK + 顶部结构` 最低到 6；多头趋势下短周期顶部结构不得把最终目标压到 4 以下。
- 顶底冲突：`structure_adjustment = 0`，`CONFLICT_WARNING`。
- `signals.resonance` 仅展示；**不乘入**真实 `weight`。

结构对次日计划有效期（`trading_valid_days`）：60/90min 默认 1 日，120min 2 日，daily 5 日。

#### 第三层：序列层（执行纪律）

- 高九 active → `NO_CHASE`；与顶结构 + 强趋势 → `TRIM_ALLOWED`。
- 低九 active → `NO_PANIC_SELL`；回调 + 底结构 → `PULLBACK_BUY_CANDIDATE`。
- `is_near_historical_extreme` 仅 `probe` / 预警，不驱动 action。
- 固定 **不**修改 `final_target_position`；只影响 `signal_strength` 或 `execution_rules`。

#### 最终决策与 action

```
actual_position  ≈ 上一交易日 final_target（无持仓 API 时的代理）
order_delta      = final_target − actual_position
order_weight     = clamp(|order_delta| / 10, 0, 1)    ← API 字段 weight
signal_strength  ≥ order_weight，仅排序/展示，可 > 1

if final_target > actual  → BUY
if final_target < actual  → SELL
if equal                  → HOLD（可有纪律规则）
if UNKNOWN / 无目标      → WAIT
```

#### 仓位限速与 hard_exit

- 默认 `normal_max_position_step = 2`。非 hard_exit 情况下，`final_target_position` 相对 `actual_position` 单日最多变化 2 档。
- hard_exit 才允许快速降仓；若 hard_exit 触发但趋势原始目标要求加仓，则目标最多持平，不允许 BUY。
- hard_exit 条件：
  - `Close_T < long_lower_{T-1}`；
  - 或 `Close_T < long_mid_{T-1}` 且 `long_mid_slope <= 0`，连续确认不少于 2 天；
  - 或跌破 ATR 风控线。
- 顶部结构、高九、短周期背离不属于 hard_exit。
- 若上一交易日刚 BUY 到高目标仓位，下一交易日除非 hard_exit，否则不得直接 SELL 到 6 以下；若上一交易日刚 SELL 到低目标仓位，下一交易日不得直接 BUY 到 10，并记录 `target_reversal_warning`。

次日执行原则（`principle` / `forbidden_actions` / `invalidation`）：如 BUY+`NO_CHASE` 时高开超 `max_chase_gap` 不追；SELL+`NO_PANIC_SELL` 时低开分批。

#### API 输出（向后兼容 + 扩展）

| 旧字段 | 含义（新） |
|--------|------------|
| `action` | BUY / SELL / HOLD / WAIT |
| `weight` | **order_weight**，恒 ∈ [0, 1] |
| `confidence` | 映射自 `confidence_label`（trend / core / resonance） |
| `position.current` | **final_target_position** |
| `position.prev` | **actual_position** |

扩展字段：`next_day_plan`、`trend`、`structure`、`sequence`、`decision`、`explanation`（见 `app/schemas/decision.py`）。

实现入口：`app/algos/integrated_decision.py` → `build_daily_trading_plan()`。
