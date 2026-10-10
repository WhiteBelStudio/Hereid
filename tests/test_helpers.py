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



def test_database_migrations_preserve_records(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "bot.sqlite3"))
    main.init_db()
    main.init_db()

    warning_id = main.record("warnings", -100123, 456, 789, "test reason")
    main.log_action(-100123, 456, 789, "warn", "test reason")
    main.init_db()

    with main.db() as con:
        warning = con.execute(
            "SELECT reason,active FROM warnings WHERE id=?", (warning_id,)
        ).fetchone()
        action_count = con.execute(
            "SELECT COUNT(*) AS n FROM actions WHERE chat_id=?", (-100123,)
        ).fetchone()["n"]
        migration_count = con.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations"
        ).fetchone()["n"]

    assert warning["reason"] == "test reason"
    assert warning["active"] == 1
    assert action_count == 1
    assert migration_count >= 1


def test_legacy_actions_table_gets_reason_column_without_data_loss(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(main, "DB_PATH", str(db_path))

    # Simulate a database from an older bot version with existing action history
    # but no reason column.
    with sqlite3.connect(db_path) as con:
        con.execute("""
            CREATE TABLE actions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER,
                moderator_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        con.execute(
            "INSERT INTO actions(chat_id,user_id,moderator_id,action,created_at) VALUES(?,?,?,?,?)",
            (-100555, 456, 789, "legacy_warn", "2026-01-01T00:00:00+00:00"),
        )

    main.init_db()
    main.init_db()  # Migration must be safe to rerun.

    with main.db() as con:
        columns = {row["name"] for row in con.execute("PRAGMA table_info(actions)")}
        rows = con.execute("SELECT * FROM actions WHERE chat_id=?", (-100555,)).fetchall()
        version = con.execute("PRAGMA user_version").fetchone()[0]
        migrations = {
            row["version"] for row in con.execute("SELECT version FROM schema_migrations")
        }

    assert "reason" in columns
    assert len(rows) == 1
    assert rows[0]["action"] == "legacy_warn"
    assert rows[0]["reason"] == ""
    assert version >= 2
    assert 2 in migrations


def test_chat_settings_defaults_and_updates_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "settings.sqlite3"))
    main.init_db()

    first = main.get_chat_settings(-100777)
    assert first["welcome_enabled"] == 0
    assert first["reports_enabled"] == 1

    with main.db() as con:
        con.execute(
            "UPDATE chat_settings SET welcome_enabled=1,welcome_text=? WHERE chat_id=?",
            ("Welcome {name}", -100777),
        )

    second = main.get_chat_settings(-100777)
    assert second["welcome_enabled"] == 1
    assert second["welcome_text"] == "Welcome {name}"



def test_startup_rejects_missing_token_without_starting(monkeypatch):
    import start

    monkeypatch.setattr(start.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    assert start.main() == 2


def test_startup_prepares_database_directory_and_runs_bot(tmp_path, monkeypatch):
    import asyncio
    import start

    monkeypatch.setattr(start.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN_FOR_AUTOMATED_TESTS")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "nested" / "bot.sqlite3"))

    async def fake_main():
        return None

    monkeypatch.setattr(main, "main", fake_main)
    assert start.main() == 0
    assert (tmp_path / "nested").is_dir()
