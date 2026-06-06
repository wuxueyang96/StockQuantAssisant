from flask import Blueprint, request, jsonify
import pandas as pd
from app.services.workflow_service import workflow_service
from app.scheduler.job_scheduler import job_scheduler
from app.models.database import db_manager
from app.services.analysis_service import analyze_stock
from app.services.backtest_service import run_backtest
from app.services.chart_data_service import build_chart_data
from app.services.data_job_service import data_job_service
from app.services.data_service import (
    backfill_data,
    estimate_backfill_api_usage,
    get_data_status,
    refresh_all_registered,
    refresh_data,
)

api_bp = Blueprint('api', __name__)


@api_bp.route('/stock/register', methods=['POST'])
def register_stock():
    data = request.get_json()
    if not data or 'stock' not in data:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400

    stock = data['stock'].strip()
    if not stock:
        return jsonify({'success': False, 'message': 'stock 参数不能为空'}), 400

    try:
        result = workflow_service.register_stock(stock)

        if result['success'] and '已创建' in result['message']:
            for wf_id in result['workflows']:
                wf_data = workflow_service.workflows.get(wf_id)
                if wf_data:
                    job_scheduler.add_workflow_job(wf_id, wf_data)

        return jsonify(result)
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

    if not any(k in data for k in ('a', 'hk', 'us')):
        return jsonify({'success': False, 'message': '至少需要提供 a、hk、us 中的一个市场代码'}), 400

    try:
        db_manager.upsert_stock_code(
            name=name,
            a_code=data.get('a'),
            hk_code=data.get('hk'),
            us_code=data.get('us'),
        )
        return jsonify({'success': True, 'message': f'股票映射 "{name}" 已保存'})
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


@api_bp.route('/stock/<stock_code>/workflows', methods=['GET'])
def get_stock_workflows(stock_code):
    workflows = workflow_service.get_stock_workflows(stock_code)
    return jsonify({
        'success': True,
        'stock_code': stock_code,
        'workflows': workflows
    })


@api_bp.route('/workflows', methods=['GET'])
def get_all_workflows():
    workflows = workflow_service.get_all_workflows()
    return jsonify({
        'success': True,
        'count': len(workflows),
        'workflows': workflows
    })


@api_bp.route('/workflows/<workflow_id>', methods=['DELETE'])
def delete_workflow(workflow_id):
    job_scheduler.remove_workflow_job(workflow_id)
    deleted = workflow_service.delete_workflow(workflow_id)

    if deleted:
        return jsonify({'success': True, 'message': f'工作流 {workflow_id} 已删除'})
    else:
        return jsonify({'success': False, 'message': f'工作流 {workflow_id} 不存在'}), 404


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
    return jsonify({'status': 'ok', 'scheduler_running': job_scheduler.scheduler.running})
