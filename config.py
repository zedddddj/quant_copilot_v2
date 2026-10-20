import os
import datetime

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

ACCOUNT_CAPITAL = float(os.getenv("ACCOUNT_CAPITAL", "400.0"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))
FIXED_RISK_USDT = round(ACCOUNT_CAPITAL * (RISK_PERCENT / 100.0), 2)
MAX_MARGIN_CAP = 48.0
DEFAULT_LEVERAGE = 5.0

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8628182979:AAFW6LidR7R_5cUvMwq7byT-0rxqRZrVA_Q")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7448159853")
ENABLE_TELEGRAM = True

STATE_MACRO_FILE = "macro_state.json"
STATE_RISK_FILE = "portfolio_risk_state.json"
STATE_TRADES_FILE = "active_monitored_trades.json"
