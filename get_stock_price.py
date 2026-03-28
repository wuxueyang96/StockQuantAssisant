#!/usr/bin/env python3
"""
获取阳光电源股价的工具
股票代码：300274.SZ（阳光电源）
"""

import requests
import json
import sys
from datetime import datetime

def get_sungrow_price():
    """
    获取阳光电源（300274.SZ）的最新股价
    尝试多个数据源
    """
    
    # 数据源1: 腾讯财经
    def try_tencent():
        url = "http://qt.gtimg.cn/q=sz300274"
        try:
            response = requests.get(url, timeout=10)
            response.encoding = 'gbk'
            if response.status_code == 200 and 'sz300274' in response.text:
                data = response.text
                start = data.find('"') + 1
                end = data.rfind('"')
                stock_data = data[start:end]
                if stock_data:
                    parts = stock_data.split('~')
                    if len(parts) >= 40:
                        # 腾讯财经数据格式
                        name = parts[1]
                        current_price = parts[3]
                        open_price = parts[5]
                        prev_close = parts[4]
                        high = parts[33]
                        low = parts[34]
                        volume = parts[6]
                        amount = parts[37]
                        
                        try:
                            current = float(current_price)
                            prev = float(prev_close)
                            change = current - prev
                            change_percent = (change / prev) * 100 if prev != 0 else 0
                        except:
                            change = 0
                            change_percent = 0
                        
                        return {
                            'success': True,
                            'stock_code': '300274.SZ',
                            'name': name,
                            'current_price': current_price,
                            'open_price': open_price,
                            'prev_close': prev_close,
                            'high': high,
                            'low': low,
                            'volume': volume,
                            'amount': amount,
                            'change': round(change, 2),
                            'change_percent': round(change_percent, 2),
                            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'source': 'tencent'
                        }
        except Exception as e:
            print(f"腾讯财经数据源失败: {e}")
        return None
    
    # 数据源2: 新浪财经
    def try_sina():
        url = "http://hq.sinajs.cn/list=sz300274"
        try:
            response = requests.get(url, timeout=10)
            response.encoding = 'gbk'
            if response.status_code == 200 and 'sz300274' in response.text:
                data = response.text
                start = data.find('"') + 1
                end = data.rfind('"')
                stock_data = data[start:end]
                if stock_data:
                    parts = stock_data.split(',')
                    if len(parts) >= 32:
                        name = parts[0]
                        current_price = parts[3]
                        open_price = parts[1]
                        prev_close = parts[2]
                        high = parts[4]
                        low = parts[5]
                        volume = parts[8]
                        amount = parts[9]
                        
                        try:
                            current = float(current_price)
                            prev = float(prev_close)
                            change = current - prev
                            change_percent = (change / prev) * 100 if prev != 0 else 0
                        except:
                            change = 0
                            change_percent = 0
                        
                        return {
                            'success': True,
                            'stock_code': '300274.SZ',
                            'name': name,
                            'current_price': current_price,
                            'open_price': open_price,
                            'prev_close': prev_close,
                            'high': high,
                            'low': low,
                            'volume': volume,
                            'amount': amount,
                            'change': round(change, 2),
                            'change_percent': round(change_percent, 2),
                            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'source': 'sina'
                        }
        except Exception as e:
            print(f"新浪财经数据源失败: {e}")
        return None
    
    # 尝试多个数据源
    result = try_tencent()
    if result:
        return result
    
    result = try_sina()
    if result:
        return result
    
    return {'success': False, 'error': '所有数据源都无法获取数据，请检查网络连接或使用VPN'}

def print_stock_info(result):
    """
    格式化输出股票信息
    """
    if not result['success']:
        print(f"❌ 获取失败: {result['error']}")
        return
    
    print(f"\n📊 {result['name']} ({result['stock_code']})")
    print("=" * 50)
    print(f"💵 当前价格: ¥{result['current_price']}")
    
    # 根据涨跌幅显示颜色
    if result['change'] >= 0:
        print(f"📈 涨跌幅: +{result['change']} (+{result['change_percent']}%)")
    else:
        print(f"📉 涨跌幅: {result['change']} ({result['change_percent']}%)")
    
    print(f"📊 今开: ¥{result['open_price']}")
    print(f"📊 昨收: ¥{result['prev_close']}")
    print(f"📊 最高: ¥{result['high']}")
    print(f"📊 最低: ¥{result['low']}")
    print(f"📊 成交量: {result['volume']}")
    print(f"📊 成交额: ¥{result['amount']}")
    print(f"⏰ 更新时间: {result['timestamp']}")
    print("=" * 50)

def main():
    print("🔍 正在查询阳光电源股价...")
    result = get_sungrow_price()
    print_stock_info(result)
    
    # 返回JSON格式数据（供其他程序调用）
    if '--json' in sys.argv:
        print("\n" + json.dumps(result, ensure_ascii=False, indent=2))
    
    return 0 if result['success'] else 1

if __name__ == '__main__':
    sys.exit(main())
