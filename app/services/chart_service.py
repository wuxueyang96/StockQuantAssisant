"""Deprecated chart rendering compatibility module.

Charts are rendered in the WebUI from `/api/stock/chart-data`.  These old PNG
helpers are intentionally kept as import-safe stubs so the runtime no longer
depends on matplotlib or mplfinance.
"""


def _deprecated():
    raise RuntimeError('chart_service is deprecated; use /api/stock/chart-data')


def render_chart_png(*args, **kwargs):
    _deprecated()


def render_intraday_macd_png(*args, **kwargs):
    _deprecated()


def render_integrated_dashboard_png(*args, **kwargs):
    _deprecated()
