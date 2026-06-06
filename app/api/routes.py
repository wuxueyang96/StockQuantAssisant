from flask import Blueprint, request, jsonify
import re
import pandas as pd
from app.services.registration_service import registration_service
from app.models.database import db_manager
from app.services.analysis_service import analyze_stock
from app.services.backtest_service import run_backtest
from app.services.chart_data_service import build_chart_data
from app.services.data_job_service import data_job_service
from app.services.data_service import (
    backfill_data,
    clear_stock_data,
    estimate_backfill_api_usage,
    get_data_status,
    refresh_all_registered,
    refresh_data,
)

api_bp = Blueprint('api', __name__)


def _normalize_register_code(market: str, code: str) -> str:
    value = (code or '').strip().upper()
    if '.' in value:
        value = value.rsplit('.', 1)[0]
    if market == 'a':
        if not re.match(r'^\d{6}$', value):
            raise ValueError('A 股代码必须是 6 位数字')
        return value
    if market == 'hk':
        if not re.match(r'^\d{1,5}$', value):
            raise ValueError('港股代码必须是 1-5 位数字')
        return value.zfill(5)
    if market == 'us':
        if not re.match(r'^[A-Z0-9]{1,10}$', value):
            raise ValueError('美股代码必须是 1-10 位字母或数字')
        return value
    raise ValueError('market 必须是 a/hk/us')


def _stock_code_kwargs(market: str, code: str) -> dict:
    if market == 'a':
        return {'a_code': code}
    if market == 'hk':
        return {'hk_code': code}
    if market == 'us':
        return {'us_code': code}
    raise ValueError('market 必须是 a/hk/us')


@api_bp.route('/stock/register', methods=['POST'])
def register_stock():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '缺少 stock 或 name 参数'}), 400

    name = (data.get('name') or '').strip()
    if name:
        market = (data.get('market') or '').strip().lower()
        raw_code = str(data.get('code') or '').strip()
        if not market or not raw_code:
            return jsonify({'success': False, 'message': '名称注册需要 market 和 code 参数'}), 400
        try:
            code = _normalize_register_code(market, raw_code)
            db_manager.upsert_stock_code(name=name, **_stock_code_kwargs(market, code))
            stock = name
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400
    else:
        if 'stock' not in data:
            return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400
        stock = data['stock'].strip()

    if not stock:
        return jsonify({'success': False, 'message': 'stock 参数不能为空'}), 400

    try:
        return jsonify(registration_service.register_stock(stock))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'服务器错误: {str(e)}'}), 500


@api_bp.route('/stock/code', methods=['POST'])
def upsert_stock_code():
    data = request.get_json()
    if not data or 'name' not in data:
        return jsonify({'success': False, 'message': '缺少 name 参数'}), 400

    name = data['name'].strip()
    if not name:
        return jsonify({'success': False, 'message': 'name 不能为空'}), 400

    markets = {}
    for key in ('a', 'hk', 'us'):
        raw_code = str(data.get(key) or '').strip()
        if raw_code:
            markets[key] = raw_code

    if not markets:
        return jsonify({'success': False, 'message': '至少需要提供 a、hk、us 中的一个市场代码'}), 400

    try:
        db_manager.upsert_stock_code(
            name=name,
            a_code=_normalize_register_code('a', markets['a']) if 'a' in markets else None,
            hk_code=_normalize_register_code('hk', markets['hk']) if 'hk' in markets else None,
            us_code=_normalize_register_code('us', markets['us']) if 'us' in markets else None,
        )
        return jsonify({'success': True, 'message': f'股票映射 "{name}" 已保存'})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'服务器错误: {str(e)}'}), 500


@api_bp.route('/stock/codes', methods=['GET'])
def get_stock_codes():
    try:
        df = db_manager.get_all_stock_codes()
        rows = []
        for _, row in df.iterrows():
            rows.append({
                'name': row['name'],
                'a_code': row.get('a_code') if not pd.isna(row.get('a_code')) else None,
                'hk_code': row.get('hk_code') if not pd.isna(row.get('hk_code')) else None,
                'us_code': row.get('us_code') if not pd.isna(row.get('us_code')) else None,
            })
        return jsonify({'success': True, 'count': len(rows), 'codes': rows})
    except Exception as e:
        return jsonify({'success': False, 'message': f'服务器错误: {str(e)}'}), 500


@api_bp.route('/stock/decision', methods=['POST'])
def stock_decision():
    data = request.get_json()
    if not data or 'stock' not in data:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400

    stock = data['stock'].strip()
    if not stock:
        return jsonify({'success': False, 'message': 'stock 参数不能为空'}), 400

    interval = data.get('interval', 'daily')
    if interval not in ('daily', '120min', '90min', '60min'):
        return jsonify({'success': False, 'message': 'interval 必须是 daily/120min/90min/60min'}), 400

    try:
        result = analyze_stock(stock, interval=interval)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'服务器错误: {str(e)}'}), 500


@api_bp.route('/stock/data-status', methods=['GET'])
def stock_data_status():
    stock = (request.args.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400
    try:
        return jsonify(get_data_status(stock))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'数据状态查询失败: {str(e)}'}), 500


@api_bp.route('/stock/clear-data', methods=['POST'])
def stock_clear_data():
    data = request.get_json() or {}
    stock = (data.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400
    try:
        return jsonify(clear_stock_data(stock))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'清理数据失败: {str(e)}'}), 500


@api_bp.route('/stock/refresh', methods=['POST'])
def stock_refresh():
    data = request.get_json() or {}
    stock = (data.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400
    try:
        history_days = data.get('history_days')
        history_days = int(history_days) if history_days not in (None, '') else None
        return jsonify(refresh_data(stock, history_days=history_days))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'刷新失败: {str(e)}'}), 500


@api_bp.route('/refresh', methods=['POST'])
def refresh_registered_stocks():
    """Force refresh all stocks in the code registry."""
    data = request.get_json() or {}
    try:
        history_days = data.get('history_days')
        history_days = int(history_days) if history_days not in (None, '') else None
        return jsonify(refresh_all_registered(history_days=history_days))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'刷新失败: {str(e)}'}), 500


@api_bp.route('/stock/backfill', methods=['POST'])
def stock_backfill():
    data = request.get_json() or {}
    stock = (data.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400
    try:
        days = data.get('days')
        days = int(days) if days not in (None, '') else None
        if data.get('queued') or data.get('async'):
            job = data_job_service.enqueue_backfill(
                stock,
                days=days,
                start_date=data.get('start_date'),
                end_date=data.get('end_date'),
                source=data.get('source'),
            )
            return jsonify({
                'success': True,
                'queued': True,
                'job_id': job['id'],
                'job': job,
                'estimate': job.get('estimate'),
            })
        payload = backfill_data(
            stock,
            days=days,
            start_date=data.get('start_date'),
            end_date=data.get('end_date'),
        )
        return jsonify(payload)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'补历史失败: {str(e)}'}), 500


@api_bp.route('/stock/backfill-estimate', methods=['GET'])
def stock_backfill_estimate():
    stock = (request.args.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400
    try:
        days = request.args.get('days')
        days = int(days) if days not in (None, '') else None
        return jsonify(estimate_backfill_api_usage(stock, days=days))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'估算失败: {str(e)}'}), 500


@api_bp.route('/data-jobs/<job_id>', methods=['GET'])
def get_data_job(job_id):
    job = data_job_service.get_job(job_id)
    if not job:
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    return jsonify({'success': True, 'job': job})


@api_bp.route('/data-jobs', methods=['GET'])
def list_data_jobs():
    try:
        limit = request.args.get('limit')
        limit = int(limit) if limit not in (None, '') else 50
        jobs = data_job_service.list_jobs(
            status=(request.args.get('status') or '').strip() or None,
            stock=(request.args.get('stock') or '').strip() or None,
            limit=limit,
        )
        return jsonify({'success': True, 'count': len(jobs), 'jobs': jobs})
    except Exception as e:
        return jsonify({'success': False, 'message': f'任务列表查询失败: {str(e)}'}), 500


@api_bp.route('/data-jobs/<job_id>/tasks', methods=['GET'])
def list_data_job_tasks(job_id):
    try:
        tasks = data_job_service.get_tasks(job_id)
        return jsonify({'success': True, 'job_id': job_id, 'count': len(tasks), 'tasks': tasks})
    except KeyError:
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': f'Task 查询失败: {str(e)}'}), 500


@api_bp.route('/data-jobs/<job_id>/tasks/<task_id>/retry', methods=['POST'])
def retry_data_task(job_id, task_id):
    data = request.get_json() or {}
    try:
        task = data_job_service.retry_task(
            job_id,
            task_id,
            source=(data.get('source') or '').strip() or None,
        )
        return jsonify({'success': True, 'task': task})
    except KeyError:
        return jsonify({'success': False, 'message': 'Task 不存在'}), 404
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'Task 重试失败: {str(e)}'}), 500


@api_bp.route('/data-sources', methods=['GET'])
def list_sources():
    return jsonify({
        'success': True,
        'sources': data_job_service.data_sources((request.args.get('market') or '').strip() or None),
    })


@api_bp.route('/stock/<stock_code>/registrations', methods=['GET'])
def get_stock_registrations(stock_code):
    registrations = registration_service.get_stock_registrations(stock_code)
    return jsonify({
        'success': True,
        'stock_code': stock_code,
        'registrations': registrations
    })


@api_bp.route('/registered-stocks', methods=['GET'])
def get_all_registered_stocks():
    registered_stocks = registration_service.get_all_registered_stocks()
    return jsonify({
        'success': True,
        'count': len(registered_stocks),
        'registered_stocks': registered_stocks
    })


@api_bp.route('/registered-stocks/<registration_id>', methods=['DELETE'])
def delete_registered_stock(registration_id):
    clear_data = str(request.args.get('clear_data') or '').lower() in ('1', 'true', 'yes')
    rows_cleared = 0
    registration = registration_service.registered_stocks.get(registration_id)
    if registration and clear_data:
        stats = db_manager.get_table_stats(registration['market'], registration['table'])
        rows_cleared = int(stats.get('rows') or 0)
        db_manager.drop_table(registration['market'], registration['table'])
    deleted = registration_service.delete_registration(registration_id)

    if deleted:
        return jsonify({
            'success': True,
            'message': f'注册股票 {registration_id} 已删除',
            'data_cleared': clear_data,
            'rows_cleared': rows_cleared,
        })
    return jsonify({'success': False, 'message': f'注册股票 {registration_id} 不存在'}), 404


@api_bp.route('/stock/unregister', methods=['POST'])
def unregister_stock():
    data = request.get_json() or {}
    stock = (data.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400
    try:
        return jsonify(registration_service.unregister_stock(
            stock,
            clear_data=bool(data.get('clear_data')),
        ))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'取消注册失败: {str(e)}'}), 500


def _parse_bars(default: int = 180) -> int:
    try:
        bars = int(request.args.get('bars', str(default)))
    except ValueError:
        raise ValueError('bars 必须是整数')
    return max(20, min(bars, 500))


@api_bp.route('/stock/chart-data', methods=['GET'])
def stock_chart_data():
    """返回浏览器绘图用 JSON，不再由后端渲染 PNG。"""
    stock = (request.args.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400

    try:
        bars = _parse_bars(default=180)
    except ValueError:
        return jsonify({'success': False, 'message': 'bars 必须是整数'}), 400

    try:
        payload = build_chart_data(stock, bars=bars)
        return jsonify(payload)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'图表数据生成失败: {e}'}), 500


@api_bp.route('/stock/chart', methods=['GET'])
def stock_chart_legacy():
    """Deprecated compatibility alias for /stock/chart-data."""
    return stock_chart_data()


@api_bp.route('/stock/backtest', methods=['POST'])
def stock_backtest():
    data = request.get_json() or {}
    stock = (data.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400

    allowed = {
        'start_date', 'end_date', 'initial_cash', 'commission_rate',
        'slippage_bps', 'min_bars', 'lot_size', 'benchmark',
    }
    kwargs = {k: data[k] for k in allowed if k in data}

    try:
        kwargs['initial_cash'] = float(kwargs.get('initial_cash', 100000.0))
        kwargs['commission_rate'] = float(kwargs.get('commission_rate', 0.0003))
        kwargs['slippage_bps'] = float(kwargs.get('slippage_bps', 5.0))
        kwargs['min_bars'] = int(kwargs.get('min_bars', 90))
        kwargs['lot_size'] = int(kwargs.get('lot_size', 1))
        payload = run_backtest(stock, **kwargs)
        return jsonify(payload)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'回测失败: {str(e)}'}), 500


@api_bp.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok'})
