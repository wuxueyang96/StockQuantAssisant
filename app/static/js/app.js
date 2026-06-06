const state = {
  stock: '',
  lastPayload: null,
  chartData: null,
  chartInterval: 'daily',
  chartDays: 60,
  backtestData: null,
  btCharts: [],
  btRenderFrame: null,
  dataJobPoll: null,
  selectedJobId: null,
  dataJobs: [],
  dataSources: [],
  tv: {
    priceChart: null,
    macdChart: null,
    candleSeries: null,
    latestSnapshot: null,
    resizeObserver: null,
    resizeFrame: null,
    signalTooltip: null,
  },
};

const $ = (id) => document.getElementById(id);

function setMessage(text, type = 'info') {
  const el = $('message');
  if (!text) {
    el.hidden = true;
    el.textContent = '';
    el.className = 'notice';
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.className = `notice ${type === 'error' ? 'error' : ''}`;
}

function setBusy(isBusy) {
  document.querySelectorAll('button').forEach((btn) => {
    btn.disabled = isBusy;
  });
}

function fmt(value, fallback = '--') {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  if (typeof value === 'number') {
    const abs = Math.abs(value);
    if (abs !== 0 && abs < 0.0001) return value.toExponential(2);
    return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  }
  if (Array.isArray(value)) return value.length ? value.join(', ') : fallback;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

function fmtPct(value, fallback = '--') {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function fmtMoney(value, fallback = '--') {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function fmtDuration(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 60) return `${Math.ceil(value)} 秒`;
  return `${Math.ceil(value / 60)} 分钟`;
}

function apiBudgetText(budget) {
  if (!budget) return '--';
  const requests = fmt(budget.request_count, '0');
  if (budget.strict && budget.window_count) {
    const daily = Number(budget.daily_request_count || 0);
    const dailyText = daily ? ` + 日线 ${daily} 次` : '';
    return `${requests} 次 / ${fmt(budget.window_count, '0')} 窗口${dailyText}`;
  }
  const suffix = budget.free_mode ? ` / 约 ${fmtDuration(budget.estimated_seconds)}` : '';
  return `${requests} 次${suffix}`;
}

function shortDate(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function dateKey(value) {
  if (!value) return '';
  return String(value).slice(0, 10);
}

function fullDate(value) {
  return dateKey(value) || '--';
}

function actionClass(action) {
  return String(action || 'WAIT').toLowerCase();
}

function setActionVisual(action) {
  const normalized = actionClass(action);
  const badge = $('actionBadge');
  const metric = $('metricAction');
  badge.className = `action-badge ${normalized}`;
  metric.className = normalized;
  badge.textContent = action || 'WAIT';
  metric.textContent = action || 'WAIT';
}

function renderKV(containerId, rows) {
  const container = $(containerId);
  container.textContent = '';
  rows.forEach(([label, value]) => {
    const row = document.createElement('div');
    row.className = 'kv-row';
    const left = document.createElement('span');
    left.textContent = label;
    const right = document.createElement('strong');
    right.textContent = fmt(value);
    row.append(left, right);
    container.appendChild(row);
  });
}

function renderList(container, items) {
  container.textContent = '';
  if (!items || !items.length) {
    const li = document.createElement('li');
    li.textContent = '--';
    container.appendChild(li);
    return;
  }
  items.forEach((item) => {
    const li = document.createElement('li');
    li.textContent = item;
    container.appendChild(li);
  });
}

function resultPlan(result) {
  return result.next_day_plan || {
    date: result.timestamp,
    close: result.close,
    trend: result.trend || {},
    structure: result.structure || {},
    sequence: result.sequence || {},
    decision: result.decision || {},
    explanation: result.explanation || [],
  };
}

function updateDashboard(result) {
  const plan = resultPlan(result);
  const decision = plan.decision || {};
  const trend = plan.trend || {};
  const structure = plan.structure || {};
  const sequence = plan.sequence || {};
  const keyLines = trend.key_lines || {};
  const quality = trend.quality || {};

  state.lastPayload = result;
  $('jsonOutput').textContent = JSON.stringify(result, null, 2);

  setActionVisual(decision.action || result.action || 'WAIT');
  $('metricTarget').textContent = fmt(decision.final_target_position ?? result.position?.current);
  $('metricWeight').textContent = fmt(decision.order_weight ?? result.weight);
  $('metricClose').textContent = fmt(plan.close ?? result.close);

  const titleParts = [
    result.display_code || state.stock,
    trend.state,
    `目标 ${fmt(decision.final_target_position ?? result.position?.current)}`,
  ].filter(Boolean);
  $('planTitle').textContent = titleParts.join(' / ') || '交易计划';
  $('executeDate').textContent = fmt(plan.execute_date || plan.execute_at || result.execute_at);
  $('actualPosition').textContent = fmt(decision.actual_position ?? result.position?.prev);
  $('orderDelta').textContent = fmt(decision.order_delta);
  $('confidenceLabel').textContent = fmt(decision.confidence_label || result.confidence);

  renderKV('trendList', [
    ['状态', trend.state],
    ['基础仓位', trend.base_target_position],
    ['仓位边界', `${fmt(trend.position_floor)} - ${fmt(trend.position_cap)}`],
    ['上一仓位', trend.previous_position],
    ['短上轨 T-1', keyLines.short_upper_prev],
    ['短下轨 T-1', keyLines.short_lower_prev],
    ['长上轨 T-1', keyLines.long_upper_prev],
    ['长下轨 T-1', keyLines.long_lower_prev],
    ['短中轨斜率', quality.short_mid_slope],
    ['ATR%', quality.atr_pct],
  ]);

  renderKV('structureList', [
    ['修边', structure.adjustment],
    ['偏向', structure.bias],
    ['活跃周期', structure.active_periods],
    ['最强事件', structure.strongest_event],
    ['最高周期事件', structure.highest_timeframe_event],
    ['共振数量', structure.resonance_count],
    ['共振权重', structure.resonance_weight],
    ['提示', structure.warnings],
  ]);

  renderKV('sequenceList', [
    ['高九', sequence.high9_active],
    ['低九', sequence.low9_active],
    ['Probe', sequence.probe],
    ['历史极值附近', sequence.near_historical_extreme],
    ['执行规则', sequence.execution_rules],
  ]);

  $('principle').textContent = fmt(decision.principle);
  $('forbiddenActions').textContent = fmt(decision.forbidden_actions);
  $('invalidation').textContent = fmt(decision.invalidation || decision.no_trade_condition);
  renderList($('explanation'), plan.explanation || result.explanation || []);

  loadDataStatus(false);
  loadChartData(false);
}

async function analyzeStock() {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  state.stock = stock;
  setBusy(true);
  setMessage('计算中');
  try {
    const resp = await fetch('/api/stock/decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock, interval: 'daily' }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '分析失败');
    }
    const first = (payload.results || [])[0];
    if (!first) throw new Error('无结果');
    if (first.error) throw new Error(first.error);
    updateDashboard(first);
    setMessage('');
  } catch (err) {
    setMessage(err.message || String(err), 'error');
    $('jsonOutput').textContent = JSON.stringify({ error: err.message || String(err) }, null, 2);
  } finally {
    setBusy(false);
  }
}

async function registerStock() {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  setBusy(true);
  setMessage('注册中');
  try {
    const resp = await fetch('/api/stock/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '注册失败');
    }
    setMessage(payload.message || '已注册');
    await loadWatchlist();
    await loadDataStatus(false);
  } catch (err) {
    setMessage(err.message || String(err), 'error');
  } finally {
    setBusy(false);
  }
}

async function registerStockByName() {
  const name = $('stockNameInput').value.trim();
  const market = $('stockMarketSelect').value;
  const code = $('stockCodeInput').value.trim();
  if (!name || !code) {
    setMessage('名称和代码不能为空', 'error');
    return;
  }
  setBusy(true);
  setMessage('保存并注册中');
  try {
    const resp = await fetch('/api/stock/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, market, code }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '注册失败');
    }
    $('stockInput').value = name;
    state.stock = name;
    setMessage(payload.message || '已注册');
    await loadWatchlist();
    await loadDataStatus(false);
  } catch (err) {
    setMessage(err.message || String(err), 'error');
  } finally {
    setBusy(false);
  }
}

function renderDataStatus(result) {
  const rows = [];
  if (!result) {
    rows.push(['状态', '暂无']);
  } else if (result.error) {
    rows.push(['状态', result.error]);
    rows.push(['注册', result.registered]);
  } else {
    rows.push(['注册', result.registered]);
    rows.push(['5min 行数', result.rows]);
    rows.push(['日线数量', result.daily_bars]);
    rows.push(['开始', result.first_timestamp ? result.first_timestamp.slice(0, 10) : '--']);
    rows.push(['结束', result.last_timestamp ? result.last_timestamp.slice(0, 10) : '--']);
    rows.push(['数据源', `${result.data_source || '--'}${result.free_mode ? ' / free' : ''}`]);
    if (result.refresh_api_budget) rows.push(['刷新 API', apiBudgetText(result.refresh_api_budget)]);
    if (result.api_budget) rows.push(['本次 API', apiBudgetText(result.api_budget)]);
    if (result.rows_inserted !== undefined) rows.push(['新增行', result.rows_inserted]);
    if (result.inserted_rows !== undefined) rows.push(['补入行', result.inserted_rows]);
    if (result.updated_rows !== undefined) rows.push(['覆盖行', result.updated_rows]);
    if (result.source_trading_days !== undefined) rows.push(['源交易日', result.source_trading_days]);
    if (result.partial !== undefined) rows.push(['部分返回', result.partial]);
    if (result.strict_backfill) {
      const report = result.strict_report || {};
      const quality = result.quality_report || report.quality_report || {};
      const minute = quality.minute || {};
      const daily = quality.daily_check || {};
      rows.push(['严格补数', 'AkShare 分窗口']);
      rows.push(['请求窗口', `${fmt(report.completed_windows, '0')}/${fmt(report.window_count, '0')}`]);
      rows.push(['请求次数', report.request_count]);
      rows.push(['分钟缺口', minute.issue_count || 0]);
      const dailyText = daily.checked
        ? `${daily.issue_count || 0} 个问题`
        : (daily.skipped ? '已跳过' : (daily.error ? '失败' : '--'));
      rows.push(['日线校验', dailyText]);
    }
    if (result.warning) rows.push(['提示', result.warning]);
  }
  renderKV('dataStatusList', rows);
}

function renderApiBudgetFromEstimate(payload) {
  const el = $('apiBudget');
  if (!el) return;
  const budget = payload?.api_budget || payload?.results?.[0]?.api_budget;
  if (!budget) {
    el.textContent = '补历史预计 API: --';
    return;
  }
  const days = payload?.results?.[0]?.requested_trading_days || $('backfillDaysInput')?.value || '--';
  el.textContent = `补 ${days} 个交易日预计 API: ${apiBudgetText(budget)}`;
}

async function loadBackfillEstimate() {
  const stock = $('stockInput').value.trim();
  const days = Math.max(1, Math.min(2500, Number($('backfillDaysInput').value || 200)));
  if (!stock) return;
  try {
    const resp = await fetch(`/api/stock/backfill-estimate?stock=${encodeURIComponent(stock)}&days=${encodeURIComponent(days)}`);
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) throw new Error(payload.message || '估算失败');
    renderApiBudgetFromEstimate(payload);
  } catch {
    renderApiBudgetFromEstimate(null);
  }
}

async function loadDataStatus(showMessage = false) {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  try {
    const resp = await fetch(`/api/stock/data-status?stock=${encodeURIComponent(stock)}`);
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '数据状态查询失败');
    }
    renderDataStatus((payload.results || [])[0]);
    await loadBackfillEstimate();
    if (showMessage) setMessage('');
  } catch (err) {
    renderDataStatus({ error: err.message || String(err) });
    if (showMessage) setMessage(err.message || String(err), 'error');
  }
}

async function refreshStockData() {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  setBusy(true);
  setMessage('刷新数据中');
  try {
    const resp = await fetch('/api/stock/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '刷新失败');
    }
    const first = (payload.results || [])[0];
    renderDataStatus(first);
    if (first?.error) throw new Error(first.error);
    const budget = first.api_budget ? `；消耗预计 ${apiBudgetText(first.api_budget)}` : '';
    setMessage(`刷新完成，新增 ${fmt(first.rows_inserted, '0')} 行${budget}`);
    await loadChartData(false);
    await loadBackfillEstimate();
  } catch (err) {
    setMessage(err.message || String(err), 'error');
  } finally {
    setBusy(false);
  }
}

async function clearStockData() {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  if (!window.confirm(`确认清理 ${stock} 的本地 5min 数据？注册记录会保留。`)) return;
  setBusy(true);
  setMessage('清理数据中');
  try {
    const resp = await fetch('/api/stock/clear-data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '清理数据失败');
    }
    const first = (payload.results || [])[0];
    renderDataStatus(first);
    setMessage(`清理完成，删除 ${fmt(payload.rows_cleared, '0')} 行本地数据`);
    await loadChartData(false);
    await loadBackfillEstimate();
  } catch (err) {
    setMessage(err.message || String(err), 'error');
  } finally {
    setBusy(false);
  }
}

async function unregisterCurrentStock() {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  if (!window.confirm(`确认取消注册 ${stock}？本地数据不会被删除。`)) return;
  setBusy(true);
  setMessage('取消注册中');
  try {
    const resp = await fetch('/api/stock/unregister', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock, clear_data: false }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '取消注册失败');
    }
    renderDataStatus((payload.results || [])[0]);
    setMessage(`取消注册完成，删除 ${fmt(payload.deleted, '0')} 条注册记录`);
    await loadWatchlist();
  } catch (err) {
    setMessage(err.message || String(err), 'error');
  } finally {
    setBusy(false);
  }
}

async function backfillStockData() {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  const days = Math.max(1, Math.min(2500, Number($('backfillDaysInput').value || 200)));
  $('backfillDaysInput').value = days;
  setBusy(true);
  setMessage('补历史中');
  try {
    const resp = await fetch('/api/stock/backfill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock, days, queued: true }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '补历史失败');
    }
    if (payload.queued) {
      renderApiBudgetFromEstimate(payload.estimate);
      setMessage(`补历史任务已入队，预计 API ${apiBudgetText(payload.estimate?.api_budget)}`);
      activateTab('tasks');
      await loadDataJobs(payload.job_id);
      pollDataJob(payload.job_id);
      return;
    }
    const first = (payload.results || [])[0];
    renderDataStatus(first);
    if (first?.error) throw new Error(first.error);
    const inserted = fmt(first.inserted_rows, '0');
    const updated = fmt(first.updated_rows, '0');
    const partial = first.partial ? '；数据源只返回了部分窗口' : '';
    const message = first.warning || `补历史完成，新增 ${inserted} 行，覆盖 ${updated} 行${partial}`;
    setMessage(message);
    await loadChartData(false);
    await loadBackfillEstimate();
  } catch (err) {
    setMessage(err.message || String(err), 'error');
  } finally {
    setBusy(false);
  }
}

function pollDataJob(jobId) {
  if (!jobId) return;
  if (state.dataJobPoll) {
    clearTimeout(state.dataJobPoll);
    state.dataJobPoll = null;
  }

  const poll = async () => {
    try {
      const resp = await fetch(`/api/data-jobs/${encodeURIComponent(jobId)}`);
      const payload = await resp.json();
      if (!resp.ok || payload.success === false) {
        throw new Error(payload.message || '任务查询失败');
      }
      const job = payload.job;
      await loadDataJobs(job.id);
      if (job.status === 'queued' || job.status === 'running') {
        const label = job.status === 'queued' ? '排队中' : '运行中';
        setMessage(`补历史任务${label}，进度 ${fmt(job.progress, '0')}%，成功 ${fmt(job.success_tasks, '0')}/${fmt(job.total_tasks, '0')}`);
        state.dataJobPoll = setTimeout(poll, 3000);
        return;
      }
      if (job.status === 'failed') {
        setMessage(job.error || '补历史任务失败，可在任务页重试失败 Task', 'error');
        return;
      }
      if (job.status === 'completed' || job.status === 'partial_failed') {
        const first = (job.result?.results || [])[0];
        renderDataStatus(first);
        if (first?.error) throw new Error(first.error);
        const inserted = fmt(first?.inserted_rows, '0');
        const updated = fmt(first?.updated_rows, '0');
        const partial = job.status === 'partial_failed' ? '；部分 Task 失败，可在任务页重试' : '';
        const message = first?.warning || `补历史完成，新增 ${inserted} 行，覆盖 ${updated} 行${partial}`;
        setMessage(message);
        await loadChartData(false);
        await loadBackfillEstimate();
      }
    } catch (err) {
      setMessage(err.message || String(err), 'error');
    }
  };
  poll();
}

function jobStatusLabel(status) {
  return {
    queued: '排队',
    running: '运行',
    completed: '完成',
    partial_failed: '部分失败',
    failed: '失败',
    pending: '等待',
    success: '成功',
    empty: '空返回',
    skipped: '已存在',
  }[status] || fmt(status);
}

function statusPill(status) {
  const span = document.createElement('span');
  span.className = `status-pill ${status || ''}`;
  span.textContent = jobStatusLabel(status);
  return span;
}

function progressBar(value) {
  const track = document.createElement('div');
  track.className = 'progress-track';
  const fill = document.createElement('div');
  fill.className = 'progress-fill';
  fill.style.width = `${Math.max(0, Math.min(100, Number(value || 0)))}%`;
  track.appendChild(fill);
  return track;
}

async function loadDataJobs(selectJobId = null) {
  const resp = await fetch('/api/data-jobs?limit=50');
  const payload = await resp.json();
  if (!resp.ok || payload.success === false) {
    throw new Error(payload.message || '任务列表查询失败');
  }
  state.dataJobs = payload.jobs || [];
  if (selectJobId) state.selectedJobId = selectJobId;
  if (!state.selectedJobId && state.dataJobs.length) {
    state.selectedJobId = state.dataJobs[0].id;
  }
  renderDataJobs();
  const selected = state.dataJobs.find((job) => job.id === state.selectedJobId);
  if (selected) {
    await loadDataJobDetail(selected.id);
  } else {
    renderDataJobDetail(null);
  }
}

function renderDataJobs() {
  const root = $('dataJobList');
  if (!root) return;
  root.textContent = '';
  if (!state.dataJobs.length) {
    root.textContent = '暂无任务';
    return;
  }
  state.dataJobs.forEach((job) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `data-job-card ${job.id === state.selectedJobId ? 'is-active' : ''}`;
    const title = document.createElement('strong');
    title.textContent = `${job.stock || '--'} / ${job.type || '--'}`;
    const meta = document.createElement('div');
    meta.className = 'job-card-meta';
    meta.append(statusPill(job.status));
    const progress = document.createElement('span');
    progress.textContent = `${fmt(job.progress, '0')}%`;
    const count = document.createElement('span');
    count.textContent = `成功 ${fmt(job.success_tasks, '0')}/${fmt(job.total_tasks, '0')}`;
    const time = document.createElement('span');
    time.textContent = shortDate(job.updated_at || job.created_at);
    meta.append(progress, count, time);
    btn.append(title, progressBar(job.progress), meta);
    btn.addEventListener('click', async () => {
      state.selectedJobId = job.id;
      renderDataJobs();
      await loadDataJobDetail(job.id);
    });
    root.appendChild(btn);
  });
}

async function loadDataJobDetail(jobId) {
  const resp = await fetch(`/api/data-jobs/${encodeURIComponent(jobId)}`);
  const payload = await resp.json();
  if (!resp.ok || payload.success === false) {
    throw new Error(payload.message || '任务详情查询失败');
  }
  renderDataJobDetail(payload.job);
}

function renderDataJobDetail(job) {
  const detail = $('dataJobDetail');
  const body = $('dataTaskTableBody');
  if (!detail || !body) return;
  detail.textContent = '';
  body.textContent = '';
  if (!job) {
    detail.textContent = '请选择一个 Job';
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 8;
    cell.textContent = '暂无 Task';
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  const meta = document.createElement('div');
  meta.className = 'task-meta';
  meta.append(statusPill(job.status));
  [
    `进度 ${fmt(job.progress, '0')}%`,
    `成功 ${fmt(job.success_tasks, '0')}`,
    `已存在 ${fmt(job.skipped_tasks, '0')}`,
    `失败 ${fmt(job.failed_tasks, '0')}`,
    `空返回 ${fmt(job.empty_tasks, '0')}`,
    `新增 ${fmt(job.inserted_rows, '0')}`,
    `覆盖 ${fmt(job.updated_rows, '0')}`,
  ].forEach((text) => {
    const span = document.createElement('span');
    span.textContent = text;
    meta.appendChild(span);
  });
  detail.appendChild(meta);

  const tasks = job.tasks || [];
  if (!tasks.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 8;
    cell.textContent = '暂无 Task';
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  tasks.forEach((task) => {
    const row = document.createElement('tr');
    const retryable = task.status === 'failed' || task.status === 'empty';
    const sourceSelect = document.createElement('select');
    sourceSelect.className = 'task-source-select';
    (task.available_sources || []).forEach((source) => {
      const opt = document.createElement('option');
      opt.value = source.name;
      opt.textContent = `${source.name}${source.configured === false ? ' / 未配置' : ''}`;
      opt.selected = source.name === task.source;
      sourceSelect.appendChild(opt);
    });
    sourceSelect.disabled = !retryable;

    const retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.textContent = '重试';
    retryBtn.disabled = !retryable;
    retryBtn.addEventListener('click', () => retryDataTask(job.id, task.id, sourceSelect.value));

    [
      task.seq,
      `${fullDate(task.start_date)} - ${fullDate(task.end_date)}`,
      sourceSelect,
      statusPill(task.status),
      fmt(task.rows, '0'),
      `${fmt(task.inserted_rows, '0')} / ${fmt(task.updated_rows, '0')}`,
      task.error_summary || task.skip_reason || '--',
      retryBtn,
    ].forEach((value, idx) => {
      const td = document.createElement('td');
      if (value instanceof HTMLElement) td.appendChild(value);
      else td.textContent = value;
      if (idx === 6) td.className = 'task-error';
      row.appendChild(td);
    });
    body.appendChild(row);
  });
}

async function retryDataTask(jobId, taskId, source) {
  try {
    const resp = await fetch(`/api/data-jobs/${encodeURIComponent(jobId)}/tasks/${encodeURIComponent(taskId)}/retry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || 'Task 重试失败');
    }
    setMessage(`Task 已使用 ${source} 重新入队`);
    await loadDataJobs(jobId);
    pollDataJob(jobId);
  } catch (err) {
    setMessage(err.message || String(err), 'error');
  }
}

function canvasContext(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const surface = canvas.parentElement;
  const rect = surface.getBoundingClientRect();
  const computed = window.getComputedStyle(surface);
  const borderX = parseFloat(computed.borderLeftWidth || 0) + parseFloat(computed.borderRightWidth || 0);
  const width = Math.max(320, Math.floor(rect.width - borderX));
  const height = Number(canvas.getAttribute('height')) || 260;
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  const nextWidth = Math.floor(width * dpr);
  const nextHeight = Math.floor(height * dpr);
  if (canvas.width !== nextWidth) canvas.width = nextWidth;
  if (canvas.height !== nextHeight) canvas.height = nextHeight;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

function drawEmpty(canvas, text) {
  const { ctx, width, height } = canvasContext(canvas);
  ctx.fillStyle = '#6d7168';
  ctx.font = '13px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(text, width / 2, height / 2);
}

function drawGrid(ctx, width, height, pad) {
  ctx.strokeStyle = '#eceee7';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + ((height - pad.top - pad.bottom) * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
  }
}

function scaleY(value, min, max, pad, height) {
  if (max === min) return pad.top + (height - pad.top - pad.bottom) / 2;
  const plotH = height - pad.top - pad.bottom;
  return pad.top + (max - value) * plotH / (max - min);
}

function drawPriceChart() {
  const canvas = $('priceCanvas');
  const chart = state.chartData;
  if (!chart) {
    drawEmpty(canvas, '暂无图表');
    return;
  }

  const interval = state.chartInterval;
  const visible = visibleChartSource(interval);
  const source = visible.source;
  const candles = source?.candles || [];
  if (!candles.length) {
    drawEmpty(canvas, `${interval} 数据不足`);
    return;
  }

  const { ctx, width, height } = canvasContext(canvas);
  const pad = { left: 54, right: 18, top: 18, bottom: 30 };
  const plotW = width - pad.left - pad.right;
  const step = plotW / candles.length;
  const trendByTime = new Map((visible.daily.trend || []).map((x) => [x.time, x]));
  const decisionByTime = new Map((visible.daily.decisions || []).map((x) => [x.time, x]));
  const sequenceByTime = new Map((visible.daily.sequence || []).map((x) => [x.time, x]));
  const structureByTime = new Map((source.structure || []).map((x) => [x.time, x]));
  const values = [];
  candles.forEach((c) => {
    values.push(c.high, c.low);
    if (interval === 'daily') {
      const tr = trendByTime.get(c.time);
      if (tr) values.push(tr.short_upper, tr.short_lower, tr.long_upper, tr.long_lower);
    }
  });
  const clean = values.filter((v) => typeof v === 'number');
  let min = Math.min(...clean);
  let max = Math.max(...clean);
  const margin = (max - min || max * 0.01 || 1) * 0.08;
  min -= margin;
  max += margin;

  drawGrid(ctx, width, height, pad);
  ctx.font = '11px sans-serif';
  ctx.fillStyle = '#6d7168';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i += 1) {
    const value = max - ((max - min) * i) / 4;
    const y = pad.top + ((height - pad.top - pad.bottom) * i) / 4;
    ctx.fillText(fmt(value), pad.left - 6, y + 4);
  }

  candles.forEach((c, i) => {
    const x = pad.left + i * step + step / 2;
    const yOpen = scaleY(c.open, min, max, pad, height);
    const yClose = scaleY(c.close, min, max, pad, height);
    const yHigh = scaleY(c.high, min, max, pad, height);
    const yLow = scaleY(c.low, min, max, pad, height);
    const up = c.close >= c.open;
    ctx.strokeStyle = up ? '#1d8f63' : '#c84635';
    ctx.fillStyle = up ? '#1d8f63' : '#c84635';
    ctx.beginPath();
    ctx.moveTo(x, yHigh);
    ctx.lineTo(x, yLow);
    ctx.stroke();
    const bodyW = Math.max(2, Math.min(step * 0.62, 10));
    const bodyY = Math.min(yOpen, yClose);
    const bodyH = Math.max(1, Math.abs(yClose - yOpen));
    ctx.fillRect(x - bodyW / 2, bodyY, bodyW, bodyH);

    if (interval === 'daily') {
      const decision = decisionByTime.get(c.time);
      if (decision?.action === 'BUY' || decision?.action === 'SELL') {
        ctx.fillStyle = decision.action === 'BUY' ? '#1d8f63' : '#c84635';
        const markerY = decision.action === 'BUY' ? yLow + 13 : yHigh - 13;
        ctx.beginPath();
        ctx.arc(x, markerY, 4, 0, Math.PI * 2);
        ctx.fill();
      }
      const seq = sequenceByTime.get(c.time);
      if (seq?.high9_signal || seq?.low9_signal) {
        ctx.fillStyle = '#b7791f';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(seq.high9_signal ? 'H9' : 'L9', x, seq.high9_signal ? yHigh - 18 : yLow + 22);
      }
    } else {
      const st = structureByTime.get(c.time);
      if (st?.top_75 || st?.top_100 || st?.bottom_75 || st?.bottom_100) {
        ctx.fillStyle = (st.top_75 || st.top_100) ? '#c84635' : '#1d8f63';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        const label = st.top_100 || st.bottom_100 ? '100' : '75';
        ctx.fillText(label, x, (st.top_75 || st.top_100) ? yHigh - 14 : yLow + 18);
      }
    }
  });

  if (interval === 'daily') {
    [
      ['short_upper', '#c84635'],
      ['short_lower', '#1d8f63'],
      ['long_upper', '#b7791f'],
      ['long_lower', '#2b6f8f'],
    ].forEach(([key, color]) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = key.startsWith('long') ? 1 : 1.5;
      ctx.setLineDash(key.startsWith('long') ? [4, 4] : []);
      ctx.beginPath();
      let started = false;
      candles.forEach((c, i) => {
        const tr = trendByTime.get(c.time);
        const value = tr?.[key];
        if (typeof value !== 'number') return;
        const x = pad.left + i * step + step / 2;
        const y = scaleY(value, min, max, pad, height);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      });
      ctx.stroke();
      ctx.setLineDash([]);
    });
  }

  ctx.fillStyle = '#6d7168';
  ctx.font = '11px sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(shortDate(candles[0].time), pad.left, height - 10);
  ctx.textAlign = 'right';
  ctx.fillText(shortDate(candles[candles.length - 1].time), width - pad.right, height - 10);
}

function drawMacdChart() {
  const canvas = $('macdCanvas');
  const chart = state.chartData;
  if (!chart) {
    drawEmpty(canvas, '暂无 MACD');
    return;
  }
  const macdInterval = state.chartInterval;
  const source = visibleChartSource(macdInterval).source;
  const macd = source?.macd || [];
  if (!macd.length) {
    drawEmpty(canvas, `${macdInterval} MACD 数据不足`);
    return;
  }

  const { ctx, width, height } = canvasContext(canvas);
  const pad = { left: 54, right: 18, top: 18, bottom: 28 };
  const plotW = width - pad.left - pad.right;
  const step = plotW / macd.length;
  const values = macd.flatMap((m) => [m.dif, m.dea, m.hist]).filter((v) => typeof v === 'number');
  let min = Math.min(...values, 0);
  let max = Math.max(...values, 0);
  const margin = (max - min || 1) * 0.12;
  min -= margin;
  max += margin;
  const zeroY = scaleY(0, min, max, pad, height);

  drawGrid(ctx, width, height, pad);
  ctx.strokeStyle = '#daded2';
  ctx.beginPath();
  ctx.moveTo(pad.left, zeroY);
  ctx.lineTo(width - pad.right, zeroY);
  ctx.stroke();

  macd.forEach((m, i) => {
    const x = pad.left + i * step + step / 2;
    const y = scaleY(m.hist || 0, min, max, pad, height);
    ctx.fillStyle = (m.hist || 0) >= 0 ? '#1d8f63' : '#c84635';
    ctx.fillRect(x - Math.max(1, step * 0.25), Math.min(y, zeroY), Math.max(1, step * 0.5), Math.abs(y - zeroY));
  });

  [
    ['dif', '#2b6f8f'],
    ['dea', '#b7791f'],
  ].forEach(([key, color]) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    macd.forEach((m, i) => {
      const x = pad.left + i * step + step / 2;
      const y = scaleY(m[key], min, max, pad, height);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  $('macdChartLabel').textContent = `${macdInterval} / MACD / 结构`;
  $('fallbackMacdChartLabel').textContent = `${macdInterval} / MACD / 结构`;
}

function readChartDays() {
  const input = $('chartDaysInput');
  const raw = Number(input?.value || state.chartDays || 60);
  const value = Math.max(1, Math.min(500, Number.isFinite(raw) ? Math.floor(raw) : 60));
  if (input) input.value = value;
  state.chartDays = value;
  return value;
}

function visibleDateKeys() {
  const daily = state.chartData?.daily?.candles || [];
  const limit = readChartDays();
  const keys = [];
  daily.forEach((item) => {
    const key = dateKey(item.time);
    if (key && keys[keys.length - 1] !== key) keys.push(key);
  });
  return new Set(keys.slice(Math.max(0, keys.length - limit)));
}

function filterByVisibleDays(items, keys) {
  return (items || []).filter((item) => keys.has(dateKey(item.time)));
}

function visibleChartSource(interval) {
  const keys = visibleDateKeys();
  const dailyRaw = state.chartData?.daily || {};
  const daily = {
    candles: filterByVisibleDays(dailyRaw.candles, keys),
    trend: filterByVisibleDays(dailyRaw.trend, keys),
    sequence: filterByVisibleDays(dailyRaw.sequence, keys),
    decisions: filterByVisibleDays(dailyRaw.decisions, keys),
    macd: filterByVisibleDays(dailyRaw.macd, keys),
  };
  if (interval === 'daily') {
    return { source: daily, daily, keys };
  }
  const raw = state.chartData?.intraday?.[interval] || {};
  const source = {
    candles: filterByVisibleDays(raw.candles, keys),
    macd: filterByVisibleDays(raw.macd, keys),
    structure: filterByVisibleDays(raw.structure, keys),
  };
  return { source, daily, keys };
}

function hasLightweightCharts() {
  return Boolean(window.LightweightCharts?.createChart);
}

function intervalLabel(interval) {
  return {
    daily: '日线',
    '60min': '60m',
    '90min': '90m',
    '120min': '120m',
  }[interval] || interval;
}

function chartTime(value, interval = state.chartInterval) {
  if (interval === 'daily') return dateKey(value);
  const parsed = Date.parse(value);
  if (!Number.isNaN(parsed)) return Math.floor(parsed / 1000);
  const fallback = Date.parse(`${dateKey(value)}T00:00:00`);
  return Number.isNaN(fallback) ? String(value) : Math.floor(fallback / 1000);
}

function chartTimeKey(time) {
  if (time === null || time === undefined) return '';
  if (typeof time === 'number') return String(time);
  if (typeof time === 'string') return time;
  if (typeof time === 'object' && time.year && time.month && time.day) {
    return `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}`;
  }
  return String(time);
}

function chartItemKey(item, interval) {
  return chartTimeKey(chartTime(item?.time, interval));
}

function compareChartTime(a, b) {
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b));
}

function toCandleSeries(candles, interval) {
  return (candles || []).map((item) => ({
    time: chartTime(item.time, interval),
    open: Number(item.open),
    high: Number(item.high),
    low: Number(item.low),
    close: Number(item.close),
  })).filter((item) => (
    Number.isFinite(item.open)
    && Number.isFinite(item.high)
    && Number.isFinite(item.low)
    && Number.isFinite(item.close)
  ));
}

function toLineSeries(items, key, interval) {
  return (items || []).map((item) => ({
    time: chartTime(item.time, interval),
    value: Number(item[key]),
  })).filter((item) => Number.isFinite(item.value));
}

function toVolumeSeries(candles, interval) {
  return (candles || []).map((item) => {
    const up = Number(item.close) >= Number(item.open);
    return {
      time: chartTime(item.time, interval),
      value: Number(item.volume || 0),
      color: up ? 'rgba(29, 143, 99, 0.34)' : 'rgba(200, 70, 53, 0.34)',
    };
  }).filter((item) => Number.isFinite(item.value));
}

function toMacdSeries(macd, interval) {
  const hist = [];
  const dif = [];
  const dea = [];
  (macd || []).forEach((item) => {
    const time = chartTime(item.time, interval);
    const histValue = Number(item.hist);
    const difValue = Number(item.dif);
    const deaValue = Number(item.dea);
    if (Number.isFinite(histValue)) {
      hist.push({
        time,
        value: histValue,
        color: histValue >= 0 ? 'rgba(29, 143, 99, 0.55)' : 'rgba(200, 70, 53, 0.55)',
      });
    }
    if (Number.isFinite(difValue)) dif.push({ time, value: difValue });
    if (Number.isFinite(deaValue)) dea.push({ time, value: deaValue });
  });
  return { hist, dif, dea };
}

function buildPriceMarkers(source, interval) {
  const markers = [];
  if (interval === 'daily') {
    (source.decisions || []).forEach((item) => {
      if (item.action !== 'BUY' && item.action !== 'SELL') return;
      markers.push({
        time: chartTime(item.time, interval),
        position: item.action === 'BUY' ? 'belowBar' : 'aboveBar',
        color: item.action === 'BUY' ? '#1d8f63' : '#c84635',
        shape: item.action === 'BUY' ? 'arrowUp' : 'arrowDown',
        text: item.action,
      });
    });
    (source.sequence || []).forEach((item) => {
      if (!item.high9_signal && !item.low9_signal) return;
      markers.push({
        time: chartTime(item.time, interval),
        position: item.low9_signal ? 'belowBar' : 'aboveBar',
        color: '#b7791f',
        shape: 'circle',
        text: item.low9_signal ? 'L9' : 'H9',
      });
    });
  } else {
    (source.structure || []).forEach((item) => {
      const top = item.top_75 || item.top_100;
      const bottom = item.bottom_75 || item.bottom_100;
      if (!top && !bottom) return;
      markers.push({
        time: chartTime(item.time, interval),
        position: bottom ? 'belowBar' : 'aboveBar',
        color: bottom ? '#1d8f63' : '#c84635',
        shape: bottom ? 'arrowUp' : 'arrowDown',
        text: item.top_100 || item.bottom_100 ? '100' : '75',
      });
    });
  }
  return markers.sort((a, b) => compareChartTime(a.time, b.time));
}

function destroyTradingCharts() {
  if (state.tv.priceChart) state.tv.priceChart.remove();
  if (state.tv.macdChart) state.tv.macdChart.remove();
  state.tv.priceChart = null;
  state.tv.macdChart = null;
  state.tv.candleSeries = null;
  if (state.tv.signalTooltip) {
    state.tv.signalTooltip.remove();
    state.tv.signalTooltip = null;
  }
}

function destroyBacktestCharts() {
  if (state.btRenderFrame) {
    cancelAnimationFrame(state.btRenderFrame);
    state.btRenderFrame = null;
  }
  (state.btCharts || []).forEach((chart) => {
    try {
      chart.remove();
    } catch {
      // ignore chart cleanup races
    }
  });
  state.btCharts = [];
}

function chartOptions(container, height, showTimeScale) {
  const tv = window.LightweightCharts;
  return {
    width: Math.max(320, Math.floor(container.clientWidth || 0)),
    height,
    layout: {
      background: { type: tv.ColorType?.Solid || 'solid', color: '#ffffff' },
      textColor: '#20221f',
      fontSize: 12,
    },
    grid: {
      vertLines: { color: '#eef0ea' },
      horzLines: { color: '#eef0ea' },
    },
    crosshair: {
      mode: tv.CrosshairMode?.Normal ?? 0,
      vertLine: { color: '#7b8374', width: 1, style: 3, labelBackgroundColor: '#2d312b' },
      horzLine: { color: '#7b8374', width: 1, style: 3, labelBackgroundColor: '#2d312b' },
    },
    rightPriceScale: {
      borderColor: '#daded2',
      scaleMargins: { top: 0.08, bottom: 0.08 },
    },
    leftPriceScale: { visible: false },
    timeScale: {
      visible: showTimeScale,
      borderColor: '#daded2',
      timeVisible: state.chartInterval !== 'daily',
      secondsVisible: false,
    },
    localization: {
      locale: 'zh-CN',
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
  };
}

function syncTimeScales(primary, secondary) {
  let syncing = false;
  primary.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncing || !range) return;
    syncing = true;
    secondary.timeScale().setVisibleLogicalRange(range);
    syncing = false;
  });
  secondary.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncing || !range) return;
    syncing = true;
    primary.timeScale().setVisibleLogicalRange(range);
    syncing = false;
  });
}

function renderChartLegend(snapshot) {
  const root = $('chartLegend');
  if (!root) return;
  root.textContent = '';
  if (!snapshot?.candle) {
    root.textContent = '暂无数据';
    return;
  }
  const candle = snapshot.candle;
  const macd = snapshot.macd || {};
  const trend = snapshot.trend || {};
  const closeColor = Number(candle.close) >= Number(candle.open) ? 'up' : 'down';
  const histValue = Number(macd.hist);
  const histColor = Number.isFinite(histValue) ? (histValue >= 0 ? 'up' : 'down') : undefined;
  const items = [
    ['时间', snapshot.label || shortDate(candle.time)],
    ['O', fmt(candle.open)],
    ['H', fmt(candle.high)],
    ['L', fmt(candle.low)],
    ['C', fmt(candle.close), closeColor],
    ['V', fmtMoney(candle.volume || 0)],
  ];
  if (snapshot.trend) {
    items.push(
      ['短上轨', fmt(trend.short_upper), 'trend-red'],
      ['短下轨', fmt(trend.short_lower), 'trend-green'],
      ['长上轨', fmt(trend.long_upper), 'trend-amber'],
      ['长下轨', fmt(trend.long_lower), 'trend-blue'],
    );
  }
  items.push(
    ['DIF', fmt(macd.dif)],
    ['DEA', fmt(macd.dea)],
    ['Hist', fmt(macd.hist), histColor],
  );
  items.forEach(([label, value, tone]) => {
    const item = document.createElement('span');
    if (tone) item.className = tone;
    item.textContent = `${label} ${value}`;
    root.appendChild(item);
  });
}

function signalReasonText(decision) {
  if (!decision) return '';
  const direction = decision.action === 'BUY' ? '买入' : '卖出';
  return decision.reason || [
    `${direction}信号`,
    decision.actual_position !== null && decision.target_position !== null
      ? `目标仓位 ${fmt(decision.actual_position)} -> ${fmt(decision.target_position)}`
      : '',
    decision.order_delta !== null ? `订单差值 ${fmt(decision.order_delta)}` : '',
    decision.trend_state ? `趋势 ${decision.trend_state}` : '',
    decision.confidence_label ? `置信 ${decision.confidence_label}` : '',
    decision.principle || '',
  ].filter(Boolean).join('；');
}

function getSignalTooltip() {
  if (state.tv.signalTooltip?.isConnected) return state.tv.signalTooltip;
  const pane = document.querySelector('.price-pane');
  if (!pane) return null;
  const tooltip = document.createElement('div');
  tooltip.id = 'signalTooltip';
  tooltip.className = 'signal-tooltip';
  tooltip.hidden = true;
  pane.appendChild(tooltip);
  state.tv.signalTooltip = tooltip;
  return tooltip;
}

function hideSignalTooltip() {
  if (state.tv.signalTooltip) state.tv.signalTooltip.hidden = true;
}

function showSignalTooltip(param, decision) {
  const tooltip = getSignalTooltip();
  const pane = document.querySelector('.price-pane');
  const surface = $('priceChart');
  if (!tooltip || !pane || !surface || !param?.point || !decision) {
    hideSignalTooltip();
    return;
  }

  tooltip.textContent = '';
  tooltip.className = `signal-tooltip ${String(decision.action || '').toLowerCase()}`;
  const title = document.createElement('strong');
  title.textContent = `${dateKey(decision.time)} ${decision.action}`;
  const body = document.createElement('span');
  body.textContent = signalReasonText(decision);
  tooltip.append(title, body);
  tooltip.hidden = false;
  tooltip.style.left = '0px';
  tooltip.style.top = '0px';

  const rawLeft = surface.offsetLeft + Number(param.point.x || 0);
  const rawTop = surface.offsetTop + Number(param.point.y || 0);
  const width = tooltip.offsetWidth || 260;
  const height = tooltip.offsetHeight || 72;
  const minLeft = 12 + width / 2;
  const maxLeft = Math.max(minLeft, pane.clientWidth - 12 - width / 2);
  const left = Math.min(Math.max(rawLeft, minLeft), maxLeft);
  let top = rawTop - height - 12;
  if (top < surface.offsetTop + 8) top = rawTop + 18;
  top = Math.min(top, Math.max(surface.offsetTop + 8, pane.clientHeight - height - 8));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function renderTradingCharts(visible) {
  const stack = $('chartStack');
  if (!hasLightweightCharts()) {
    stack.classList.add('use-canvas');
    destroyTradingCharts();
    drawPriceChart();
    drawMacdChart();
    return;
  }
  stack.classList.remove('use-canvas');
  destroyTradingCharts();

  const interval = state.chartInterval;
  const source = visible.source || {};
  const candles = source.candles || [];
  const macd = source.macd || [];
  const candleData = toCandleSeries(candles, interval);
  if (!candleData.length) {
    renderChartLegend(null);
    return;
  }

  const priceContainer = $('priceChart');
  const macdContainer = $('macdChart');
  const priceHeight = Math.max(260, Math.floor(priceContainer.clientHeight || 420));
  const macdHeight = Math.max(120, Math.floor(macdContainer.clientHeight || 180));
  const tv = window.LightweightCharts;
  const priceChart = tv.createChart(priceContainer, chartOptions(priceContainer, priceHeight, false));
  const macdChart = tv.createChart(macdContainer, chartOptions(macdContainer, macdHeight, true));

  const candleSeries = priceChart.addCandlestickSeries({
    upColor: '#1d8f63',
    downColor: '#c84635',
    borderUpColor: '#1d8f63',
    borderDownColor: '#c84635',
    wickUpColor: '#1d8f63',
    wickDownColor: '#c84635',
    priceLineColor: '#2d312b',
  });
  candleSeries.setData(candleData);
  candleSeries.setMarkers(buildPriceMarkers(source, interval));
  candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.23 } });

  const volumeSeries = priceChart.addHistogramSeries({
    priceScaleId: '',
    priceFormat: { type: 'volume' },
    priceLineVisible: false,
    lastValueVisible: false,
  });
  volumeSeries.setData(toVolumeSeries(candles, interval));
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

  if (interval === 'daily') {
    [
      ['short_upper', '#c84635', 2],
      ['short_lower', '#1d8f63', 2],
      ['long_upper', '#b7791f', 1],
      ['long_lower', '#2b6f8f', 1],
    ].forEach(([key, color, width]) => {
      const line = priceChart.addLineSeries({
        color,
        lineWidth: width,
        lineStyle: key.startsWith('long') ? (tv.LineStyle?.Dashed ?? 2) : (tv.LineStyle?.Solid ?? 0),
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(toLineSeries(source.trend, key, interval));
    });
  }

  const macdData = toMacdSeries(macd, interval);
  const histSeries = macdChart.addHistogramSeries({
    priceLineVisible: false,
    lastValueVisible: false,
    priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
  });
  histSeries.setData(macdData.hist);
  const difSeries = macdChart.addLineSeries({
    color: '#2b6f8f',
    lineWidth: 2,
    priceLineVisible: false,
  });
  difSeries.setData(macdData.dif);
  const deaSeries = macdChart.addLineSeries({
    color: '#b7791f',
    lineWidth: 2,
    priceLineVisible: false,
  });
  deaSeries.setData(macdData.dea);

  const candleByTime = new Map(candles.map((item) => [chartItemKey(item, interval), item]));
  const macdByTime = new Map(macd.map((item) => [chartItemKey(item, interval), item]));
  const trendByTime = new Map((source.trend || []).map((item) => [chartItemKey(item, 'daily'), item]));
  const signalByTime = new Map(
    (interval === 'daily' ? (source.decisions || []) : [])
      .filter((item) => item.action === 'BUY' || item.action === 'SELL')
      .map((item) => [chartItemKey(item, interval), item])
  );
  const latestCandle = candles[candles.length - 1];
  const latestKey = chartItemKey(latestCandle, interval);
  state.tv.latestSnapshot = {
    candle: latestCandle,
    macd: macdByTime.get(latestKey),
    trend: interval === 'daily' ? trendByTime.get(latestKey) : undefined,
    label: latestCandle?.time
      ? (interval === 'daily' ? dateKey(latestCandle.time) : latestCandle.time.replace('T', ' ').slice(0, 16))
      : '',
  };
  renderChartLegend(state.tv.latestSnapshot);

  const updateLegendAt = (time) => {
    if (!time) {
      renderChartLegend(state.tv.latestSnapshot);
      return;
    }
    const key = chartTimeKey(time);
    const candle = candleByTime.get(key);
    const itemMacd = macdByTime.get(key);
    const itemTrend = interval === 'daily' ? trendByTime.get(key) : undefined;
    renderChartLegend({
      candle,
      macd: itemMacd,
      trend: itemTrend,
      label: candle?.time
        ? (interval === 'daily' ? dateKey(candle.time) : candle.time.replace('T', ' ').slice(0, 16))
        : key,
    });
  };
  priceChart.subscribeCrosshairMove((param) => {
    updateLegendAt(param.time);
    const decision = signalByTime.get(chartTimeKey(param.time));
    if (decision) showSignalTooltip(param, decision);
    else hideSignalTooltip();
  });
  macdChart.subscribeCrosshairMove((param) => {
    updateLegendAt(param.time);
    hideSignalTooltip();
  });
  syncTimeScales(priceChart, macdChart);
  priceChart.timeScale().fitContent();
  macdChart.timeScale().fitContent();

  state.tv.priceChart = priceChart;
  state.tv.macdChart = macdChart;
  state.tv.candleSeries = candleSeries;
}

function resizeTradingCharts() {
  if (!state.tv.priceChart || !state.tv.macdChart) return;
  const priceContainer = $('priceChart');
  const macdContainer = $('macdChart');
  state.tv.priceChart.applyOptions({
    width: Math.max(320, Math.floor(priceContainer.clientWidth || 0)),
    height: Math.max(260, Math.floor(priceContainer.clientHeight || 420)),
  });
  state.tv.macdChart.applyOptions({
    width: Math.max(320, Math.floor(macdContainer.clientWidth || 0)),
    height: Math.max(120, Math.floor(macdContainer.clientHeight || 180)),
  });
}

function installChartResizeObserver() {
  if (!window.ResizeObserver || state.tv.resizeObserver) return;
  state.tv.resizeObserver = new ResizeObserver(() => {
    if (state.tv.resizeFrame) cancelAnimationFrame(state.tv.resizeFrame);
    state.tv.resizeFrame = requestAnimationFrame(() => {
      state.tv.resizeFrame = null;
      resizeTradingCharts();
      renderBacktestCharts();
    });
  });
  ['chartStack', 'priceChart', 'macdChart'].forEach((id) => {
    const el = $(id);
    if (el) state.tv.resizeObserver.observe(el);
  });
}

async function loadChartData(forceTab = true) {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  readChartDays();
  state.stock = stock;
  try {
    const resp = await fetch(`/api/stock/chart-data?stock=${encodeURIComponent(stock)}&bars=500`);
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '图表数据获取失败');
    }
    const first = (payload.results || [])[0];
    if (!first) throw new Error('无图表数据');
    if (first.error) throw new Error(first.error);
    state.chartData = first;
    $('chartStack').classList.add('has-data');
    renderCharts();
    if (forceTab) activateTab('chart');
  } catch (err) {
    destroyTradingCharts();
    renderChartLegend(null);
    $('chartStack').classList.remove('has-data');
    drawEmpty($('priceCanvas'), '暂无图表');
    drawEmpty($('macdCanvas'), '暂无 MACD');
    setMessage(err.message || String(err), 'error');
  }
}

function renderCharts() {
  if (!state.chartData) return;
  const interval = state.chartInterval;
  const visible = visibleChartSource(interval);
  const source = visible.source;
  const candles = source?.candles || [];
  const shownDays = visible.keys.size;
  const availableDays = new Set((state.chartData.daily?.candles || []).map((x) => dateKey(x.time))).size;
  const first = candles[0]?.time ? shortDate(candles[0].time) : '--';
  const last = candles[candles.length - 1]?.time ? shortDate(candles[candles.length - 1].time) : '--';
  $('chartTitle').textContent = `${state.chartData.display_code || state.stock} / ${interval}`;
  $('chartSymbol').textContent = state.chartData.display_code || state.stock || '--';
  $('chartIntervalLabel').textContent = intervalLabel(interval);
  $('priceChartLabel').textContent = `${interval} / ${shownDays}/${availableDays} 交易日 / ${candles.length} bars / ${first} - ${last}`;
  $('fallbackPriceChartLabel').textContent = $('priceChartLabel').textContent;
  renderTradingCharts(visible);
}

function drawLineChart(canvas, seriesList, options = {}) {
  const { ctx, width, height } = canvasContext(canvas);
  const pad = { left: 58, right: 18, top: 18, bottom: 28 };
  const all = seriesList.flatMap((s) => s.values.map((p) => p.value).filter((v) => typeof v === 'number'));
  if (!all.length) {
    drawEmpty(canvas, '暂无数据');
    return;
  }
  let min = options.min ?? Math.min(...all);
  let max = options.max ?? Math.max(...all);
  const margin = (max - min || 1) * 0.08;
  min = options.min ?? (min - margin);
  max = options.max ?? (max + margin);
  drawGrid(ctx, width, height, pad);

  seriesList.forEach((series) => {
    const values = series.values;
    const step = (width - pad.left - pad.right) / Math.max(1, values.length - 1);
    ctx.strokeStyle = series.color;
    ctx.lineWidth = 1.7;
    ctx.beginPath();
    values.forEach((p, i) => {
      const x = pad.left + i * step;
      const y = scaleY(p.value, min, max, pad, height);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  ctx.fillStyle = '#6d7168';
  ctx.font = '11px sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i += 1) {
    const value = max - ((max - min) * i) / 4;
    const y = pad.top + ((height - pad.top - pad.bottom) * i) / 4;
    ctx.fillText(options.percent ? fmtPct(value) : fmt(value), pad.left - 6, y + 4);
  }

  ctx.textAlign = 'left';
  let x = pad.left;
  seriesList.forEach((series) => {
    ctx.fillStyle = series.color;
    ctx.fillRect(x, 8, 10, 3);
    ctx.fillStyle = '#6d7168';
    ctx.fillText(series.label, x + 14, 12);
    x += 92;
  });
}

async function runBacktest(forceTab = true) {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  state.stock = stock;
  setBusy(true);
  setMessage('回测中');
  try {
    const body = {
      stock,
      start_date: $('btStart').value || undefined,
      end_date: $('btEnd').value || undefined,
      initial_cash: Number($('btCash').value || 100000),
      commission_rate: Number($('btCommission').value || 0),
      slippage_bps: Number($('btSlippage').value || 0),
      min_bars: Number($('btMinBars').value || 90),
      lot_size: Math.max(1, Number($('btLotSize').value || 100)),
    };
    const resp = await fetch('/api/stock/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.success === false) {
      throw new Error(payload.message || '回测失败');
    }
    const first = (payload.results || [])[0];
    if (!first) throw new Error('无回测结果');
    if (first.error) throw new Error(first.error);
    state.backtestData = first;
    $('jsonOutput').textContent = JSON.stringify(payload, null, 2);
    if (forceTab) activateTab('backtest');
    renderBacktest(first);
    setMessage('');
  } catch (err) {
    setMessage(err.message || String(err), 'error');
    $('jsonOutput').textContent = JSON.stringify({ error: err.message || String(err) }, null, 2);
  } finally {
    setBusy(false);
  }
}

function renderBacktest(result) {
  const metrics = result.metrics || {};
  $('btTotalReturn').textContent = fmtPct(metrics.total_return);
  $('btExcessReturn').textContent = fmtPct(metrics.excess_return);
  $('btMaxDrawdown').textContent = fmtPct(metrics.max_drawdown);
  $('btSharpe').textContent = fmt(metrics.sharpe);
  $('btTradeCount').textContent = fmt(metrics.trade_count);
  $('btWinRate').textContent = fmtPct(metrics.win_rate);
  renderBacktestCharts(result);
  renderTrades(result.trades || []);
}

function renderBacktestCharts(result = state.backtestData) {
  if (!result) return;
  const equity = result.equity_curve || [];
  const positions = result.positions || [];
  const root = document.querySelector('.backtest-charts');
  if (hasLightweightCharts()) {
    root?.classList.add('tv-ready');
    if (state.btRenderFrame) cancelAnimationFrame(state.btRenderFrame);
    state.btRenderFrame = requestAnimationFrame(() => {
      state.btRenderFrame = null;
      renderBacktestTradingCharts(result);
    });
    return;
  }
  root?.classList.remove('tv-ready');
  destroyBacktestCharts();
  drawLineChart($('equityCanvas'), [
    { label: '策略', color: '#2b6f8f', values: equity.map((p) => ({ time: p.date, value: p.equity })) },
    { label: '基准', color: '#b7791f', values: equity.map((p) => ({ time: p.date, value: p.benchmark_equity })) },
  ]);
  drawLineChart($('drawdownCanvas'), [
    { label: '回撤', color: '#c84635', values: equity.map((p) => ({ time: p.date, value: p.drawdown })) },
  ], { max: 0, percent: true });
  drawLineChart($('positionCanvas'), [
    { label: '仓位', color: '#1d8f63', values: positions.map((p) => ({ time: p.date, value: p.position_weight })) },
  ], { min: 0, max: 1, percent: true });
}

function backtestChartOptions(container, height, showTimeScale) {
  const tv = window.LightweightCharts;
  const rect = container.getBoundingClientRect();
  const parentWidth = container.parentElement?.getBoundingClientRect?.().width || 0;
  return {
    width: Math.max(320, Math.floor(rect.width || parentWidth || container.clientWidth || 0)),
    height,
    layout: {
      background: { type: tv.ColorType?.Solid || 'solid', color: '#ffffff' },
      textColor: '#20221f',
      fontSize: 12,
    },
    grid: {
      vertLines: { color: '#eef0ea' },
      horzLines: { color: '#eef0ea' },
    },
    rightPriceScale: {
      borderColor: '#daded2',
      scaleMargins: { top: 0.12, bottom: 0.12 },
    },
    timeScale: {
      visible: showTimeScale,
      borderColor: '#daded2',
      timeVisible: false,
      secondsVisible: false,
    },
    localization: {
      locale: 'zh-CN',
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
  };
}

function backtestSeries(points, key) {
  return (points || []).map((point) => ({
    time: dateKey(point.date),
    value: Number(point[key]),
  })).filter((point) => point.time && Number.isFinite(point.value));
}

function renderBacktestTradingCharts(result) {
  destroyBacktestCharts();
  const tv = window.LightweightCharts;
  const equity = result.equity_curve || [];
  const positions = result.positions || [];
  const chartDefs = [
    { id: 'btEquityChart', height: 260, time: false },
    { id: 'btDrawdownChart', height: 180, time: false },
    { id: 'btPositionChart', height: 180, time: true },
  ];
  const charts = chartDefs.map((def) => {
    const container = $(def.id);
    container.textContent = '';
    const chart = tv.createChart(container, backtestChartOptions(container, def.height, def.time));
    state.btCharts.push(chart);
    return chart;
  });

  charts[0].applyOptions({
    localization: {
      locale: 'zh-CN',
      priceFormatter: (value) => fmtMoney(value),
    },
  });
  charts[0].addLineSeries({
    color: '#2b6f8f',
    lineWidth: 2,
    title: '策略权益',
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
  })
    .setData(backtestSeries(equity, 'equity'));
  charts[0].addLineSeries({
    color: '#b7791f',
    lineWidth: 2,
    title: '买入持有',
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
  })
    .setData(backtestSeries(equity, 'benchmark_equity'));

  charts[1].addAreaSeries({
    topColor: 'rgba(200, 70, 53, 0.05)',
    bottomColor: 'rgba(200, 70, 53, 0.32)',
    lineColor: '#c84635',
    lineWidth: 2,
    title: '回撤',
    priceFormat: {
      type: 'custom',
      minMove: 0.01,
      formatter: (value) => `${Number(value).toFixed(2)}%`,
    },
  }).setData(backtestSeries(equity, 'drawdown').map((p) => ({ ...p, value: p.value * 100 })));

  charts[2].addAreaSeries({
    topColor: 'rgba(29, 143, 99, 0.32)',
    bottomColor: 'rgba(29, 143, 99, 0.04)',
    lineColor: '#1d8f63',
    lineWidth: 2,
    title: '仓位',
    priceFormat: {
      type: 'custom',
      minMove: 0.01,
      formatter: (value) => `${Number(value).toFixed(2)}%`,
    },
  }).setData(backtestSeries(positions, 'position_weight').map((p) => ({ ...p, value: p.value * 100 })));

  syncTimeScales(charts[0], charts[1]);
  syncTimeScales(charts[0], charts[2]);
  charts.forEach((chart) => chart.timeScale().fitContent());
}

function renderTrades(trades) {
  const body = $('tradeTableBody');
  body.textContent = '';
  if (!trades.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 8;
    cell.textContent = '暂无交易';
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  trades.slice().reverse().slice(0, 80).forEach((trade) => {
    const row = document.createElement('tr');
    [
      fullDate(trade.signal_date),
      fullDate(trade.date),
      trade.side,
      fmt(trade.price),
      fmt(trade.quantity),
      fmtMoney(trade.gross_value),
      fmt(trade.target_position),
      fmtMoney(trade.realized_pnl),
    ].forEach((value) => {
      const td = document.createElement('td');
      td.textContent = value;
      row.appendChild(td);
    });
    body.appendChild(row);
  });
}

async function loadWatchlist() {
  try {
    const resp = await fetch('/api/registered-stocks');
    const payload = await resp.json();
    const root = $('watchlist');
    root.textContent = '';
    if (!resp.ok || payload.success === false || !payload.registered_stocks?.length) {
      root.textContent = '暂无注册股票';
      return;
    }
    const registered = new Map();
    payload.registered_stocks.forEach((item) => {
      const key = `${item.market}:${item.stock_code}`;
      if (!registered.has(key)) registered.set(key, item);
    });
    Array.from(registered.values()).slice(0, 30).forEach((item) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'watch-item';
      const name = document.createElement('strong');
      const displayCode = item.display_code || item.stock_code;
      const displayName = item.stock_name || displayCode;
      name.textContent = displayName;
      const meta = document.createElement('span');
      meta.className = 'watch-code';
      meta.textContent = `${displayCode} / ${item.active === false ? '暂停' : '启用'}`;
      btn.append(name, meta);
      btn.addEventListener('click', () => {
        $('stockInput').value = item.stock_name || displayCode;
        analyzeStock();
      });
      root.appendChild(btn);
    });
  } catch {
    $('watchlist').textContent = '暂无注册股票';
  }
}

async function checkApi() {
  const status = $('apiStatus');
  try {
    const resp = await fetch('/api/health');
    if (!resp.ok) throw new Error('bad status');
    status.classList.add('ok');
    status.classList.remove('error');
  } catch {
    status.classList.add('error');
    status.classList.remove('ok');
  }
}

function activateTab(name) {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('is-active', tab.dataset.tab === name);
  });
  document.querySelectorAll('.tab-panel').forEach((panel) => {
    panel.classList.toggle('is-active', panel.id === `panel-${name}`);
  });
  if (name === 'chart') renderCharts();
  if (name === 'backtest') renderBacktestCharts();
  if (name === 'tasks') loadDataJobs().catch((err) => setMessage(err.message || String(err), 'error'));
}

document.addEventListener('DOMContentLoaded', () => {
  $('decisionForm').addEventListener('submit', (event) => {
    event.preventDefault();
    analyzeStock();
  });
  $('registerBtn').addEventListener('click', registerStock);
  $('registerByNameBtn').addEventListener('click', registerStockByName);
  $('chartBtn').addEventListener('click', () => loadChartData(true));
  $('backtestBtn').addEventListener('click', () => runBacktest(true));
  $('refreshBtn').addEventListener('click', refreshStockData);
  $('backfillDataBtn').addEventListener('click', backfillStockData);
  $('clearDataBtn').addEventListener('click', clearStockData);
  $('unregisterStockBtn').addEventListener('click', unregisterCurrentStock);
  $('refreshJobsBtn').addEventListener('click', () => loadDataJobs().catch((err) => setMessage(err.message || String(err), 'error')));
  $('backfillDaysInput').addEventListener('change', loadBackfillEstimate);
  $('backtestForm').addEventListener('submit', (event) => {
    event.preventDefault();
    runBacktest(true);
  });
  $('chartDaysInput').addEventListener('change', () => renderCharts());
  document.querySelectorAll('[data-chart-interval]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const nextInterval = btn.dataset.chartInterval;
      if (state.chartInterval === nextInterval) return;
      state.chartInterval = nextInterval;
      document.querySelectorAll('[data-chart-interval]').forEach((item) => {
        item.classList.toggle('is-active', item === btn);
      });
      renderCharts();
    });
  });
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => activateTab(tab.dataset.tab));
  });
  window.addEventListener('resize', () => {
    if (state.tv.priceChart) resizeTradingCharts();
    else renderCharts();
    renderBacktestCharts();
  });
  ['btStart', 'btEnd'].forEach((id) => {
    const input = $(id);
    if (!input || typeof input.showPicker !== 'function') return;
    const openPicker = () => {
      try {
        input.showPicker();
      } catch {
        // Native pickers may require a direct user activation in some browsers.
      }
    };
    input.addEventListener('click', openPicker);
    input.addEventListener('focus', openPicker);
  });
  installChartResizeObserver();
  checkApi();
  loadWatchlist();
  loadDataStatus(false);
});
