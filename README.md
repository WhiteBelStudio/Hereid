# heradeLegend — Telegram moderation bot

Telegram bot with Russian keyword commands, moderation actions, staff reprimand records, SQLite persistence, and inline help/admin menus.

## Pterodactyl launch

1. Create a Python server (Python 3.12 recommended).
2. Upload/clone this repository into the server directory.
3. In the server **Startup** settings, set the command to:
   `python start.py`
4. Add the environment variable `BOT_TOKEN` with the token from @BotFather.
5. Start the server and check the console for errors.

The bot creates `heradelegend.db` automatically. Keep this file on persistent server storage.

## Telegram setup

1. Add the bot to your group.
2. Promote it to administrator and grant **Ban users** and **Restrict members**. Delete messages is optional.
3. For plain keyword messages such as «помощь» or «админ меню», open @BotFather, use `/setprivacy`, select your bot, then choose **Disable**. If privacy stays enabled, Telegram may not deliver ordinary group messages to the bot.
4. Test commands by replying to a user's message.

## Commands

Prefer replying to the target user's message:

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

Duration formats: `30м`, `2ч`, `7д`.

## Notes

- For reliable targeting, reply to the target user's message or use their numeric Telegram ID. Telegram does not always allow bots to resolve an arbitrary @username.
- Commands are restricted to Telegram chat administrators, and the bot needs the corresponding Telegram permissions.
- This version moderates Telegram groups. YouTube comment moderation requires a separate YouTube Data API integration and channel authorization.
- Never commit the real bot token to GitHub. If exposed, revoke it via @BotFather.
