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
        bio_migration = con.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=6"
        ).fetchone()[0]
        version = con.execute("PRAGMA user_version").fetchone()[0]
        bio = con.execute(
            "SELECT bio FROM xp_profiles WHERE chat_id=? AND user_id=?",
            (-100123, 456),
        ).fetchone()["bio"]

    assert row["xp"] == 5
    assert row["eligible_messages"] == 1
    assert migration == 1
    assert bio_migration == 1
    assert bio == ""
    assert version >= 6


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



def test_xp_level_thresholds_and_progress_bar_values():
    assert main.xp_level(0) == 1
    assert main.xp_level(99) == 1
    assert main.xp_level(100) == 2
    assert main.xp_required_for_level(2) == 100
    assert main.xp_required_for_level(3) == 225
    assert main.xp_required_for_level(4) == 375
    assert main.xp_level(224) == 2
    assert main.xp_level(225) == 3
    assert main.xp_required_for_level(100) == 131_175
    assert main.xp_level(131_174) == 99
    assert main.xp_level(131_175) == 100
    assert main.xp_level_progress(250) == {
        "level": 3,
        "current_xp": 25,
        "needed_xp": 150,
        "next_level_xp": 375,
        "percent": 16,
    }


def test_level_up_announcement_only_on_level_change_and_milestones_are_special():
    assert main.level_up_announcement(95, 99, "Alice") is None
    ordinary = main.level_up_announcement(95, 100, "Alice")
    assert ordinary is not None
    assert "НОВЫЙ УРОВЕНЬ" in ordinary
    assert "2-й уровень" in ordinary

    xp_100 = main.xp_required_for_level(100)
    xp_200 = main.xp_required_for_level(200)
    xp_1100 = main.xp_required_for_level(1100)
    milestone_100 = main.level_up_announcement(xp_100 - 1, xp_100, "Alice")
    milestone_200 = main.level_up_announcement(xp_200 - 1, xp_200, "Bob")
    milestone_1100 = main.level_up_announcement(xp_1100 - 1, xp_1100, "Charlie")
    assert "100-го уровня" in milestone_100
    assert "200-го уровня" in milestone_200
    assert "1100-го уровня" in milestone_1100
    assert milestone_100 != milestone_200
    assert milestone_200 != milestone_1100
    assert "<b>Alice</b>" in milestone_100



def test_xp_rewards_unlock_from_level_and_activity_without_duplicates():
    empty = {"xp": 0, "eligible_messages": 0}
    beginner = {"xp": 100, "eligible_messages": 10}
    rewards = main.get_xp_rewards(beginner)
    unlocked_keys = {reward["key"] for reward in rewards["unlocked"]}
    assert "level_2" in unlocked_keys
    assert "messages_10" in unlocked_keys
    assert rewards["next_level"]["key"] == "level_5"
    assert rewards["next_activity"]["key"] == "messages_50"

    new_rewards = main.newly_unlocked_xp_rewards(empty, beginner)
    assert {reward["key"] for reward in new_rewards} >= {"level_2", "messages_10"}
    assert main.newly_unlocked_xp_rewards(beginner, beginner) == []


def test_xp_rewards_text_and_announcement_are_safe_and_informative():
    profile = {"xp": 225, "eligible_messages": 50, "display_name": "<Alice>", "username": ""}
    text = main.xp_rewards_text(profile)
    assert "ТВОИ НАГРАДЫ" in text
    assert "Первый шаг" in text
    assert "Открыто:" in text
    assert "<Alice>" not in text
    rewards = main.newly_unlocked_xp_rewards(
        {"xp": 220, "eligible_messages": 49},
        {"xp": 225, "eligible_messages": 50},
    )
    announcement = main.xp_rewards_announcement(rewards)
    assert announcement is not None
    assert "НОВАЯ НАГРАДА" in announcement
    assert "/rewards" in announcement


def test_xp_keywords_are_documented_and_excluded_from_xp_farming():
    for keyword in (
        "мой опыт", "мой уровень", "мой xp", "топ опыта", "топ уровней",
        "рейтинг опыта", "лидеры опыта", "награды", "мои награды",
    ):
        assert main.is_control_message(keyword), keyword
    assert "/xp" in main.COMMANDS
    assert "/level" in main.COMMANDS
    assert "/topxp" in main.COMMANDS
    assert "/rewards" in main.COMMANDS
    assert "мой опыт" in main.COMMANDS
    assert "топ опыта" in main.COMMANDS

def test_xp_reward_catalog_boundaries_and_completion():
    catalog = (*main.LEVEL_REWARDS, *main.ACTIVITY_REWARDS)
    keys = [reward["key"] for reward in catalog]

    assert len(catalog) == 14
    assert len(keys) == len(set(keys))

    for reward in main.LEVEL_REWARDS:
        threshold_xp = main.xp_required_for_level(reward["threshold"])
        assert main.xp_reward_is_unlocked(reward, {
            "xp": threshold_xp, "eligible_messages": 0,
        })
        assert not main.xp_reward_is_unlocked(reward, {
            "xp": threshold_xp - 1, "eligible_messages": 0,
        })

    for reward in main.ACTIVITY_REWARDS:
        threshold = reward["threshold"]
        assert main.xp_reward_is_unlocked(reward, {
            "xp": 0, "eligible_messages": threshold,
        })
        assert not main.xp_reward_is_unlocked(reward, {
            "xp": 0, "eligible_messages": threshold - 1,
        })

    completed = main.get_xp_rewards({
        "xp": main.xp_required_for_level(500),
        "eligible_messages": 1000,
    })
    assert len(completed["unlocked"]) == completed["total"] == 14
    assert completed["next_level"] is None
    assert completed["next_activity"] is None



def test_xp_bio_is_persistent_chat_scoped_trimmed_and_clearable(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "xp_bio.sqlite3"))
    main.init_db()

    saved = main.set_xp_bio(-100123, 456, "  Люблю игры и общение  ", "Alice", "alice")
    assert saved == "Люблю игры и общение"
    profile = main.get_xp_profile(-100123, 456)
    assert profile["bio"] == "Люблю игры и общение"
    assert profile["display_name"] == "Alice"

    # Descriptions are isolated by group and are HTML-escaped for Telegram HTML mode.
    assert main.get_xp_profile(-100999, 456)["bio"] == ""
    main.set_xp_bio(-100123, 789, "<b>Не мой профиль</b>")
    assert main.xp_bio_text(main.get_xp_profile(-100123, 789)) == "&lt;b&gt;Не мой профиль&lt;/b&gt;"
    assert main.xp_bio_text({"bio": ""}) == "Пока не заполнено."

    # Editing and clearing never reset the user's XP or activity counters.
    main.award_message_xp(-100123, 456, "Alice", "alice")
    xp_before = main.get_xp_profile(-100123, 456)["xp"]
    main.set_xp_bio(-100123, 456, "Обновлённое описание")
    assert main.get_xp_profile(-100123, 456)["xp"] == xp_before
    main.set_xp_bio(-100123, 456, "")
    assert main.get_xp_profile(-100123, 456)["bio"] == ""
    assert main.get_xp_profile(-100123, 456)["xp"] == xp_before


def test_xp_bio_enforces_character_limit():
    try:
        main.set_xp_bio(-100123, 456, "x" * (main.XP_BIO_MAX_LENGTH + 1))
    except ValueError as exc:
        assert "250" in str(exc)
    else:
        raise AssertionError("Overlong about-me text must be rejected")


def test_legacy_xp_profile_migration_adds_bio_without_losing_records(tmp_path, monkeypatch):
    import sqlite3

    database = tmp_path / "legacy_xp.sqlite3"
    monkeypatch.setattr(main, "DB_PATH", str(database))
    with sqlite3.connect(database) as con:
        con.execute("""
            CREATE TABLE xp_profiles(
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                xp INTEGER NOT NULL DEFAULT 0,
                eligible_messages INTEGER NOT NULL DEFAULT 0,
                last_awarded_at REAL NOT NULL DEFAULT 0,
                display_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(chat_id,user_id)
            )
        """)
        con.execute(
            "INSERT INTO xp_profiles(chat_id,user_id,xp,eligible_messages,last_awarded_at,display_name,username,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (-100123, 456, 175, 35, 1800000000.0, "Existing User", "existing", "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
        )
        con.commit()

    main.init_db()
    profile = main.get_xp_profile(-100123, 456)
    assert profile["xp"] == 175
    assert profile["eligible_messages"] == 35
    assert profile["display_name"] == "Existing User"
    assert profile["bio"] == ""
    with main.db() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] >= 6
        assert con.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=6").fetchone()[0] == 1


def test_rp_action_keywords_are_unique_per_chat_and_normalized(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "rp_keywords.sqlite3"))
    main.init_db()
    assert main.normalize_rp_keywords(" ОБНЯТЬ, обнять ,   ДАЙ   ПЯТЬ ") == [
        "обнять", "дай пять",
    ]

    with main.db() as con:
        first = con.execute(
            "INSERT INTO rp_actions(chat_id,name,keywords,response,created_by,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (-100123, "Обнять", "обнять, обнимашки", "Ответ 1", 1, "2026-10-11"),
        ).lastrowid
        con.execute(
            "INSERT INTO rp_actions(chat_id,name,keywords,response,created_by,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (-100123, "Дать пять", "дай пять, пять", "Ответ 2", 1, "2026-10-11"),
        )

        conflicts = main.find_rp_keyword_conflicts(
            con, -100123, ["ОБНИМАШКИ", "дай пять"]
        )
        assert conflicts == {
            "обнимашки": "Обнять",
            "дай пять": "Дать пять",
        }

        # Editing an action may keep its own keywords; only other actions conflict.
        assert main.find_rp_keyword_conflicts(
            con, -100123, ["обнять", "обнимашки"], exclude_action_id=first
        ) == {}

        # Keyword ownership is isolated by chat.
        assert main.find_rp_keyword_conflicts(
            con, -100999, ["обнять"]
        ) == {}


def test_rp_action_keywords_reject_empty_and_overlong_values():
    import pytest

    with pytest.raises(ValueError):
        main.normalize_rp_keywords(" , , ")
    with pytest.raises(ValueError, match="60"):
        main.normalize_rp_keywords("x" * 61)



def test_reputation_votes_are_unique_changeable_and_chat_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "reputation.sqlite3"))
    now = [1_800_000_000.0]
    monkeypatch.setattr(main.time, "time", lambda: now[0])
    main.init_db()

    assert main.cast_reputation_vote(-1001, 10, 20, 1) == "created"
    assert main.cast_reputation_vote(-1001, 10, 20, 1) == "unchanged"
    assert main.get_reputation(-1001, 20) == {"positive": 1, "negative": 0, "score": 1}

    now[0] += main.REPUTATION_VOTE_COOLDOWN_SECONDS
    assert main.cast_reputation_vote(-1001, 10, 20, -1) == "changed"
    assert main.get_reputation(-1001, 20) == {"positive": 0, "negative": 1, "score": -1}

    # The same users in another group have independent reputation history.
    assert main.cast_reputation_vote(-1002, 10, 20, 1) == "created"
    assert main.get_reputation(-1002, 20) == {"positive": 1, "negative": 0, "score": 1}
    assert main.get_reputation(-1001, 20) == {"positive": 0, "negative": 1, "score": -1}


def test_reputation_rejects_self_votes_and_invalid_values(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "reputation.sqlite3"))
    main.init_db()

    with pytest.raises(ValueError, match="самого себя"):
        main.cast_reputation_vote(-1001, 10, 10, 1)
    with pytest.raises(ValueError, match="Оценка"):
        main.cast_reputation_vote(-1001, 10, 20, 0)

    with main.db() as con:
        count = con.execute("SELECT COUNT(*) FROM reputation_votes").fetchone()[0]
    assert count == 0


def test_reputation_migration_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "reputation-migration.sqlite3"))
    main.init_db()
    main.init_db()

    with main.db() as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        migrations = {
            row["version"] for row in con.execute("SELECT version FROM schema_migrations")
        }
        table_exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reputation_votes'"
        ).fetchone()
        rate_table_exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reputation_rate_limits'"
        ).fetchone()
        fingerprint_table_exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='xp_message_fingerprints'"
        ).fetchone()

    assert version >= 8
    assert 7 in migrations and 8 in migrations
    assert table_exists is not None
    assert rate_table_exists is not None
    assert fingerprint_table_exists is not None


def test_reputation_vote_cooldown_blocks_spam_and_is_chat_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "reputation-rate.sqlite3"))
    now = [1_800_000_000.0]
    monkeypatch.setattr(main.time, "time", lambda: now[0])
    main.init_db()

    assert main.cast_reputation_vote(-1001, 10, 20, 1) == "created"
    assert main.cast_reputation_vote(-1001, 10, 20, 1) == "unchanged"
    assert main.cast_reputation_vote(-1001, 10, 21, 1) == "rate_limited"
    assert main.get_reputation(-1001, 21)["positive"] == 0

    # Rate limit is per group, not global across all chats.
    assert main.cast_reputation_vote(-1002, 10, 21, 1) == "created"
    now[0] += main.REPUTATION_VOTE_COOLDOWN_SECONDS
    assert main.cast_reputation_vote(-1001, 10, 21, 1) == "created"
    # Vote changes are rate-limited too, preventing rapid reputation flipping.
    assert main.cast_reputation_vote(-1001, 10, 20, -1) == "rate_limited"
    now[0] += main.REPUTATION_VOTE_COOLDOWN_SECONDS
    assert main.cast_reputation_vote(-1001, 10, 20, -1) == "changed"


def test_duplicate_xp_message_filter_blocks_repeats_and_is_chat_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "xp-fingerprints.sqlite3"))
    now = [1_800_000_000.0]
    monkeypatch.setattr(main.time, "time", lambda: now[0])
    main.init_db()

    assert main.is_duplicate_xp_message(-1001, 10, "Привет всем") is False
    assert main.is_duplicate_xp_message(-1001, 10, "  ПРИВЕТ   ВСЕМ  ") is True
    assert main.is_duplicate_xp_message(-1001, 11, "Привет всем") is False
    assert main.is_duplicate_xp_message(-1002, 10, "Привет всем") is False

    now[0] += main.XP_DUPLICATE_MESSAGE_WINDOW_SECONDS + 1
    assert main.is_duplicate_xp_message(-1001, 10, "Привет всем") is False


def test_xp_duplicate_filter_prunes_old_fingerprints(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "xp-prune.sqlite3"))
    now = [1_800_000_000.0]
    monkeypatch.setattr(main.time, "time", lambda: now[0])
    main.init_db()

    assert main.is_duplicate_xp_message(-1001, 10, "Original message") is False
    now[0] += main.XP_FINGERPRINT_RETENTION_SECONDS + 1
    assert main.is_duplicate_xp_message(-1001, 10, "A different message") is False
    with main.db() as con:
        count = con.execute("SELECT COUNT(*) FROM xp_message_fingerprints").fetchone()[0]
    assert count == 1



def test_moderation_settings_defaults_and_persisted_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "moderation-settings.sqlite3"))
    main.init_db()

    defaults = main.get_moderation_settings(-100701)
    assert defaults["antispam_enabled"] == 1
    assert defaults["block_links"] == 0
    assert defaults["flood_limit"] == 5
    assert defaults["flood_window_seconds"] == 8
    assert defaults["mute_duration_minutes"] == 5

    with main.db() as con:
        con.execute(
            "UPDATE moderation_settings SET antispam_enabled=0,block_links=1,flood_limit=7,"
            "flood_window_seconds=12,mute_duration_minutes=10 WHERE chat_id=?",
            (-100701,),
        )

    saved = main.get_moderation_settings(-100701)
    assert (saved["antispam_enabled"], saved["block_links"]) == (0, 1)
    assert (saved["flood_limit"], saved["flood_window_seconds"]) == (7, 12)
    assert saved["mute_duration_minutes"] == 10
    assert "выключен" in main.moderation_settings_text(-100701)
    assert "включена" in main.moderation_settings_text(-100701)


def test_chat_settings_text_escapes_html_and_keyboard_respects_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "chat-settings-ui.sqlite3"))
    main.init_db()

    main.get_chat_settings(-100702)  # Ensure the chat-scoped settings row exists before updating it.
    with main.db() as con:
        con.execute(
            "UPDATE chat_settings SET welcome_enabled=1,welcome_text=?,goodbye_text=?,rules_text=? "
            "WHERE chat_id=?",
            ("<b>Привет</b>", "<i>Пока</i>", "<script>rules</script>", -100702),
        )

    rendered = main.chat_settings_text(-100702)
    assert "&lt;b&gt;Привет&lt;/b&gt;" in rendered
    assert "&lt;i&gt;Пока&lt;/i&gt;" in rendered
    assert "&lt;script&gt;rules&lt;/script&gt;" in rendered

    manager_keyboard = main.chat_settings_keyboard(-100702, True)
    viewer_keyboard = main.chat_settings_keyboard(-100702, False)
    manager_callbacks = [button.callback_data for row in manager_keyboard.inline_keyboard for button in row]
    viewer_callbacks = [button.callback_data for row in viewer_keyboard.inline_keyboard for button in row]
    assert "chatset:toggle:welcome" in manager_callbacks
    assert "chatset:edit:rules_text" in manager_callbacks
    assert not any(value.startswith("chatset:toggle:") for value in viewer_callbacks)
    assert not any(value.startswith("chatset:edit:") for value in viewer_callbacks)


def test_rp_catalog_and_item_view_are_chat_scoped_and_escape_html(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "rp-catalog.sqlite3"))
    main.init_db()

    with main.db() as con:
        con.execute(
            "INSERT INTO rp_actions(chat_id,name,keywords,response,created_by,created_at,is_enabled) "
            "VALUES(?,?,?,?,?,?,?)",
            (-100703, "<обнять>", "обнять, hugs", "<b>обнял</b>", 10, "2026-10-11T00:00:00+00:00", 1),
        )
        con.execute(
            "INSERT INTO rp_actions(chat_id,name,keywords,response,created_by,created_at,is_enabled) "
            "VALUES(?,?,?,?,?,?,?)",
            (-100704, "Other chat", "hello", "other response", 10, "2026-10-11T00:00:00+00:00", 1),
        )
        action_id = con.execute(
            "SELECT id FROM rp_actions WHERE chat_id=?", (-100703,)
        ).fetchone()["id"]

    catalog = main.rp_catalog(-100703)
    assert "&lt;обнять&gt;" in catalog
    assert "&lt;b&gt;обнял&lt;/b&gt;" in catalog
    assert "Other chat" not in catalog

    item_text, enabled = main.rp_item_view(-100703, action_id)
    assert enabled is True
    assert "&lt;обнять&gt;" in item_text
    assert "&lt;b&gt;обнял&lt;/b&gt;" in item_text
    assert main.rp_item_view(-100704, action_id) == (None, None)


def test_action_log_pagination_is_chat_scoped_and_escapes_untrusted_text(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "action-log.sqlite3"))
    main.init_db()

    for index in range(5):
        main.log_action(
            -100705, 900 + index, 800, "test_action", f"reason {index}",
        )
    main.log_action(-100706, 999, 888, "foreign_action", "foreign reason")
    with main.db() as con:
        con.execute(
            "UPDATE actions SET action=?,reason=? WHERE chat_id=? AND user_id=?",
            ("<script>", "<b>unsafe</b>", -100705, 904),
        )

    first_page, _ = main.action_log_page(-100705, page=0, page_size=2)
    last_page, _ = main.action_log_page(-100705, page=99, page_size=2)
    assert "страница 1/3" in first_page
    assert "страница 3/3" in last_page
    assert "foreign_action" not in first_page
    assert "&lt;script&gt;" in first_page
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in first_page


def test_statistics_text_counts_only_selected_chat_records(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "statistics.sqlite3"))
    main.init_db()
    main.log_action(-100707, 21, 31, "mute", "test")
    main.record("warnings", -100707, 21, 31, "warning")
    main.log_action(-100708, 22, 32, "ban", "other chat")

    stats = main.statistics_text(-100707)
    assert "Всего записей в журнале: <b>1</b>" in stats
    assert "Активных предупреждений: <b>1</b>" in stats
    assert "mute" in stats
    assert "foreign" not in stats
    assert "ban" not in stats
