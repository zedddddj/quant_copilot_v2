import os
import datetime

# Chuẩn hóa Múi giờ UTC+7
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

# Quản trị vốn & rủi ro cố định
ACCOUNT_CAPITAL = float(os.getenv("ACCOUNT_CAPITAL", "400.0"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))  # 1% = $4.00
FIXED_RISK_USDT = round(ACCOUNT_CAPITAL * (RISK_PERCENT / 100.0), 2)
DEFAULT_LEVERAGE = 5.0
MAX_MARGIN_CAP = 48.0

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8628182979:AAFW6LidR7R_5cUvMwq7byT-0rxqRZrVA_Q")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7448159853")
ENABLE_TELEGRAM = True

# Tệp lưu trữ trạng thái các Agent
STATE_MACRO_FILE = "macro_state.json"
STATE_RISK_FILE = "portfolio_risk_state.json"
STATE_TRADES_FILE = "active_monitored_trades.json"
