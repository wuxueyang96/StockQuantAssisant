import os


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _get_data_dir():
    env = os.environ.get('STOCKQUANT_DATA_DIR')
    if env:
        return env
    return os.path.join(os.path.expanduser('~'), '.stockquant', 'data')


class Config:
    DATA_DIR = _get_data_dir()

    OSS_BUCKET = os.environ.get('OSS_BUCKET')
    OSS_ENDPOINT = os.environ.get('OSS_ENDPOINT')
    OSS_REGION = os.environ.get('OSS_REGION', 'us-east-1')
    OSS_ACCESS_KEY_ID = os.environ.get('OSS_ACCESS_KEY_ID')
    OSS_ACCESS_KEY_SECRET = os.environ.get('OSS_ACCESS_KEY_SECRET')

    METADATA_DB_PATH = os.path.join(DATA_DIR, 'metadata.db')

    DATA_SOURCE = os.environ.get('STOCKQUANT_DATA_SOURCE', 'itick').lower()
    ITICK_TOKEN = os.environ.get('ITICK_TOKEN') or os.environ.get('ITICK_API_KEY')
    ITICK_BASE_URL = os.environ.get('ITICK_BASE_URL', 'https://api-free.itick.org/stock')
    ITICK_PAGE_LIMIT = int(os.environ.get('ITICK_PAGE_LIMIT', '1000'))
    ITICK_MAX_PAGES = int(os.environ.get('ITICK_MAX_PAGES', '30'))
    ITICK_RETRIES = int(os.environ.get('ITICK_RETRIES', '3'))
    ITICK_PAGE_DELAY_SECONDS = float(os.environ.get('ITICK_PAGE_DELAY_SECONDS', '0.6'))
    ITICK_FREE_MODE = _env_bool('ITICK_FREE_MODE', True)
    ITICK_FREE_MIN_INTERVAL_SECONDS = float(os.environ.get('ITICK_FREE_MIN_INTERVAL_SECONDS', '13'))
    ITICK_FREE_REFRESH_LIMIT = int(os.environ.get('ITICK_FREE_REFRESH_LIMIT', '1000'))
    AUTO_COLLECT_ENABLED = _env_bool('STOCKQUANT_AUTO_COLLECT', not ITICK_FREE_MODE)

    # 采集层只拉取最细粒度的 5min K 线，daily / 60min / 90min / 120min 由 app.services.resample
    # 在运行时合成。这样能同时绕开三件事：
    # 1) Yahoo 不支持 120m（任何市场都报 Invalid input）；
    # 2) yfinance 对 A 股小时线按美股 RTH 切片导致 12:30 出现伪 K 线；
    # 3) Yahoo 港股小时线返回空。
    COLLECT_INTERVAL = '5min'
    INITIAL_5MIN_HISTORY_DAYS = 200
    REFRESH_5MIN_HISTORY_DAYS = 7
    INTERVAL_MAP = {
        '5min': {'period': f'{INITIAL_5MIN_HISTORY_DAYS}d', 'interval': '5m'},
    }
    INTERVAL_MINUTES = {
        '5min': 5,
        '60min': 60,
        '90min': 90,
        '120min': 120,
        'daily': 24 * 60,
    }
    # 决策 API 固定为「日线趋势 + 60/90/120 结构 + 日线序列」，见 DecisionEngine.summary_integrated
    # 下列元组仅表示 resample 可能产出的目标周期（供调度/文档引用），**不是** HTTP 决策维度枚举。
    RESAMPLE_INTERVALS = ('5min', '60min', '90min', '120min', 'daily')

    YFINANCE_TICKER_MAP = {
        'a': lambda code: f"{code}.SS" if code.startswith(('5', '6')) else f"{code}.SZ",
        'hk': lambda code: f"{int(code):04d}.HK",
        'us': lambda code: code,
    }

    TRADING_HOURS = {
        'a': {'start': '09:30', 'end': '15:00', 'tz': 'Asia/Shanghai'},
        'hk': {'start': '09:30', 'end': '16:00', 'tz': 'Asia/Hong_Kong'},
        'us': {'start': '09:30', 'end': '16:00', 'tz': 'America/New_York'},
    }
