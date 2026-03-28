#!/usr/bin/env python3
"""
通用A股股价查询工具
支持通过公司名称或股票代码查询A股实时股价
"""

import requests
import json
import sys
import re
from datetime import datetime

def search_stock_by_name(company_name):
    """
    通过公司名称搜索股票代码
    使用东方财富网搜索接口
    """
    search_url = f"http://searchapi.eastmoney.com/api/suggest/get?input={company_name}&type=14&token=D43BF722C8E33BD8C8B185F60AA70F4E&count=5"
    
    try:
        response = requests.get(search_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('QuotationCodeTable') and data['QuotationCodeTable'].get('Data'):
                results = data['QuotationCodeTable']['Data']
                # 筛选A股（上海600/601/603/605，深圳000/001/002/300）
                a_stocks = []
                for item in results:
                    code = item.get('Code', '')
                    name = item.get('Name', '')
                    market = item.get('Market', '')
                    
                    # 判断是否为A股
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

def get_stock_price_by_code(stock_code, market=None):
    """
    通过股票代码获取股价
    stock_code: 股票代码（如 300274）
    market: 市场（SH或SZ，可选，会自动判断）
    """
    # 自动判断市场
    if not market:
        if stock_code.startswith('600') or stock_code.startswith('601') or \
           stock_code.startswith('603') or stock_code.startswith('605'):
            market = 'SH'
        else:
            market = 'SZ'
    
    # 尝试多个数据源
    def try_tencent():
        """腾讯财经数据源"""
        prefix = 'sh' if market == 'SH' else 'sz'
        url = f"http://qt.gtimg.cn/q={prefix}{stock_code}"
        try:
            response = requests.get(url, timeout=10)
            response.encoding = 'gbk'
            if response.status_code == 200 and f'{prefix}{stock_code}' in response.text:
                data = response.text
                start = data.find('"') + 1
                end = data.rfind('"')
                stock_data = data[start:end]
                if stock_data:
                    parts = stock_data.split('~')
                    if len(parts) >= 40:
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
                            'stock_code': f'{stock_code}.{market}',
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
    
    def try_sina():
        """新浪财经数据源"""
        prefix = 'sh' if market == 'SH' else 'sz'
        url = f"http://hq.sinajs.cn/list={prefix}{stock_code}"
        try:
            response = requests.get(url, timeout=10)
            response.encoding = 'gbk'
            if response.status_code == 200 and f'{prefix}{stock_code}' in response.text:
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
                            'stock_code': f'{stock_code}.{market}',
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
    
    return {'success': False, 'error': '所有数据源都无法获取数据'}

def query_stock(query):
    """
    通用查询函数
    query: 可以是公司名称或股票代码
    """
    # 判断是否为股票代码（6位数字）
    if re.match(r'^\d{6}$', query):
        # 是股票代码
        result = get_stock_price_by_code(query)
        if result['success']:
            return result
        else:
            return {'success': False, 'error': f'无法找到股票代码 {query} 的信息'}
    else:
        # 是公司名称，先搜索
        stocks = search_stock_by_name(query)
        if not stocks:
            return {'success': False, 'error': f'未找到名为 "{query}" 的A股上市公司'}
        
        if len(stocks) == 1:
            # 只有一个结果，直接查询
            stock = stocks[0]
            result = get_stock_price_by_code(stock['code'], stock['market'])
            return result
        else:
            # 多个结果，返回候选列表
            return {
                'success': False,
                'error': f'找到多个匹配的公司，请选择具体的股票代码',
                'candidates': stocks
            }

def print_stock_info(result):
    """
    格式化输出股票信息
    """
    if not result['success']:
        print(f"❌ {result['error']}")
        if 'candidates' in result:
            print("\n📋 候选公司列表：")
            for stock in result['candidates']:
                print(f"  • {stock['name']} ({stock['code']}.{stock['market']})")
        return
    
    print(f"\n📊 {result['name']} ({result['stock_code']})")
    print("=" * 50)
    print(f"💵 当前价格: ¥{result['current_price']}")
    
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
    print(f"📊 数据来源: {result['source']}")
    print(f"⏰ 更新时间: {result['timestamp']}")
    print("=" * 50)

def main():
    if len(sys.argv) < 2:
        print("使用方法: python get_a_stock_price.py <公司名称或股票代码>")
        print("示例:")
        print("  python get_a_stock_price.py 阳光电源")
        print("  python get_a_stock_price.py 300274")
        print("  python get_a_stock_price.py 贵州茅台")
        sys.exit(1)
    
    query = ' '.join(sys.argv[1:])
    print(f"🔍 正在查询: {query}")
    result = query_stock(query)
    print_stock_info(result)
    
    if '--json' in sys.argv:
        print("\n" + json.dumps(result, ensure_ascii=False, indent=2))
    
    return 0 if result['success'] else 1

if __name__ == '__main__':
    sys.exit(main())
