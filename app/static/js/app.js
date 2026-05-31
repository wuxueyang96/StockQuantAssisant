const state = {
  stock: '',
  lastPayload: null,
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

  updateChart(false);
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
  } catch (err) {
    setMessage(err.message || String(err), 'error');
  } finally {
    setBusy(false);
  }
}

function updateChart(forceTab = true) {
  const stock = $('stockInput').value.trim();
  if (!stock) return;
  const bars = Math.max(20, Math.min(500, Number($('barsInput').value || 90)));
  $('barsInput').value = bars;
  const img = $('chartImage');
  const frame = img.closest('.chart-frame');
  $('chartTitle').textContent = `${stock} / integrated`;
  img.onload = () => frame.classList.add('has-image');
  img.onerror = () => {
    frame.classList.remove('has-image');
    setMessage('图表暂不可用', 'error');
  };
  img.src = `/api/stock/chart?stock=${encodeURIComponent(stock)}&bars=${bars}&mode=integrated&_=${Date.now()}`;
  if (forceTab) activateTab('chart');
}

async function loadWatchlist() {
  try {
    const resp = await fetch('/api/stock/codes');
    const payload = await resp.json();
    const root = $('watchlist');
    root.textContent = '';
    if (!resp.ok || payload.success === false || !payload.codes?.length) {
      root.textContent = '暂无映射';
      return;
    }
    payload.codes.slice(0, 20).forEach((item) => {
      const code = item.a_code || item.hk_code || item.us_code || item.name;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'watch-item';
      const name = document.createElement('strong');
      name.textContent = item.name;
      const meta = document.createElement('span');
      meta.textContent = [item.a_code, item.hk_code, item.us_code].filter(Boolean).join(' / ');
      btn.append(name, meta);
      btn.addEventListener('click', () => {
        $('stockInput').value = code;
        analyzeStock();
      });
      root.appendChild(btn);
    });
  } catch {
    $('watchlist').textContent = '暂无映射';
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
}

document.addEventListener('DOMContentLoaded', () => {
  $('decisionForm').addEventListener('submit', (event) => {
    event.preventDefault();
    analyzeStock();
  });
  $('registerBtn').addEventListener('click', registerStock);
  $('chartBtn').addEventListener('click', () => updateChart(true));
  $('barsInput').addEventListener('change', () => updateChart(false));
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => activateTab(tab.dataset.tab));
  });
  checkApi();
  loadWatchlist();
});
