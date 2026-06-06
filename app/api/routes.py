from flask import Blueprint, request, jsonify, Response
import pandas as pd
from app.services.workflow_service import workflow_service
from app.scheduler.job_scheduler import job_scheduler
from app.models.database import db_manager
from app.services.analysis_service import analyze_stock
from app.services.stock_service import detect_market, get_table_name, format_stock_code, collect_and_store
from app.services.resample import resample_ohlcv
from app.services.chart_service import render_chart_png, render_integrated_dashboard_png
from app.algos.decision import DecisionEngine

api_bp = Blueprint('api', __name__)
_chart_engine = DecisionEngine()


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


@api_bp.route('/stock/chart', methods=['GET'])
def stock_chart():
    """渲染行情图，直接返回 image/png。

    Query 参数：
        stock    str  必填，股票代码 / 名称（与 /stock/decision 一致）
        mode     str  默认 `integrated`：日线 K+趋势四轨，下接 60/90/120 的 K+MACD；
                 `single` 时仅画 `interval` 指定的一根周期（旧行为）。
        interval str  仅 `mode=single` 时使用；默认 daily；可选 daily/120min/90min/60min
        bars     int  默认 integrated=90 / single=120，最近 N 根 K 线
    """
    stock = (request.args.get('stock') or '').strip()
    if not stock:
        return jsonify({'success': False, 'message': '缺少 stock 参数'}), 400

    mode = (request.args.get('mode') or 'integrated').lower()
    if mode not in ('integrated', 'single'):
        return jsonify({'success': False, 'message': 'mode 必须是 integrated 或 single'}), 400

    interval = request.args.get('interval', 'daily')
    if interval not in ('daily', '120min', '90min', '60min'):
        return jsonify({
            'success': False,
            'message': 'interval 必须是 daily/120min/90min/60min'
        }), 400

    try:
        default_bars = 90 if mode == 'integrated' else 120
        bars = int(request.args.get('bars', str(default_bars)))
        bars = max(20, min(bars, 500))
    except ValueError:
        return jsonify({'success': False, 'message': 'bars 必须是整数'}), 400

    try:
        detections = detect_market(stock)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    market, code = detections[0]
    table = get_table_name(market, code, '5min')
    raw = db_manager.get_data(market, table, limit=20000)
    if raw is None or raw.empty:
        return jsonify({
            'success': False,
            'message': f'数据表 {table} 不存在或为空，请先注册该股票工作流'
        }), 404

    if 'timestamp' in raw.columns:
        raw = raw.set_index('timestamp')
    raw = raw.sort_index()
    raw = raw.rename(columns={c: c.capitalize() for c in raw.columns})

    _POS_ASCII = {'满仓': 'full', '重仓': 'heavy', '轻仓': 'light',
                  '空仓': 'empty', '冷启动': 'cold'}
    display = format_stock_code(market, code)

    try:
        if mode == 'integrated':
            df_daily = resample_ohlcv(raw, 'daily')
            if df_daily is None or len(df_daily) < 30:
                return jsonify({
                    'success': False,
                    'message': '5min 数据不足以合成日线（需要至少 30 根日线）'
                }), 404
            intraday = {}
            for itv in ('60min', '90min', '120min'):
                dfi = resample_ohlcv(raw, itv)
                if dfi is not None and len(dfi) >= 30:
                    intraday[itv] = dfi
            try:
                summary = _chart_engine.summary_integrated(df_daily, intraday)
                close = summary.get('close')
                pos_label = _POS_ASCII.get(
                    summary.get('position', {}).get('label'),
                    summary.get('position', {}).get('label'),
                )
                action = summary.get('action')
            except Exception:
                close = pos_label = action = None
            title_daily = '  '.join(
                [x for x in [
                    f'{display} | daily+trend',
                    f'close={close}' if close is not None else None,
                    f'pos={pos_label}' if pos_label else None,
                    f'action={action}' if action else None,
                ] if x]
            )
            intraday_titles = {k: f'{display} | {k} + MACD' for k in intraday}
            png = render_integrated_dashboard_png(
                df_daily, intraday, title_daily, intraday_titles=intraday_titles, bars=bars,
            )
        else:
            df = resample_ohlcv(raw, interval)
            if df is None or len(df) < 30:
                return jsonify({
                    'success': False,
                    'message': f'5min 数据不足以合成 {interval}（需要至少 30 根目标周期）'
                }), 404
            try:
                summary = _chart_engine.summary(df)
                close = summary.get('close')
                pos_label = summary.get('position', {}).get('label')
                pos_label = _POS_ASCII.get(pos_label, pos_label)
                action = summary.get('action')
            except Exception:
                close = pos_label = action = None
            title_bits = [f'{display} | {interval}']
            if close is not None:
                title_bits.append(f'close={close}')
            if pos_label:
                title_bits.append(f'pos={pos_label}')
            if action:
                title_bits.append(f'action={action}')
            title = '  '.join(title_bits)
            png = render_chart_png(df=df, title=title, bars=bars)
    except Exception as e:
        return jsonify({'success': False, 'message': f'图表渲染失败: {e}'}), 500

    return Response(png, mimetype='image/png')


@api_bp.route('/refresh', methods=['POST'])
def refresh_data():
    """强制刷新所有已注册股票的 5min K 线数据（跳过交易时间检查）。"""
    df = db_manager.get_all_stock_codes()
    results = []
    for _, row in df.iterrows():
        name = row['name']
        for market_key, code_col in [('a', 'a_code'), ('hk', 'hk_code'), ('us', 'us_code')]:
            code = row.get(code_col)
            if not code or pd.isna(code):
                continue
            try:
                rows = collect_and_store(market_key, code, skip_trading_check=True)
                results.append({'stock': name, 'market': market_key, 'code': code, 'rows': rows, 'status': 'ok'})
            except Exception as e:
                results.append({'stock': name, 'market': market_key, 'code': code, 'rows': 0, 'status': f'error: {e}'})
    total_rows = sum(r['rows'] for r in results if r['status'] == 'ok')
    errors = [r for r in results if r['status'] != 'ok']
    return jsonify({
        'success': True,
        'total_stocks': len(df),
        'total_queries': len(results),
        'total_rows_inserted': total_rows,
        'errors': len(errors),
        'results': results,
    })


@api_bp.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'scheduler_running': job_scheduler.scheduler.running})
