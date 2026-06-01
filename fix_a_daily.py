"""修复 A 股日线数据 —— 带重试和限速"""
import time, sys, pandas as pd, akshare as ak, duckdb
from datetime import datetime

DATA_DIR = "/root/.stockquant/data"
A_STOCKS = {
    "600309": "万华化学", "600031": "三一重工", "601318": "中国平安",
    "601088": "中国神华", "688981": "中芯国际", "000858": "五粮液",
    "300750": "宁德时代", "600276": "恒瑞医药", "600036": "招商银行",
    "000651": "格力电器", "002594": "比亚迪", "603288": "海天味业",
    "600690": "海尔智家", "002415": "海康威视", "002475": "立讯精密",
    "000333": "美的集团", "300274": "阳光电源", "600519": "贵州茅台",
    "300760": "迈瑞医疗", "600900": "长江电力", "601012": "隆基绿能",
    "600887": "伊利股份", "603501": "韦尔股份",
}

end = datetime.now().strftime('%Y%m%d')
start = (datetime.now() - pd.DateOffset(years=1)).strftime('%Y%m%d')

conn = duckdb.connect(f"{DATA_DIR}/a_stock.db")
ok = fail = 0

for code, name in A_STOCKS.items():
    tbl = f"A_{code}.SZ_daily" if code[0] in "03" else f"A_{code}.SS_daily"
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period='daily',
                                   start_date=start, end_date=end, adjust='qfq')
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                print(f"  FAIL {name}({code}): {e}")
                df = None
    if df is None or df.empty:
        fail += 1
        continue
    
    df = df.rename(columns={'日期': 'timestamp', '开盘': 'Open', '最高': 'High',
        '最低': 'Low', '收盘': 'Close', '成交量': 'Volume'})
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df[['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume']]
    df['Dividends'] = 0.0
    df['Stock Splits'] = 0.0
    
    last = df.tail(3)
    conn.execute(f"INSERT OR REPLACE INTO \"{tbl}\" SELECT * FROM last")
    c = last['Close'].iloc[-1]
    d = last['timestamp'].iloc[-1].strftime('%Y-%m-%d')
    print(f"  OK  {name}({code}) {d} close={c}")
    ok += 1
    time.sleep(0.8)

conn.close()
print(f"\nDone: OK={ok} FAIL={fail}")
