import pytest
from unittest.mock import MagicMock, patch

from app.services.registration_service import RegistrationService


class TestRegistrationService:
    @pytest.fixture
    def mock_deps(self):
        with patch('app.services.registration_service.detect_market') as mock_detect, \
             patch('app.services.registration_service.db_manager') as mock_db:
            mock_db.save_registration = MagicMock()
            mock_db.load_registered_stocks.return_value = {}
            mock_db.table_exists.return_value = False
            mock_db.create_stock_table = MagicMock()
            mock_db.find_stock_name_by_code.return_value = '平安银行'
            yield mock_detect, mock_db

    @pytest.fixture
    def registration_service(self, mock_deps):
        service = RegistrationService()
        service.registered_stocks = {}
        return service

    def test_registration_id_format(self, registration_service):
        assert registration_service.get_registration_id('a', '000001', '5min') == 'A_000001.SZ_5min'
        assert registration_service.get_registration_id('hk', '00700', '5min') == 'HK_00700.HK_5min'
        assert registration_service.get_registration_id('us', 'AAPL', '5min') == 'US_AAPL.US_5min'

    def test_register_single_market(self, registration_service, mock_deps):
        mock_detect, mock_db = mock_deps
        mock_detect.return_value = [('a', '000001')]

        result = registration_service.register_stock('000001')

        assert result['success'] is True
        assert result['message'] == '股票已注册'
        assert len(result['registration_ids']) == 1
        assert mock_db.save_registration.call_count == 1
        registration_id = result['registration_ids'][0]
        assert registration_id == 'A_000001.SZ_5min'
        assert registration_id in registration_service.registered_stocks
        mock_db.create_stock_table.assert_called_once_with('a', 'A_000001.SZ_5min')

    def test_register_does_not_fetch_data(self, registration_service, mock_deps):
        mock_detect, _ = mock_deps
        mock_detect.return_value = [('a', '000001')]

        result = registration_service.register_stock('000001')

        assert result['success'] is True
        assert result['registration_ids'] == ['A_000001.SZ_5min']

    def test_register_multi_market(self, registration_service, mock_deps):
        mock_detect, mock_db = mock_deps
        mock_detect.return_value = [('hk', '09988'), ('us', 'BABA')]

        result = registration_service.register_stock('阿里巴巴')

        assert result['success'] is True
        assert len(result['registration_ids']) == 2
        assert mock_db.save_registration.call_count == 2
        hk_ids = [x for x in result['registration_ids'] if x.startswith('HK_')]
        us_ids = [x for x in result['registration_ids'] if x.startswith('US_')]
        assert len(hk_ids) == 1
        assert len(us_ids) == 1
        assert all(x.endswith('_5min') for x in result['registration_ids'])

    def test_register_duplicate(self, registration_service, mock_deps):
        mock_detect, _ = mock_deps
        mock_detect.return_value = [('a', '000001')]

        registration_service.register_stock('000001')
        result = registration_service.register_stock('000001')
        assert result['message'] == '股票已存在'

    def test_get_stock_registrations(self, registration_service, mock_deps):
        mock_detect, _ = mock_deps
        mock_detect.return_value = [('a', '000001')]
        registration_service.register_stock('000001')

        registrations = registration_service.get_stock_registrations('000001')
        assert len(registrations) == 1
        assert registrations[0]['interval'] == '5min'
        assert registrations[0]['stock_name'] == '平安银行'

    def test_get_all_registered_stocks_includes_stock_name(self, registration_service, mock_deps):
        mock_detect, _ = mock_deps
        mock_detect.return_value = [('a', '000001')]
        registration_service.register_stock('000001')

        registered = registration_service.get_all_registered_stocks()
        assert len(registered) == 1
        assert registered[0]['display_code'] == '000001.SZ'
        assert registered[0]['stock_name'] == '平安银行'

    def test_delete_registration(self, registration_service, mock_deps):
        mock_detect, _ = mock_deps
        mock_detect.return_value = [('a', '000001')]

        registration_service.register_stock('000001')
        assert registration_service.delete_registration('A_000001.SZ_5min') is True
        assert registration_service.delete_registration('A_000001.SZ_5min') is False

    def test_table_name_matches_registration_id(self, registration_service, mock_deps):
        mock_detect, _ = mock_deps
        mock_detect.return_value = [('a', '000001')]

        result = registration_service.register_stock('000001')
        for registration_id in result['registration_ids']:
            data = registration_service.registered_stocks[registration_id]
            assert data['table'] == registration_id

    def test_multi_market_partial(self, registration_service, mock_deps):
        mock_detect, _ = mock_deps
        mock_detect.return_value = [('hk', '09988'), ('us', 'BABA')]
        r1 = registration_service.register_stock('阿里巴巴')
        assert len(r1['registration_ids']) == 2
        r2 = registration_service.register_stock('阿里巴巴')
        assert r2['message'] == '股票已存在'
