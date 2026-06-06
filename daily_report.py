#!/usr/bin/env python3
"""每日股票量化分析简报 — 每天 1:00 UTC (9:00 SGT) 由 cron 执行"""

import json
import subprocess
import sys
from datetime import datetime, timezone

API = "http://127.0.0.1:5000"
REPORT_PATH = "/tmp/daily_stock_report.txt"
HEARTBEAT_PATH = "/root/.openclaw/workspace/HEARTBEAT.md"
STOCKQUANT_DIR = "/root/.openclaw/workspace/StockQuantAssistant"


def api_get(path):
    r = subprocess.run(["curl", "-s", f"{API}{path}"], capture_output=True, text=True, timeout=120)
    return json.loads(r.stdout) if r.stdout else {}


def api_post(path, data):
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{API}{path}", "-H", "Content-Type: application/json", "-d", json.dumps(data)],
        capture_output=True, text=True, timeout=600,
    )
    return json.loads(r.stdout) if r.stdout else {}


def ensure_service():
    import time, os
    # 多次重试健康检查，避免误杀运行中的服务
    for attempt in range(3):
        try:
            r = subprocess.run(["curl", "-s", "--max-time", "10", f"{API}/api/health"],
                              capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and '"status":"ok"' in r.stdout:
                return True
        except Exception:
            pass
        if attempt < 2:
            time.sleep(3)
    # 服务确实不可用，才清理僵尸进程并重启
    os.system("pkill -f stockquant-server 2>/dev/null")
    time.sleep(2)
    subprocess.run(
        ["nohup", f"{STOCKQUANT_DIR}/venv/bin/stockquant-server", "start", "--host", "127.0.0.1", "--port", "5000"],
        cwd=STOCKQUANT_DIR,
        stdout=open("/tmp/stockquant.log", "w"),
        stderr=subprocess.STDOUT,
    )
    # 等待服务器完全启动（包括调度器初始化 + 日线数据拉取）
    time.sleep(30)
    return True


def pad_cn(s, width):
    """中文友好填充：中文字符算2宽度"""
    w = 0
    for c in str(s):
        w += 2 if ord(c) > 127 else 1
    return str(s) + " " * max(0, width - w)


def collect_results():
    codes_resp = api_get("/api/stock/codes")
    stock_names = [c["name"] for c in codes_resp.get("codes", [])]

    results = []
    for name in stock_names:
        try:
            resp = api_post("/api/stock/decision", {"stock": name})
            if resp.get("success"):
                for r in resp.get("results", []):
                    if "error" in r:
                        continue
                    market_map = {"a": "A股", "hk": "港股", "us": "美股"}

                    # 四轨值：从 trend.key_lines 取当前日线通道
                    trend = r.get("trend", {})
                    kl = trend.get("key_lines", {}) if trend else {}

                    # 新版 API: 信号在 signals dict 里
                    sig_data = r.get("signals", {})
                    signals = []
                    sig_seq = sig_data.get("sequence", "none")
                    if sig_seq and sig_seq != "none":
                        signals.append(f"序列{sig_seq}")
                    sig_struct = sig_data.get("structure", "none")
                    if sig_struct and sig_struct != "none":
                        signals.append(f"结构{sig_struct}")
                    if sig_data.get("probe"):
                        signals.append("探头")
                    reso = sig_data.get("resonance")
                    if reso:
                        signals.append(f"共振{reso}")

                    # 新版 API: position 是 dict
                    pos = r.get("position", 0)
                    if isinstance(pos, dict):
                        pos = pos.get("current", 0)
                    if pos is None:
                        pos = 0

                    # 新版 API: action 和 confidence
                    action = r.get("action", "-")
                    confidence = r.get("confidence", "-")

                    results.append({
                        "name": resp.get("input", name),
                        "code": r.get("display_code", r.get("stock_code", "")),
                        "market": market_map.get(r.get("market"), r.get("market", "")),
                        "close": r.get("close"),
                        "position": pos,
                        "action": action,
                        "confidence": confidence,
                        "short_upper": kl.get("short_upper_current"),
                        "short_lower": kl.get("short_lower_current"),
                        "long_upper": kl.get("long_upper_current"),
                        "long_lower": kl.get("long_lower_current"),
                        "signals": signals,
                    })
        except Exception as e:
            print(f"  ⚠ {name} 查询失败: {e}", file=sys.stderr)

    return results


def format_num(v):
    if isinstance(v, (int, float)):
        return f"{v:+.2f}" if abs(v) >= 0.01 else f"{v:+.4f}"
    return str(v)


def generate_report(results):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    # 分组
    full = [r for r in results if r["position"] == 10]
    heavy = [r for r in results if 6 <= r["position"] < 10]
    light = [r for r in results if 4 <= r["position"] < 6]
    cold = [r for r in results if 0 < r["position"] < 4]
    empty = [r for r in results if r["position"] == 0]
    signaled = [r for r in results if r["signals"]]

    lines = []
    lines.append(f"📊 每日量化决策 ({now} UTC)")
    lines.append(f"总计 {len(results)} 只 | 🟢满仓{len(full)} 🟡重仓{len(heavy)} 🔵轻仓{len(light)} 🚀冷启{len(cold)} 🔴空仓{len(empty)}")
    lines.append("=" * 60)

    def write_section(emoji, label, stocks):
        if not stocks:
            return
        lines.append("")
        lines.append(f"{emoji} {label} ({len(stocks)}只)")
        lines.append("-" * 60)
        # 表头（紧凑格式，适配微信等非等宽环境）
        lines.append("股票 代码 价 信号 操作")
        lines.append("-" * 60)
        for s in stocks:
            price = f"{s['close']:.2f}" if isinstance(s['close'], (int, float)) else "-"
            su = f"{s['short_upper']:.2f}" if isinstance(s['short_upper'], (int, float)) else "-"
            sl = f"{s['short_lower']:.2f}" if isinstance(s['short_lower'], (int, float)) else "-"
            lu = f"{s['long_upper']:.2f}" if isinstance(s['long_upper'], (int, float)) else "-"
            ll = f"{s['long_lower']:.2f}" if isinstance(s['long_lower'], (int, float)) else "-"
            sig = ",".join(s["signals"][:3]) if s["signals"] else "-"
            action = s.get("action", "-")
            name = s['name'][:12]
            code = s['code'][:13]
            lines.append(f"{name} {code} {price} {sig} {action}")
            lines.append(f"  轨: 短{su}/{sl} 长{lu}/{ll}")

    write_section("🟢", "满仓", full)
    write_section("🟡", "重仓", heavy)
    write_section("🔵", "轻仓", light)
    write_section("🚀", "冷启动", cold)
    write_section("🔴", "空仓", empty)

    # 信号关注
    if signaled:
        lines.append("")
        lines.append("⚠️ 重点关注信号")
        lines.append("-" * 50)
        for s in signaled:
            sigs = " ".join(s["signals"])
            lines.append(f"  {s['name']}({s['code']}) → {sigs}")

    lines.append("")
    lines.append("=" * 50)
    lines.append(f"自动生成 · 下次推送 9:00 SGT")

    return "\n".join(lines)


def main():
    print("确保服务运行中...")
    ensure_service()

    print("强制刷新数据（跳过交易时间检查）...")
    refresh_resp = api_post("/api/refresh", {})
    if not refresh_resp.get('success'):
        print(f"  ❌ 刷新失败: {refresh_resp}")
        sys.exit(1)
    print(f"  刷新完成: {refresh_resp.get('total_queries')} 次查询, {refresh_resp.get('total_rows_inserted')} 行新数据, {refresh_resp.get('errors')} 个错误")

    print("采集量化决策数据...")
    results = collect_results()
    print(f"共采集 {len(results)} 条结果")

    if not results:
        print("无数据")
        return

    report = generate_report(results)
    with open(REPORT_PATH, "w") as f:
        f.write(report)
    print(f"报告已保存: {REPORT_PATH}")

    # 标记心跳
    now_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with open(HEARTBEAT_PATH, "w") as f:
        f.write(f"# Daily report ready at {now_tag} — send to 老板\n")
        f.write(f"READ /tmp/daily_stock_report.txt AND SEND THE FULL CONTENT TO 老板\n")


if __name__ == "__main__":
    main()
