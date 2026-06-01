#!/bin/bash
# 每日股票量化分析报告生成脚本
# 每天早上 9:00 (UTC+8) / 1:00 (UTC) 执行

API="http://127.0.0.1:5000"
REPORT="/tmp/daily_stock_report.md"
HEARTBEAT="/root/.openclaw/workspace/HEARTBEAT.md"
STOCKQUANT="/root/.openclaw/workspace/StockQuantAssistant"

# 确保服务在运行
curl -s "$API/api/health" > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "StockQuant 服务未运行，尝试启动..."
  cd "$STOCKQUANT" && nohup venv/bin/stockquant-server start --host 127.0.0.1 --port 5000 > /tmp/stockquant.log 2>&1 &
  sleep 5
fi

# 获取所有已录入的股票
CODES=$(curl -s "$API/api/stock/codes" 2>&1)
if [ -z "$CODES" ]; then
  echo "无法获取股票列表"
  exit 1
fi

# 提取所有股票名称
NAMES=$(echo "$CODES" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for c in data.get('codes', []):
    print(c['name'])
")

if [ -z "$NAMES" ]; then
  echo "没有已注册的股票"
  exit 1
fi

# 生成报告头
cat > "$REPORT" << 'HEADER'
# 📊 每日股票量化决策简报

> 数据时间：$(date '+%Y-%m-%d %H:%M UTC')
> 分析周期：daily

---

## 概览

HEADER

# 分析每只股票
FULL=0
HEAVY=0
LIGHT=0
EMPTY=0
TOTAL=0

echo "" >> "$REPORT"
echo "| 股票 | 代码 | 市场 | 最新价 | 仓位 | DIF | DEA | 信号 |" >> "$REPORT"
echo "|------|------|------|--------|------|-----|-----|------|" >> "$REPORT"

while IFS= read -r name; do
  [ -z "$name" ] && continue
  RESULT=$(curl -s -X POST "$API/api/stock/decision" \
    -H "Content-Type: application/json" \
    -d "{\"stock\":\"$name\"}" 2>&1)
  
  echo "$RESULT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
if not data.get('success'):
    sys.exit(0)
for r in data.get('results', []):
    if 'error' in r:
        continue
    pos = r.get('position', 0)
    pos_label = r.get('position_label', '')
    signals = []
    if r.get('high9_signal'): signals.append('⚠高九')
    if r.get('low9_signal'): signals.append('⚡低九')
    if r.get('core_long'): signals.append('🟢核心做多')
    if r.get('core_short'): signals.append('🔴核心做空')
    if r.get('resonance_buy'): signals.append('✅共振买入')
    if r.get('resonance_sell'): signals.append('❌共振卖出')
    if r.get('top_structure_75'): signals.append('▼顶75')
    if r.get('top_structure_100'): signals.append('▼▼顶100')
    if r.get('bottom_structure_75'): signals.append('▲底75')
    if r.get('bottom_structure_100'): signals.append('▲▲底100')
    
    sig_str = ','.join(signals) if signals else '-'
    
    market_map = {'a': 'A股', 'hk': '港股', 'us': '美股'}
    market_label = market_map.get(r.get('market'), r.get('market',''))
    code = r.get('display_code', r.get('stock_code',''))
    close = r.get('close', '-')
    dif = r.get('structure_standard', {}).get('dif', '-')
    dea = r.get('structure_standard', {}).get('dea', '-')
    if isinstance(dif, float): dif = round(dif, 2)
    if isinstance(dea, float): dea = round(dea, 2)
    
    # 输出表格行
    print(f'| {data[\"input\"]} | {code} | {market_label} | {close} | {pos_label}({pos:.0f}) | {dif} | {dea} | {sig_str} |')
    
    # 统计
    if pos == 10:
        print('FULL', end='', flush=True)
    elif pos >= 6:
        print('HEAVY', end='', flush=True)
    elif pos >= 4:
        print('LIGHT', end='', flush=True)
    else:
        print('EMPTY', end='', flush=True)
" 2>&1
  
  TOTAL=$((TOTAL + 1))
done <<< "$NAMES"

# 统计各项
while IFS= read -r name; do
  [ -z "$name" ] && continue
  RESULT=$(curl -s -X POST "$API/api/stock/decision" \
    -H "Content-Type: application/json" \
    -d "{\"stock\":\"$name\"}" 2>&1)
  
  echo "$RESULT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
if not data.get('success'): sys.exit(0)
for r in data.get('results', []):
    if 'error' in r: continue
    pos = r.get('position', 0)
    # count signaled items
    signals = 0
    if r.get('high9_signal') or r.get('low9_signal'): signals += 1
    if r.get('core_long') or r.get('core_short'): signals += 1
    if r.get('resonance_buy') or r.get('resonance_sell'): signals += 1
    if r.get('top_structure_75') or r.get('top_structure_100'): signals += 1
    if r.get('bottom_structure_75') or r.get('bottom_structure_100'): signals += 1
    print(f'{pos:.0f} {signals}')
" 2>&1
done <<< "$NAMES" > /tmp/report_stats.txt

# 生成统计摘要
python3 << 'PYEOF' >> "$REPORT"
stats_file = "/tmp/report_stats.txt"
full = heavy = light = empty = 0
signaled = 0
total = 0

try:
    with open(stats_file) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            parts = line.split()
            if len(parts) >= 2:
                pos = float(parts[0])
                sigs = int(parts[1])
                if pos == 10: full += 1
                elif pos >= 6: heavy += 1
                elif pos >= 4: light += 1
                else: empty += 1
                if sigs > 0: signaled += 1
                total += 1
except: pass

print()
print("---")
print()
print(f"**统计：** 🟢 满仓 {full} | 🟡 重仓 {heavy} | 🔵 轻仓 {light} | 🔴 空仓 {empty} | 📶 有信号 {signaled} / 总计 {total}")
PYEOF

echo "" >> "$REPORT"
echo "---" >> "$REPORT"
echo "*报告由 StockQuantAssistant 自动生成*" >> "$REPORT"

# 标记心跳任务
echo "# Daily report ready at $(date '+%Y-%m-%d %H:%M')" > "$HEARTBEAT"
echo "READ /tmp/daily_stock_report.md AND SEND TO 老板" >> "$HEARTBEAT"

echo "报告生成完毕: $REPORT"
