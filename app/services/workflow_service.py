import logging
from datetime import datetime
from typing import Optional
from app.config import Config
from app.services.stock_service import (
    detect_market, get_workflow_id, get_table_name, collect_and_store,
    format_stock_code, MARKET_LABEL
)
from app.models.database import db_manager

logger = logging.getLogger(__name__)


class WorkflowService:
    def __init__(self):
        self.workflows = self.load_workflows()

    def get_workflow_id(self, market: str, stock_code: str, interval: str) -> str:
        return get_workflow_id(market, stock_code, interval)

    # 采集层只保留一个粒度：5min。daily/60min/90min/120min 由 resample 运行时合成。
    _COLLECT_INTERVALS = ('5min',)

    def check_existing_workflows_for_code(self, market: str, stock_code: str) -> Optional[list[str]]:
        existing = []
        for interval in self._COLLECT_INTERVALS:
            wf_id = self.get_workflow_id(market, stock_code, interval)
            if wf_id in self.workflows:
                existing.append(wf_id)
        return existing if existing else None

    def _fill_empty_tables(self, market: str, stock_code: str):
        for interval in self._COLLECT_INTERVALS:
            table_name = get_table_name(market, stock_code, interval)
            if not db_manager.table_exists(market, table_name) or db_manager.get_latest_timestamp(market, table_name) is None:
                if Config.ITICK_FREE_MODE:
                    logger.info(f"free mode: 跳过 {table_name} 的自动初始拉取")
                    continue
                logger.info(f"表 {table_name} 为空，立即拉取初始数据...")
                try:
                    rows = collect_and_store(market, stock_code, interval, skip_trading_check=True)
                    if rows > 0:
                        logger.info(f"写入 {rows} 条数据到 {table_name}")
                except Exception as e:
                    logger.warning(f"数据拉取失败 ({table_name}): {e}")

    def _register_one_market(self, market: str, stock_code: str) -> list[str]:
        created = []
        for interval in self._COLLECT_INTERVALS:
            wf_id = self.get_workflow_id(market, stock_code, interval)
            table_name = get_table_name(market, stock_code, interval)

            if not db_manager.table_exists(market, table_name):
                db_manager.create_stock_table(market, table_name)

            if db_manager.get_latest_timestamp(market, table_name) is None:
                if Config.ITICK_FREE_MODE:
                    logger.info(f"free mode: 工作流 {wf_id} 已注册，跳过自动初始拉取")
                else:
                    logger.info(f"表 {table_name} 为空，立即拉取初始数据...")
                    try:
                        collect_and_store(market, stock_code, interval, skip_trading_check=True)
                    except Exception as e:
                        logger.warning(f"数据拉取失败 ({wf_id}): {e}，工作流已注册但暂无数据")

            wf_data = {
                'market': market,
                'stock_code': stock_code,
                'interval': interval,
                'table': table_name,
                'db_path': '',
                'created_at': datetime.now().isoformat(),
                'active': True
            }
            self.workflows[wf_id] = wf_data
            db_manager.save_workflow(wf_id, wf_data)
            created.append(wf_id)
        return created

    def register_stock(self, stock_input: str) -> dict:
        detections = detect_market(stock_input)

        all_workflows = []
        is_new = False
        for market, stock_code in detections:
            existing = self.check_existing_workflows_for_code(market, stock_code)
            if existing:
                all_workflows.extend(existing)
                self._fill_empty_tables(market, stock_code)
            else:
                is_new = True
                created = self._register_one_market(market, stock_code)
                all_workflows.extend(created)

        return {
            'success': True,
            'message': '工作流已创建' if is_new else '工作流已存在',
            'workflows': all_workflows,
            'markets': [{'market': d[0], 'stock_code': d[1]} for d in detections],
        }

    def get_stock_workflows(self, stock_code: str) -> list[dict]:
        result = []
        for wf_id, wf_data in self.workflows.items():
            if wf_data['stock_code'] == stock_code:
                display_code = format_stock_code(wf_data['market'], wf_data['stock_code'])
                result.append({
                    'id': wf_id,
                    'market': wf_data['market'],
                    'stock_code': wf_data['stock_code'],
                    'display_code': display_code,
                    'interval': wf_data['interval'],
                    'table': wf_data['table'],
                    'active': wf_data.get('active', True),
                    'created_at': wf_data.get('created_at'),
                })
        return result

    def get_all_workflows(self) -> list[dict]:
        result = []
        for wf_id, wf_data in self.workflows.items():
            display_code = format_stock_code(wf_data['market'], wf_data['stock_code'])
            result.append({
                'id': wf_id,
                'market': wf_data['market'],
                'stock_code': wf_data['stock_code'],
                'display_code': display_code,
                'interval': wf_data['interval'],
                'table': wf_data['table'],
                'active': wf_data.get('active', True),
                'created_at': wf_data.get('created_at'),
            })
        return result

    def delete_workflow(self, workflow_id: str) -> bool:
        if workflow_id in self.workflows:
            del self.workflows[workflow_id]
            db_manager.delete_workflow_by_id(workflow_id)
            return True
        return False

    def load_workflows(self) -> dict:
        return db_manager.load_workflows()


workflow_service = WorkflowService()
