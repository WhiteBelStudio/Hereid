from profile import format_participant_profile


def test_participant_profile_shows_identity_level_xp_and_bio():
    result = format_participant_profile(
        display_name="Alice <Admin>",
        username="alice",
        user_id=123456789,
        profile={"xp": 175, "eligible_messages": 35, "bio": "Игры & общение"},
        progress={"level": 2, "current_xp": 75, "needed_xp": 125, "percent": 60},
    )

    assert "ПРОФИЛЬ УЧАСТНИКА" in result
    assert "Alice &lt;Admin&gt;" in result
    assert "@alice" in result
    assert "123456789" in result
    assert "Уровень: <b>2</b>" in result
    assert "175 XP" in result
    assert "75/125 XP" in result
    assert "35" in result
    assert "Игры &amp; общение" in result


def test_participant_profile_handles_missing_username_and_empty_bio():
    result = format_participant_profile(
        display_name="Участник",
        username="",
        user_id=42,
        profile={"xp": 0, "eligible_messages": 0, "bio": ""},
        progress={"level": 1, "current_xp": 0, "needed_xp": 100, "percent": 0},
    )

    assert "не указан" in result
    assert "Пока не заполнено." in result
    assert "42" in result
    assert "🟦" not in result
    assert len(result) < 2000


def test_participant_profile_escapes_bio_html():
    result = format_participant_profile(
        display_name="Tester",
        username="tester",
        user_id=7,
        profile={"xp": 10, "eligible_messages": 2, "bio": "<b>admin</b> & text"},
        progress={"level": 1, "current_xp": 10, "needed_xp": 100, "percent": 10},
    )

    assert "&lt;b&gt;admin&lt;/b&gt; &amp; text" in result
    assert "<b>admin</b>" not in result
