# heradeLegend — Telegram moderation bot

Telegram group moderation bot with Russian commands, role-based administration, editable RP actions, per-chat settings, anti-spam controls, action history, and SQLite persistence.

## Requirements

- Python 3.12 (recommended; CI tests against Python 3.12).
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- A Pterodactyl Python egg/container with outbound HTTPS access to `api.telegram.org:443`, or a working proxy.
- Persistent writable server storage for `heradelegend.db`.

## Pterodactyl deployment

1. Create a Python server using Python 3.12.
2. Upload/clone the repository into the server's working directory. Ensure `main.py`, `start.py`, and `requirements.txt` are in the same directory.
3. Set the Pterodactyl Startup command to:
   ```bash
   python start.py
   ```
4. In Startup variables, set `BOT_TOKEN` to the real token from @BotFather. Never put the token into source code, logs, screenshots, or GitHub.
5. Set `OWNER_TELEGRAM_ID` to the numeric Telegram ID of the person who owns the internal admin panel. Do not use a username here.
6. Keep `DB_PATH=heradelegend.db` unless you have a specific persistent path. The database file and its directory must be writable and must survive container restarts.
7. Optional settings:
   - `LOG_LEVEL=INFO`
   - `TELEGRAM_REQUEST_TIMEOUT=60`
   - `TELEGRAM_PROXY=` — leave blank when direct outbound access works; otherwise use a reachable SOCKS5/HTTP proxy URL supported by aiogram/aiohttp-socks. Keep proxy credentials private.
8. Start the server and inspect the console. A successful connection logs the bot username and ID. If startup reports a network error, verify outbound access and the proxy from the hosting provider; do not repeatedly rotate tokens for a network issue.
9. Restart once and confirm the bot reconnects and existing settings/actions remain in the SQLite database. Back up the database before upgrades.

The startup script installs/checks requirements, validates the presence of `BOT_TOKEN`, prepares the database directory, and then starts the bot. It does not print the bot token or proxy URL. Do not delete `heradelegend.db` to troubleshoot.

## Telegram group setup

1. Add the bot to the group and promote it to administrator.
2. Grant only the permissions needed: **Ban users**, **Restrict members**, and **Delete messages** if link/flood cleanup is desired.
3. If the bot needs to react to ordinary group text (RP keywords and plain-text panel triggers), use @BotFather `/setprivacy`, select the bot, then disable privacy. Re-add the bot to the group if Telegram requires it for the privacy change to take effect.
4. Test in a non-critical group first. Verify the bot cannot restrict the group creator or administrators.
5. Configure `OWNER_TELEGRAM_ID` before using the internal role system. Telegram group admin status and internal panel roles are related but are not identical.

## Commands

Prefer replying to the target user's message when possible:

- `бан 7д спам` — ban for seven days.
- `бан спам` — permanent ban.
- `разбан TELEGRAM_ID` — unban by numeric Telegram ID.
- `мут 30м флуд` — mute for 30 minutes.
- `размут` — remove mute.
- `варн флуд` — record a warning.
- `варны` — list active warnings.
- `снять варн` — deactivate the newest warning.
- `выговор неактив` — record a staff reprimand.
- `выговоры` — list active reprimands.
- `снять выговор` — deactivate the newest reprimand.
- `админ меню` — open the admin panel.
- `помощь` / `команды` — show help.
- `/xp` — show your XP in the current group.
- `/topxp` — show the group XP leaderboard.

XP rules: eligible text messages award **5 XP** at most once every **60 seconds** per user and group. Commands, bots, private chats, and recognized control/help messages do not award XP. XP profiles are stored per group in SQLite; schema migrations preserve existing records. Leveling becomes progressively harder: level 2 requires 100 XP, level 3 requires 225 total XP, level 4 requires 375 total XP, and each next level costs **25 XP more** than the previous level. `/xp` shows the level, progress bar, XP needed for the next level, and your «О себе» description. Use `/setbio ТЕКСТ` to create or edit a description of up to **250 characters**, `/mybio` to view it, and `/setbio очистить` to remove it. Descriptions are stored separately for each group, trimmed, and HTML-escaped before display. Use `/rewards` (or write «награды») to see unlocked decorative rewards and the nearest next reward. Level rewards unlock at levels 2, 5, 10, 25, 50, 100, 250, and 500; activity rewards unlock at 10, 50, 100, 250, 500, and 1000 XP-eligible messages. Rewards are derived from saved XP/message counters, require no XP spending, and do not need a separate claim, so they remain consistent after restarts. Level-ups are announced in the group; every 100th level (100, 200, 300, and onward) receives a special milestone celebration, with custom messages for levels 100–1000 and generated milestone-specific wording for higher levels.

Duration formats: `30м`, `2ч`, `7д`.

## Automated checks

GitHub Actions runs on pushes to `main`, pull requests, and manual dispatch. It installs dependencies on Python 3.12, compiles the application and test module, then runs pytest.

Run the same checks locally:

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m py_compile main.py start.py tests/test_helpers.py
BOT_TOKEN=123456:TEST_TOKEN_FOR_AUTOMATED_TESTS python -m pytest -q
```

The tests use a placeholder token and do not call Telegram. A passing CI run does not replace a real deployment smoke test.

## Troubleshooting

- **`BOT_TOKEN is missing`** — set the environment variable in Pterodactyl Startup; for local development, copy `.env.example` to `.env` and fill it in.
- **`Startup connection ... failed` / `Host unreachable`** — verify the host's outbound network access and `TELEGRAM_PROXY`. Do not share proxy credentials in logs or screenshots.
- **Database cannot be opened / permission denied** — verify `DB_PATH`, writable directory permissions, and persistent storage.
- **Bot starts but ignores ordinary text** — check BotFather privacy mode, group membership, and permissions.
- **Commands fail to moderate** — confirm the bot is an administrator with the relevant Telegram permissions and test against a regular member.
- **Never commit a real token**. If a token has been exposed, revoke it through @BotFather and replace the server variable.

## Limitations and production sign-off

This project is designed for Telegram groups. YouTube comment moderation needs a separate YouTube Data API integration and channel authorization. Live Telegram connectivity, real group permissions, proxy reachability, Pterodactyl restart behavior, and persistence across a real container restart must be verified in the actual hosting account before calling the deployment production-ready.
