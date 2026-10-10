import os
os.environ.setdefault('BOT_TOKEN', '123456:TEST_TOKEN_FOR_AUTOMATED_TESTS')
os.environ.setdefault('DB_PATH', 'test.sqlite3')

import main
from datetime import timedelta


def test_duration_parser():
    assert main.duration('1м') == timedelta(minutes=1)
    assert main.duration('2ч') == timedelta(hours=2)
    assert main.duration('3д') == timedelta(days=3)
    assert main.duration('0м') is None
    assert main.duration('366д') is None
    assert main.duration('nonsense') is None


def test_command_parser():
    assert main.parse('/ban@heradeLegendRobot 12345 10м') == ('ban', ['12345', '10м'])
    assert main.parse('  МУТ 10м флуд ') == ('мут', ['10м', 'флуд'])
    assert main.parse(' ') == ('', [])


def test_rp_control_message_guard():
    for value in ('/ban 12345', 'мут 10м флуд', 'АДМИН МЕНЮ', 'помощь', 'нужна поддержка'):
        assert main.is_control_message(value)
    assert not main.is_control_message('обнимашки')


def test_role_permissions_are_limited():
    assert 'manage_roles' in main.ROLE_PERMISSIONS['owner']
    assert 'moderate' in main.ROLE_PERMISSIONS['moderator']
    assert 'manage_settings' not in main.ROLE_PERMISSIONS['moderator']
    assert 'moderate' not in main.ROLE_PERMISSIONS['helper']
