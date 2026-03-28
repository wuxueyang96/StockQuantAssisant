#!/usr/bin/env python3
"""
股票技术分析工具
获取历史数据、计算趋势、支撑压力位、ZigZag转折点，并生成图表
"""

import requests
import json
import sys
import re
import math
from datetime import datetime, timedelta
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # 非交互式后端
from io import BytesIO

def search_stock_by_name(company_name):
    """通过公司名称搜索股票代码"""
    search_url = f"http://searchapi.eastmoney.com/api/suggest/get?input={company_name}&type=14&token=D43BF722C8E33BD8C8B185F60AA70F4E&count=5"
    
    try:
        response = requests.get(search_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('QuotationCodeTable') and data['QuotationCodeTable'].get('Data'):
                results = data['QuotationCodeTable']['Data']
                a_stocks = []
                for item in results:
                    code = item.get('Code', '')
                    name = item.get('Name', '')
                    if (code.startswith('600') or code.startswith('601') or 
                        code.startswith('603') or code.startswith('605') or
                        code.startswith('000') or code.startswith('001') or 
                        code.startswith('002') or code.startswith('300')):
                        a_stocks.append({
                            'code': code,
                            'name': name,
                            'market': 'SH' if code.startswith('6') else 'SZ'
                        })
                return a_stocks
        return []
    except Exception as e:
        print(f"搜索股票时出错: {e}")
        return []

def get_stock_history(stock_code, market=None, days=60):
    """获取股票历史数据"""
    if not market:
        if stock_code.startswith('600') or stock_code.startswith('601') or \
           stock_code.startswith('603') or stock_code.startswith('605'):
            market = 'SH'
        else:
            market = 'SZ'
    
    # 使用东方财富API获取K线数据
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days*2)  # 多取一些数据用于计算均线
    
    url = f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        'secid': f"1.{stock_code}" if market == 'SH' else f"0.{stock_code}",
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': '101',  # 101=日K
        'fqt': '1',    # 1=前复权
        'beg': start_date.strftime('%Y%m%d'),
        'end': end_date.strftime('%Y%m%d'),
        '_': '1'
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('data') and data['data'].get('klines'):
                klines = data['data']['klines']
                stock_name = data['data'].get('name', stock_code)
                
                history_data = []
                for kline in klines:
                    parts = kline.split(',')
                    if len(parts) >= 7:
                        date = parts[0]
                        open_price = float(parts[1])
                        close_price = float(parts[2])
                        high_price = float(parts[3])
                        low_price = float(parts[4])
                        volume = float(parts[5])
                        amount = float(parts[6])
                        
                        history_data.append({
                            'date': date,
                            'open': open_price,
                            'close': close_price,
                            'high': high_price,
                            'low': low_price,
                            'volume': volume,
                            'amount': amount
                        })
                
                return {
                    'success': True,
                    'stock_code': f'{stock_code}.{market}',
                    'name': stock_name,
                    'data': history_data[-days:]  # 只返回最近days天的数据
                }
        
        return {'success': False, 'error': '无法获取历史数据'}
    except Exception as e:
        return {'success': False, 'error': f'获取历史数据失败: {str(e)}'}

def linear_regression_trend(prices):
    """最小二乘法线性回归计算趋势"""
    n = len(prices)
    if n < 2:
        return {'slope': 0, 'intercept': 0, 'r_squared': 0}
    
    x = np.arange(n)
    y = np.array(prices)
    
    slope, intercept = np.polyfit(x, y, 1)
    
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
    
    return {
        'slope': slope,
        'intercept': intercept,
        'r_squared': r_squared,
        'trend': 'up' if slope > 0 else 'down',
        'strength': abs(slope) * 100 / prices[0] if prices[0] != 0 else 0
    }

def calculate_ma(data, period):
    """计算移动平均线"""
    closes = [d['close'] for d in data]
    ma = []
    for i in range(len(closes)):
        if i < period - 1:
            ma.append(None)
        else:
            ma.append(np.mean(closes[i-period+1:i+1]))
    return ma

def ma_slope_trend(ma_data):
    """通过均线斜率计算趋势"""
    valid_ma = [m for m in ma_data if m is not None]
    if len(valid_ma) < 2:
        return {'slope': 0, 'trend': 'flat'}
    
    recent_ma = valid_ma[-10:] if len(valid_ma) >= 10 else valid_ma
    x = np.arange(len(recent_ma))
    slope, _ = np.polyfit(x, recent_ma, 1)
    
    return {
        'slope': slope,
        'trend': 'up' if slope > 0 else 'down',
        'momentum': slope * 100 / recent_ma[0] if recent_ma[0] != 0 else 0
    }

def find_support_resistance(data, window=5):
    """滑动窗口极值算法找支撑位和压力位"""
    if len(data) < window * 2:
        return {'supports': [], 'resistances': []}
    
    highs = [d['high'] for d in data]
    lows = [d['low'] for d in data]
    
    supports = []
    resistances = []
    
    for i in range(window, len(data) - window):
        # 检查是否为局部低点（支撑位）
        if lows[i] == min(lows[i-window:i+window+1]):
            supports.append({
                'date': data[i]['date'],
                'price': lows[i],
                'index': i
            })
        
        # 检查是否为局部高点（压力位）
        if highs[i] == max(highs[i-window:i+window+1]):
            resistances.append({
                'date': data[i]['date'],
                'price': highs[i],
                'index': i
            })
    
    return {'supports': supports, 'resistances': resistances}

def zigzag_algorithm(data, threshold=0.05):
    """ZigZag算法，过滤小于threshold的波动"""
    if len(data) < 2:
        return []
    
    zigzag_points = []
    closes = [d['close'] for d in data]
    
    last_extreme_idx = 0
    last_extreme_price = closes[0]
    last_extreme_type = None  # 'high' or 'low'
    
    for i in range(1, len(data)):
        current_price = closes[i]
        
        if last_extreme_type is None:
            if current_price > last_extreme_price * (1 + threshold):
                zigzag_points.append({
                    'date': data[last_extreme_idx]['date'],
                    'price': last_extreme_price,
                    'type': 'low',
                    'index': last_extreme_idx
                })
                last_extreme_idx = i
                last_extreme_price = current_price
                last_extreme_type = 'high'
            elif current_price < last_extreme_price * (1 - threshold):
                zigzag_points.append({
                    'date': data[last_extreme_idx]['date'],
                    'price': last_extreme_price,
                    'type': 'high',
                    'index': last_extreme_idx
                })
                last_extreme_idx = i
                last_extreme_price = current_price
                last_extreme_type = 'low'
        elif last_extreme_type == 'high':
            if current_price > last_extreme_price:
                last_extreme_idx = i
                last_extreme_price = current_price
            elif current_price < last_extreme_price * (1 - threshold):
                zigzag_points.append({
                    'date': data[last_extreme_idx]['date'],
                    'price': last_extreme_price,
                    'type': 'high',
                    'index': last_extreme_idx
                })
                last_extreme_idx = i
                last_extreme_price = current_price
                last_extreme_type = 'low'
        elif last_extreme_type == 'low':
            if current_price < last_extreme_price:
                last_extreme_idx = i
                last_extreme_price = current_price
            elif current_price > last_extreme_price * (1 + threshold):
                zigzag_points.append({
                    'date': data[last_extreme_idx]['date'],
                    'price': last_extreme_price,
                    'type': 'low',
                    'index': last_extreme_idx
                })
                last_extreme_idx = i
                last_extreme_price = current_price
                last_extreme_type = 'high'
    
    if last_extreme_type is not None:
        zigzag_points.append({
            'date': data[last_extreme_idx]['date'],
            'price': last_extreme_price,
            'type': last_extreme_type,
            'index': last_extreme_idx
        })
    
    return zigzag_points

def generate_chart(stock_info, analysis_data, output_path=None):
    """生成分析图表"""
    data = stock_info['data']
    dates = [d['date'] for d in data]
    closes = [d['close'] for d in data]
    highs = [d['high'] for d in data]
    lows = [d['low'] for d in data]
    
    fig = plt.figure(figsize=(16, 12))
    
    # 1. 主图：价格、均线、趋势线
    ax1 = plt.subplot(3, 1, 1)
    ax1.plot(dates, closes, label='收盘价', linewidth=2, color='blue')
    
    if 'ma20' in analysis_data:
        ax1.plot(dates, analysis_data['ma20'], label='MA20', linewidth=1.5, color='orange', alpha=0.8)
    if 'ma60' in analysis_data:
        ax1.plot(dates, analysis_data['ma60'], label='MA60', linewidth=1.5, color='green', alpha=0.8)
    
    if 'linear_regression' in analysis_data:
        lr = analysis_data['linear_regression']
        x = np.arange(len(closes))
        trend_line = lr['slope'] * x + lr['intercept']
        ax1.plot(dates, trend_line, label=f'趋势线 (R²={lr["r_squared"]:.3f})', 
                 linewidth=2, color='red', linestyle='--')
    
    if 'support_resistance' in analysis_data:
        sr = analysis_data['support_resistance']
        for support in sr['supports']:
            ax1.axhline(y=support['price'], color='green', linestyle=':', linewidth=1, alpha=0.6)
        for resistance in sr['resistances']:
            ax1.axhline(y=resistance['price'], color='red', linestyle=':', linewidth=1, alpha=0.6)
    
    if 'zigzag' in analysis_data:
        zz = analysis_data['zigzag']
        zz_dates = [dates[p['index']] for p in zz]
        zz_prices = [p['price'] for p in zz]
        ax1.plot(zz_dates, zz_prices, label='ZigZag', linewidth=2, color='purple', marker='o', markersize=4)
    
    ax1.set_title(f'{stock_info["name"]} ({stock_info["stock_code"]}) - 技术分析', fontsize=14, fontweight='bold')
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    ax1.xaxis.set_major_locator(plt.MaxNLocator(10))
    
    # 2. 成交量
    ax2 = plt.subplot(3, 1, 2, sharex=ax1)
    volumes = [d['volume'] for d in data]
    colors = ['green' if closes[i] >= closes[i-1] else 'red' for i in range(1, len(closes))]
    colors = ['gray'] + colors
    ax2.bar(dates, volumes, label='成交量', color=colors, alpha=0.6)
    ax2.set_ylabel('成交量')
    ax2.legend(loc='upper left')
    ax2.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    
    # 3. 分析总结
    ax3 = plt.subplot(3, 1, 3)
    ax3.axis('off')
    
    summary_text = f"【分析总结】\n\n"
    
    if 'linear_regression' in analysis_data:
        lr = analysis_data['linear_regression']
        summary_text += f"线性回归趋势：{'📈 上涨' if lr['trend'] == 'up' else '📉 下跌'}\n"
        summary_text += f"趋势强度：{lr['strength']:.2f}% | R²：{lr['r_squared']:.3f}\n\n"
    
    if 'ma20_trend' in analysis_data:
        ma20 = analysis_data['ma20_trend']
        summary_text += f"MA20趋势：{'📈 向上' if ma20['trend'] == 'up' else '📉 向下'}\n"
        summary_text += f"MA20动量：{ma20['momentum']:.2f}%\n\n"
    
    if 'support_resistance' in analysis_data:
        sr = analysis_data['support_resistance']
        if sr['supports']:
            nearest_support = min(sr['supports'], key=lambda x: len(data)-1 - x['index'])
            summary_text += f"最近支撑位：¥{nearest_support['price']:.2f} ({nearest_support['date']})\n"
        if sr['resistances']:
            nearest_resistance = min(sr['resistances'], key=lambda x: len(data)-1 - x['index'])
            summary_text += f"最近压力位：¥{nearest_resistance['price']:.2f} ({nearest_resistance['date']})\n\n"
    
    if 'zigzag' in analysis_data and len(analysis_data['zigzag']) >= 2:
        zz = analysis_data['zigzag']
        last_point = zz[-1]
        prev_point = zz[-2]
        if last_point['type'] == 'low':
            summary_text += f"📊 ZigZag：最后一个点是底部 (¥{last_point['price']:.2f})\n"
        else:
            summary_text += f"📊 ZigZag：最后一个点是顶部 (¥{last_point['price']:.2f})\n"
    
    summary_text += f"\n数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    ax3.text(0.05, 0.95, summary_text, transform=ax3.transAxes,
             fontsize=11, verticalalignment='top',
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    plt.tight_layout()
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"图表已保存到: {output_path}")
    
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    
    return buf

def analyze_stock(query, days=60, ma_periods=[20, 60], zz_threshold=0.05):
    """完整的股票分析流程"""
    if re.match(r'^\d{6}$', query):
        stock_code = query
        market = 'SH' if stock_code.startswith('6') else 'SZ'
    else:
        stocks = search_stock_by_name(query)
        if not stocks:
            return {'success': False, 'error': f'未找到名为 "{query}" 的A股上市公司'}
        if len(stocks) > 1:
            return {'success': False, 'error': '找到多个匹配公司', 'candidates': stocks}
        stock_code = stocks[0]['code']
        market = stocks[0]['market']
    
    history_result = get_stock_history(stock_code, market, days)
    if not history_result['success']:
        return history_result
    
    data = history_result['data']
    closes = [d['close'] for d in data]
    
    analysis_data = {}
    
    analysis_data['linear_regression'] = linear_regression_trend(closes)
    
    for period in ma_periods:
        ma = calculate_ma(data, period)
        analysis_data[f'ma{period}'] = ma
        analysis_data[f'ma{period}_trend'] = ma_slope_trend(ma)
    
    analysis_data['support_resistance'] = find_support_resistance(data)
    analysis_data['zigzag'] = zigzag_algorithm(data, zz_threshold)
    
    return {
        'success': True,
        'stock_info': history_result,
        'analysis': analysis_data
    }

def main():
    if len(sys.argv) < 2:
        print("使用方法: python stock_analyzer.py <公司名称或股票代码> [天数]")
        print("示例:")
        print("  python stock_analyzer.py 阳光电源 60")
        print("  python stock_analyzer.py 300274 60")
        sys.exit(1)
    
    query = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    
    print(f"🔍 正在分析: {query} (最近{days}天)")
    
    result = analyze_stock(query, days)
    
    if not result['success']:
        print(f"❌ {result['error']}")
        if 'candidates' in result:
            print("\n📋 候选公司列表：")
            for stock in result['candidates']:
                print(f"  • {stock['name']} ({stock['code']}.{stock['market']})")
        sys.exit(1)
    
    stock_info = result['stock_info']
    analysis = result['analysis']
    
    print(f"\n✅ 分析完成: {stock_info['name']} ({stock_info['stock_code']})")
    print(f"📊 数据范围: {stock_info['data'][0]['date']} 至 {stock_info['data'][-1]['date']}")
    
    lr = analysis['linear_regression']
    print(f"\n📈 线性回归趋势: {'上涨' if lr['trend'] == 'up' else '下跌'}")
    print(f"   斜率: {lr['slope']:.4f}, R²: {lr['r_squared']:.3f}")
    
    if 'ma20_trend' in analysis:
        ma20 = analysis['ma20_trend']
        print(f"📊 MA20趋势: {'向上' if ma20['trend'] == 'up' else '向下'}")
    
    if 'ma60_trend' in analysis:
        ma60 = analysis['ma60_trend']
        print(f"📊 MA60趋势: {'向上' if ma60['trend'] == 'up' else '向下'}")
    
    # 确保 /root/.openclaw/ 目录存在
    import os
    output_dir = "/root/.openclaw"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    output_path = f"{output_dir}/{stock_info['stock_code']}_analysis.png"
    generate_chart(stock_info, analysis, output_path)
    
    print(f"\nMEDIA: {output_path}")
    
    if '--json' in sys.argv:
        result['chart_path'] = output_path
        print("\n" + json.dumps(result, ensure_ascii=False, indent=2))
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
