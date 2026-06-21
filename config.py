import os

BOT_TOKEN = os.environ["FINANCE_BOT_TOKEN"]

ALLOWED_USER_IDS = {
    int(user_id)
    for user_id in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if user_id.strip()
}

DB_PATH = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///finance.db",
)

DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "IQD")