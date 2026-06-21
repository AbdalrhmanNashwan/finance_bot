import os

# Get this from @BotFather on Telegram
BOT_TOKEN = os.getenv("FINANCE_BOT_TOKEN", "8115381297:AAEfdEGz9LzN8ODshgMcNzN_WgLdosrlMOo")

# Your numeric Telegram user ID. Message @userinfobot on Telegram to get it.
# Only this user (and anyone else you add) can use the bot.
ALLOWED_USER_IDS = {
    1898021733,  # <-- replace with your real Telegram user ID
}

DB_PATH = "sqlite+aiosqlite:///finance.db"

# Default currency symbol used in messages (purely cosmetic for v1)
DEFAULT_CURRENCY = "IQD"
