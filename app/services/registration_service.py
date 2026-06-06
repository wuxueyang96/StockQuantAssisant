import logging
from datetime import datetime
from typing import Optional

from app.models.database import db_manager
from app.services.stock_service import (
    MARKET_LABEL,
    detect_market,
    format_stock_code,
    get_registration_id,
    get_table_name,
)

logger = logging.getLogger(__name__)


class RegistrationService:
    # 采集层只保留一个粒度：5min。daily/60min/90min/120min 由 resample 运行时合成。
    _COLLECT_INTERVALS = ('5min',)

    def __init__(self):
        self.registered_stocks = self.load_registered_stocks()

    def get_registration_id(self, market: str, stock_code: str, interval: str) -> str:
        return get_registration_id(market, stock_code, interval)

    def check_existing_for_code(self, market: str, stock_code: str) -> Optional[list[str]]:
        existing = []
        for interval in self._COLLECT_INTERVALS:
            registration_id = self.get_registration_id(market, stock_code, interval)
            if registration_id in self.registered_stocks:
                existing.append(registration_id)
        return existing if existing else None

    def _register_one_market(self, market: str, stock_code: str) -> list[str]:
        created = []
        for interval in self._COLLECT_INTERVALS:
            registration_id = self.get_registration_id(market, stock_code, interval)
            table_name = get_table_name(market, stock_code, interval)

            if not db_manager.table_exists(market, table_name):
                db_manager.create_stock_table(market, table_name)

            registration_data = {
                'market': market,
                'stock_code': stock_code,
                'interval': interval,
                'table': table_name,
                'created_at': datetime.now().isoformat(),
                'active': True,
            }
            self.registered_stocks[registration_id] = registration_data
            db_manager.save_registration(registration_id, registration_data)
            created.append(registration_id)
        return created

    def register_stock(self, stock_input: str) -> dict:
        detections = detect_market(stock_input)

        all_ids = []
        is_new = False
        for market, stock_code in detections:
            existing = self.check_existing_for_code(market, stock_code)
            if existing:
                all_ids.extend(existing)
            else:
                is_new = True
                all_ids.extend(self._register_one_market(market, stock_code))

        return {
            'success': True,
            'message': '股票已注册' if is_new else '股票已存在',
            'registration_ids': all_ids,
            'markets': [{'market': d[0], 'stock_code': d[1]} for d in detections],
        }

    def _stock_name_for(self, market: str, stock_code: str) -> Optional[str]:
        try:
            name = db_manager.find_stock_name_by_code(market, stock_code)
        except Exception:
            return None
        return name if isinstance(name, str) and name else None

    def get_stock_registrations(self, stock_code: str) -> list[dict]:
        result = []
        normalized = (stock_code or '').split('.', 1)[0]
        for registration_id, data in self.registered_stocks.items():
            if data['stock_code'] != normalized:
                continue
            result.append(self._format_registration(registration_id, data))
        return result

    def get_all_registered_stocks(self) -> list[dict]:
        return [
            self._format_registration(registration_id, data)
            for registration_id, data in self.registered_stocks.items()
        ]

    def _format_registration(self, registration_id: str, data: dict) -> dict:
        display_code = format_stock_code(data['market'], data['stock_code'])
        stock_name = self._stock_name_for(data['market'], data['stock_code'])
        return {
            'id': registration_id,
            'market': data['market'],
            'market_label': MARKET_LABEL[data['market']],
            'stock_code': data['stock_code'],
            'stock_name': stock_name,
            'display_code': display_code,
            'interval': data['interval'],
            'table': data['table'],
            'active': data.get('active', True),
            'created_at': data.get('created_at'),
        }

    def delete_registration(self, registration_id: str) -> bool:
        if registration_id in self.registered_stocks:
            del self.registered_stocks[registration_id]
            db_manager.delete_registration_by_id(registration_id)
            return True
        return False

    def load_registered_stocks(self) -> dict:
        return db_manager.load_registered_stocks()


registration_service = RegistrationService()
