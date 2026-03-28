#!/usr/bin/env python3
"""
股票技术分析工具 - 支持A股、港股、美股
获取多时间周期历史数据（周、日、小时、分钟）、计算趋势、支撑压力位、ZigZag转折点，并生成图表
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
    """通过公司名称搜索股票代码 - 支持A股、港股、美股"""
    search_url = f"http://searchapi.eastmoney.com/api/suggest/get?input={company_name}&type=14&token=D43BF722C8E33BD8C8B185F60AA70F4E&count=10"
    
    try:
        response = requests.get(search_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('QuotationCodeTable') and data['QuotationCodeTable'].get('Data'):
                results = data['QuotationCodeTable']['Data']
                stocks = []
                for item in results:
                    code = item.get('Code', '')
                    name = item.get('Name', '')
                    market = item.get('Market', '')
                    
                    if (code.startswith('600') or code.startswith('601') or 
                        code.startswith('603') or code.startswith('605') or
                        code.startswith('000') or code.startswith('001') or 
                        code.startswith('002') or code.startswith('300')):
                        stocks.append({
                            'code': code,
                            'name': name,
                            'market': 'SH' if code.startswith('6') else 'SZ',
                            'type': 'A股'
                        })
                    elif code.startswith('00') or code.startswith('01') or code.startswith('02'):
                        stocks.append({
                            'code': code,
                            'name': name,
                            'market': 'HK',
                            'type': '港股'
                        })
                
                if not stocks:
                    us_stocks = search_us_stock(company_name)
                    stocks.extend(us_stocks)
                
                return stocks
        return []
    except Exception as e:
        print(f"搜索股票时出错: {e}")
        return []

def search_us_stock(company_name):
    """搜索美股"""
    common_us_stocks = {
        '腾讯': {'code': 'TCEHY', 'name': '腾讯控股 ADR', 'market': 'US'},
        '阿里巴巴': {'code': 'BABA', 'name': '阿里巴巴', 'market': 'US'},
        '百度': {'code': 'BIDU', 'name': '百度', 'market': 'US'},
        '京东': {'code': 'JD', 'name': '京东', 'market': 'US'},
        '拼多多': {'code': 'PDD', 'name': '拼多多', 'market': 'US'},
        '网易': {'code': 'NTES', 'name': '网易', 'market': 'US'},
        '苹果': {'code': 'AAPL', 'name': '苹果', 'market': 'US'},
        '微软': {'code': 'MSFT', 'name': '微软', 'market': 'US'},
        '谷歌': {'code': 'GOOGL', 'name': '谷歌', 'market': 'US'},
        '特斯拉': {'code': 'TSLA', 'name': '特斯拉', 'market': 'US'},
    }
    
    results = []
    for name, info in common_us_stocks.items():
        if company_name in name or name in company_name:
            results.append({
                'code': info['code'],
                'name': info['name'],
                'market': 'US',
                'type': '美股'
            })
    
    return results

def get_stock_history(stock_code, market=None, period='daily', count=60):
    """获取股票历史数据 - 支持多时间周期"""
    
    if not market:
        if stock_code.startswith('600') or stock_code.startswith('601') or \
           stock_code.startswith('603') or stock_code.startswith('605'):
            market = 'SH'
        elif stock_code.startswith('000') or stock_code.startswith('001') or \
             stock_code.startswith('002') or stock_code.startswith('300'):
            market = 'SZ'
        elif stock_code.startswith('00') or stock_code.startswith('01') or \
             stock_code.startswith('02'):
            market = 'HK'
        else:
            market = 'US'
    
    end_date = datetime.now()
    
    if period == 'weekly':
        start_date = end_date - timedelta(weeks=count*2)
        klt = '102'  # 周K
    elif period == 'hourly':
        start_date = end_date - timedelta(hours=count*2)
        klt = '60'  # 小时K
    elif period == 'minute':
        start_date = end_date - timedelta(minutes=count*2)
        klt = '1'  # 分钟K
    else:  # daily
        start_date = end_date - timedelta(days=count*2)
        klt = '101'  # 日K
    
    if market in ['SH', 'SZ', 'HK']:
        return get_cn_stock_history(stock_code, market, start_date, end_date, klt, count, period)
    elif market == 'US':
        return get_us_stock_history(stock_code, start_date, end_date, count, period)
    else:
        return {'success': False, 'error': '不支持的市场类型'}

def get_cn_stock_history(stock_code, market, start_date, end_date, klt, count, period):
    """获取A股/港股历史数据"""
    url = f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
    
    if market == 'SH':
        secid = f"1.{stock_code}"
    elif market == 'SZ':
        secid = f"0.{stock_code}"
    elif market == 'HK':
        secid = f"116.{stock_code}"
    
    params = {
        'secid': secid,
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': klt,
        'fqt': '1',
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
                    'period': period,
                    'data': history_data[-count:]
                }
        
        return {'success': False, 'error': f'无法获取{period}历史数据'}
    except Exception as e:
        return {'success': False, 'error': f'获取{period}历史数据失败: {str(e)}'}

def get_us_stock_history(stock_code, start_date, end_date, count, period):
    """获取美股历史数据 - 使用Yahoo Finance API"""
    period1 = int(start_date.timestamp())
    period2 = int(end_date.timestamp())
    
    if period == 'weekly':
        interval = '1wk'
    elif period == 'hourly':
        interval = '1h'
    elif period == 'minute':
        interval = '1m'
    else:
        interval = '1d'
    
    url = f"https://query1.finance.yahoo.com/v7/finance/download/{stock_code}"
    params = {
        'period1': period1,
        'period2': period2,
        'interval': interval,
        'events': 'history',
        'includeAdjustedClose': 'true'
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            lines = response.text.strip().split('\n')
            if len(lines) < 2:
                return {'success': False, 'error': '没有找到美股数据'}
            
            history_data = []
            for line in lines[1:]:
                parts = line.split(',')
                if len(parts) >= 7:
                    date = parts[0]
                    open_price = float(parts[1])
                    high_price = float(parts[2])
                    low_price = float(parts[3])
                    close_price = float(parts[4])
                    adj_close = float(parts[5])
                    volume = float(parts[6])
                    
                    history_data.append({
                        'date': date,
                        'open': open_price,
                        'close': adj_close,
                        'high': high_price,
                        'low': low_price,
                        'volume': volume,
                        'amount': volume * adj_close
                    })
            
            return {
                'success': True,
                'stock_code': f'{stock_code}.US',
                'name': stock_code,
                'period': period,
                'data': history_data[-count:]
            }
        
        return {'success': False, 'error': f'无法获取{period}历史数据'}
    except Exception as e:
        return {'success': False, 'error': f'获取{period}历史数据失败: {str(e)}'}

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
        if lows[i] == min(lows[i-window:i+window+1]):
            supports.append({
                'date': data[i]['date'],
                'price': lows[i],
                'index': i
            })
        
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
    last_extreme_type = None
    
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

def analyze_single_period(stock_info, ma_periods=[20, 60], zz_threshold=0.05):
    """分析单个时间周期"""
    data = stock_info['data']
    closes = [d['close'] for d in data]
    
    analysis_data = {}
    
    analysis_data['linear_regression'] = linear_regression_trend(closes)
    
    for period in ma_periods:
        ma = calculate_ma(data, period)
        analysis_data[f'ma{period}'] = ma
        analysis_data[f'ma{period}_trend'] = ma_slope_trend(ma)
    
    analysis_data['support_resistance'] = find_support_resistance(data)
    analysis_data['zigzag'] = zigzag_algorithm(data, zz_threshold)
    
    return analysis_data

def generate_multi_period_chart(stock_name, stock_code, period_results, output_path=None):
    """生成多时间周期分析图表"""
    periods = list(period_results.keys())
    n_periods = len(periods)
    
    fig = plt.figure(figsize=(16, 5 * n_periods))
    
    for i, period in enumerate(periods):
        result = period_results[period]
        stock_info = result['stock_info']
        analysis = result['analysis']
        data = stock_info['data']
        dates = [d['date'] for d in data]
        closes = [d['close'] for d in data]
        
        ax = plt.subplot(n_periods, 1, i + 1)
        ax.plot(dates, closes, label='收盘价', linewidth=2, color='blue')
        
        if 'ma20' in analysis:
            ax.plot(dates, analysis['ma20'], label='MA20', linewidth=1.5, color='orange', alpha=0.8)
        if 'ma60' in analysis:
            ax.plot(dates, analysis['ma60'], label='MA60', linewidth=1.5, color='green', alpha=0.8)
        
        if 'linear_regression' in analysis:
            lr = analysis['linear_regression']
            x = np.arange(len(closes))
            trend_line = lr['slope'] * x + lr['intercept']
            ax.plot(dates, trend_line, label=f'趋势线 (R²={lr["r_squared"]:.3f})', 
                     linewidth=2, color='red', linestyle='--')
        
        period_label = {
            'weekly': '周线',
            'daily': '日线',
            'hourly': '小时线',
            'minute': '分钟线'
        }.get(period, period)
        
        ax.set_title(f'{stock_name} ({stock_code}) - {period_label}分析', fontsize=12, fontweight='bold')
        ax.legend(loc='upper left', fontsize=8)
        ax.grid(True, alpha=0.3)
        plt.xticks(rotation=45)
        ax.xaxis.set_major_locator(plt.MaxNLocator(8))
        
        summary_text = f"{period_label}总结:\n"
        if 'linear_regression' in analysis:
            lr = analysis['linear_regression']
            summary_text += f"趋势: {'📈上涨' if lr['trend'] == 'up' else '📉下跌'} | R²: {lr['r_squared']:.3f}\n"
        if 'ma20_trend' in analysis:
            ma20 = analysis['ma20_trend']
            summary_text += f"MA20: {'向上' if ma20['trend'] == 'up' else '向下'}\n"
        if 'ma60_trend' in analysis:
            ma60 = analysis['ma60_trend']
            summary_text += f"MA60: {'向上' if ma60['trend'] == 'up' else '向下'}"
        
        ax.text(0.02, 0.98, summary_text, transform=ax.transAxes,
                fontsize=9, verticalalignment='top',
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

def analyze_stock_multi_period(query, periods=['weekly', 'daily'], count=60):
    """多时间周期股票分析"""
    stocks = search_stock_by_name(query)
    
    if not stocks:
        return {'success': False, 'error': f'未找到名为 "{query}" 的股票'}
    
    if len(stocks) > 1:
        return {'success': False, 'error': '找到多个匹配公司', 'candidates': stocks}
    
    stock = stocks[0]
    stock_code = stock['code']
    market = stock['market']
    stock_name = stock['name']
    
    print(f"🔍 正在分析: {stock_name} ({stock.get('type', '')})")
    
    period_results = {}
    
    for period in periods:
        print(f"  正在获取{period}数据...")
        history_result = get_stock_history(stock_code, market, period, count)
        
        if history_result['success']:
            analysis = analyze_single_period(history_result)
            period_results[period] = {
                'stock_info': history_result,
                'analysis': analysis
            }
            print(f"  ✅ {period}分析完成")
        else:
            print(f"  ❌ {period}获取失败: {history_result.get('error', '未知错误')}")
            print(f"  ⏭️  跳过{period}，继续下一个周期")
    
    if not period_results:
        return {'success': False, 'error': '所有时间周期数据获取失败'}
    
    return {
        'success': True,
        'stock_name': stock_name,
        'stock_code': f'{stock_code}.{market}',
        'period_results': period_results
    }

def main():
    if len(sys.argv) < 2:
        print("使用方法: python stock_analyzer.py <公司名称或股票代码> [周期数]")
        print("示例:")
        print("  python stock_analyzer.py 阳光电源 60")
        print("  python stock_analyzer.py 腾讯控股 60")
        print("  python stock_analyzer.py AAPL 60")
        sys.exit(1)
    
    query = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    
    periods = ['weekly', 'daily', 'hourly', 'minute']
    result = analyze_stock_multi_period(query, periods, count)
    
    if not result['success']:
        print(f"❌ {result['error']}")
        if 'candidates' in result:
            print("\n📋 候选公司列表：")
            for stock in result['candidates']:
                print(f"  • {stock['name']} ({stock['code']}.{stock['market']}) - {stock.get('type', '')}")
        sys.exit(1)
    
    stock_name = result['stock_name']
    stock_code = result['stock_code']
    period_results = result['period_results']
    
    print(f"\n✅ 多时间周期分析完成: {stock_name} ({stock_code})")
    print(f"\n📊 各时间周期分析:")
    
    for period, pr in period_results.items():
        period_label = {
            'weekly': '周线',
            'daily': '日线',
            'hourly': '小时线',
            'minute': '分钟线'
        }.get(period, period)
        
        analysis = pr['analysis']
        lr = analysis['linear_regression']
        ma20 = analysis.get('ma20_trend', {}).get('trend', 'N/A')
        ma60 = analysis.get('ma60_trend', {}).get('trend', 'N/A')
        
        print(f"\n  {period_label}:")
        print(f"    趋势: {'📈上涨' if lr['trend'] == 'up' else '📉下跌'} (R²: {lr['r_squared']:.3f})")
        print(f"    MA20: {'向上' if ma20 == 'up' else '向下'}")
        print(f"    MA60: {'向上' if ma60 == 'up' else '向下'}")
    
    import os
    output_dir = "/root/.openclaw"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    output_path = f"{output_dir}/{stock_code}_multi_period_analysis.png"
    generate_multi_period_chart(stock_name, stock_code, period_results, output_path)
    
    print(f"\nMEDIA: {output_path}")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
