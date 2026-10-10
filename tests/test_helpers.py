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


def test_legacy_moderation_tables_get_active_columns_without_data_loss(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "legacy_moderation.sqlite3"
    monkeypatch.setattr(main, "DB_PATH", str(db_path))

    # Older versions may have warnings/reprimands without the active flag.
    with sqlite3.connect(db_path) as con:
        con.execute("""
            CREATE TABLE warnings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE reprimands(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        con.execute(
            "INSERT INTO warnings(chat_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",
            (-100111, 222, 333, "legacy warning", "2026-10-01T00:00:00+00:00"),
        )
        con.execute(
            "INSERT INTO reprimands(chat_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",
            (-100111, 222, 333, "legacy reprimand", "2026-10-01T00:00:00+00:00"),
        )

    main.init_db()
    main.init_db()  # The migration must be idempotent.

    with main.db() as con:
        warning_columns = {row["name"] for row in con.execute("PRAGMA table_info(warnings)")}
        reprimand_columns = {row["name"] for row in con.execute("PRAGMA table_info(reprimands)")}
        warning = con.execute("SELECT reason,active FROM warnings").fetchone()
        reprimand = con.execute("SELECT reason,active FROM reprimands").fetchone()
        version = con.execute("PRAGMA user_version").fetchone()[0]

    assert "active" in warning_columns
    assert "active" in reprimand_columns
    assert (warning["reason"], warning["active"]) == ("legacy warning", 1)
    assert (reprimand["reason"], reprimand["active"]) == ("legacy reprimand", 1)
    assert version >= 3


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



def test_legacy_actions_table_gets_reason_column_without_losing_history(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(main, "DB_PATH", str(db_path))

    # Simulate an older deployed database whose actions table predates reason.
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
            (-100555, 456, 789, "old_action", "2026-10-01T12:00:00+00:00"),
        )

    main.init_db()
    main.log_action(-100555, 999, 789, "new_action", "new reason")

    with main.db() as con:
        columns = {row["name"] for row in con.execute("PRAGMA table_info(actions)")}
        rows = con.execute(
            "SELECT action,reason FROM actions WHERE chat_id=? ORDER BY id",
            (-100555,),
        ).fetchall()
        version = con.execute("PRAGMA user_version").fetchone()[0]

    assert "reason" in columns
    assert [(row["action"], row["reason"]) for row in rows] == [
        ("old_action", ""),
        ("new_action", "new reason"),
    ]
    assert version >= 2

def test_log_action_supports_legacy_required_details_column(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "legacy_actions_details.sqlite3"
    monkeypatch.setattr(main, "DB_PATH", str(db_path))

    # Existing Pterodactyl databases may have a NOT NULL details column.
    with sqlite3.connect(db_path) as con:
        con.execute("""
            CREATE TABLE actions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER,
                moderator_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

    main.init_db()
    main.log_action(-100999, 123, 456, "mute", "test moderation reason")

    with main.db() as con:
        row = con.execute(
            "SELECT action,reason,details FROM actions WHERE chat_id=?",
            (-100999,),
        ).fetchone()

    assert row is not None
    assert row["action"] == "mute"
    assert row["reason"] == "test moderation reason"
    assert row["details"] == "test moderation reason"


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



def test_cancel_pending_operations_only_clears_current_user_and_chat(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "pending.sqlite3"
    monkeypatch.setattr(main, "DB_PATH", str(db_path))
    main.init_db()

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO pending_user_actions(chat_id,admin_id,target_id,action,created_at) VALUES(?,?,?,?,?)",
            (-1001, 11, None, "lookup", "2026-10-11T00:00:00+00:00"),
        )
        con.execute(
            "INSERT INTO pending_user_actions(chat_id,admin_id,target_id,action,created_at) VALUES(?,?,?,?,?)",
            (-1001, 22, None, "lookup", "2026-10-11T00:00:00+00:00"),
        )
        con.execute(
            "INSERT INTO pending_role_changes(chat_id,admin_id,role,created_at) VALUES(?,?,?,?)",
            (-1001, 11, "moderator", "2026-10-11T00:00:00+00:00"),
        )
        con.execute(
            "INSERT INTO pending_rp_edits(chat_id,user_id,action_id,field,created_at) VALUES(?,?,?,?,?)",
            (-1001, 11, 7, "name", "2026-10-11T00:00:00+00:00"),
        )
        con.execute(
            "INSERT INTO pending_panel_inputs(chat_id,user_id,kind,created_at) VALUES(?,?,?,?)",
            (-1001, 11, "rp_create", "2026-10-11T00:00:00+00:00"),
        )
        con.execute(
            "INSERT INTO pending_chat_settings(chat_id,user_id,field,created_at) VALUES(?,?,?,?)",
            (-1001, 11, "welcome_text", "2026-10-11T00:00:00+00:00"),
        )

    main.cancel_pending_operations(-1001, 11)

    with sqlite3.connect(db_path) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
            (-1001, 11),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
            (-1001, 22),
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM pending_role_changes WHERE chat_id=? AND admin_id=?",
            (-1001, 11),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM pending_rp_edits WHERE chat_id=? AND user_id=?",
            (-1001, 11),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM pending_panel_inputs WHERE chat_id=? AND user_id=?",
            (-1001, 11),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM pending_chat_settings WHERE chat_id=? AND user_id=?",
            (-1001, 11),
        ).fetchone()[0] == 0



def test_xp_awards_respect_cooldown_and_are_chat_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "xp.sqlite3"))
    now = [1_800_000_000.0]
    monkeypatch.setattr(main.time, "time", lambda: now[0])
    main.init_db()

    assert main.award_message_xp(-100123, 456, "Test User", "test_user") is True
    assert main.award_message_xp(-100123, 456, "Test User", "test_user") is False
    assert main.get_xp_profile(-100123, 456)["xp"] == 5
    assert main.get_xp_profile(-100123, 456)["eligible_messages"] == 1

    now[0] += main.XP_COOLDOWN_SECONDS + 1
    assert main.award_message_xp(-100123, 456, "Test User", "test_user") is True
    profile = main.get_xp_profile(-100123, 456)
    assert profile["xp"] == 10
    assert profile["eligible_messages"] == 2
    assert profile["display_name"] == "Test User"
    assert profile["username"] == "test_user"

    # XP is separate for each group, so one busy chat cannot affect another.
    assert main.award_message_xp(-100999, 456, "Test User", "test_user") is True
    assert main.get_xp_profile(-100999, 456)["xp"] == 5


def test_xp_schema_migration_is_idempotent_and_preserves_existing_records(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "xp_migration.sqlite3"))
    main.init_db()
    main.award_message_xp(-100123, 456, "Existing User", "")
    main.init_db()

    with main.db() as con:
        row = con.execute(
            "SELECT xp,eligible_messages FROM xp_profiles WHERE chat_id=? AND user_id=?",
            (-100123, 456),
        ).fetchone()
        migration = con.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=5"
        ).fetchone()[0]
        version = con.execute("PRAGMA user_version").fetchone()[0]

    assert row["xp"] == 5
    assert row["eligible_messages"] == 1
    assert migration == 1
    assert version >= 5


def test_xp_leaderboard_has_clean_unframed_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "xp_leaderboard.sqlite3"))
    monkeypatch.setattr(main.time, "time", lambda: 1_800_000_000.0)
    main.init_db()
    main.award_message_xp(-100123, 456, "Alice", "alice")
    main.award_message_xp(-100123, 789, "Bob", "bob")

    text = main.xp_leaderboard_text(-100123)
    assert "🏆" in text
    assert "Alice" in text
    assert "Bob" in text
    assert "XP" in text
    assert "┌" not in text and "┐" not in text
